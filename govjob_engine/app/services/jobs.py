"""Student jobs feed: open this month / closed last 6 months / dates unknown.

Never invents dates or cutoffs. Classification is a pure helper so tests
can cover edge cases without a live DB.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from dateutil import parser as date_parser
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import DocumentVersion, Notification, Source
from app.services import age_fallback


def parse_flexible_date(value: Any) -> date | None:
    """Parse datetime/date/str into a date. Returns None if unparseable — never guesses."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Prefer DD/MM/YYYY (common in Indian notifications) when unambiguous.
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # If day > 12, must be DMY; if month > 12, invalid for MDY so try DMY.
        if d > 12 or mo <= 12:
            try:
                return date(y, mo, d)
            except ValueError:
                pass
    try:
        dt = date_parser.parse(s, dayfirst=True, fuzzy=False)
        return dt.date()
    except (ValueError, OverflowError, TypeError):
        return None


def merge_application_dates(
    notif_start: Any,
    notif_end: Any,
    det_start: Any,
    det_end: Any,
) -> tuple[date | None, date | None]:
    """Prefer the more complete of notification columns vs deterministic extraction."""
    ns, ne = parse_flexible_date(notif_start), parse_flexible_date(notif_end)
    ds, de = parse_flexible_date(det_start), parse_flexible_date(det_end)
    notif_score = (1 if ns else 0) + (1 if ne else 0)
    det_score = (1 if ds else 0) + (1 if de else 0)
    if det_score > notif_score:
        start, end = ds, de
    elif notif_score > det_score:
        start, end = ns, ne
    else:
        # Equal completeness: fill gaps from either side.
        start = ns or ds
        end = ne or de
        # If both sides have a value for the same field, prefer notification column
        # (crawler-set) then fall back — already handled by `or` for gaps; for
        # equal both-present, keep notification.
        if ns and ds:
            start = ns
        if ne and de:
            end = ne
    return start, end


def overlaps_calendar_month(start: date | None, end: date | None, today: date) -> bool:
    """True if [start, end] overlaps the calendar month containing today."""
    month_start = today.replace(day=1)
    if today.month == 12:
        month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    # Open-ended ranges: treat missing start as -inf, missing end as +inf for overlap only.
    range_start = start or date.min
    range_end = end or date.max
    return range_start <= month_end and range_end >= month_start


def classify_window(
    start: date | None,
    end: date | None,
    today: date | None = None,
) -> str:
    """Return 'open' | 'closed' | 'unknown'.

    open:   application still open as of today (end is null or >= today).
            Preferable when the window overlaps the current calendar month,
            but still 'open' if end >= today even outside the month.
    closed: end < today AND end >= today - 180 days.
    unknown: no usable end (and not clearly open with a start-only?), or
             closed more than 180 days ago — caller filters by window param.
    """
    today = today or date.today()
    if end is None and start is None:
        return "unknown"
    if end is None:
        # Still open (no close date known) — treat as open if we at least have a start,
        # or if dates are partial. Spec: "application_end is null or >= today" => open.
        return "open"
    if end >= today:
        return "open"
    cutoff = today - timedelta(days=180)
    if end >= cutoff:
        return "closed"
    # Closed too long ago — still classify as closed for honesty, but the
    # list endpoint excludes these from the closed window filter via age check
    # (same condition). Returning 'closed' here keeps the helper pure; the
    # feed applies the 180-day bound when filtering.
    return "closed"


def in_closed_window(end: date | None, today: date | None = None) -> bool:
    today = today or date.today()
    if end is None:
        return False
    return end < today and end >= (today - timedelta(days=180))


def extract_cutoff(deterministic: dict | None, llm: dict | None) -> Any:
    """Return cutoff value if present under known keys; else None. Never fabricates."""
    for obj in (deterministic or {}, llm or {}):
        if not isinstance(obj, dict):
            continue
        for key in ("cutoff", "cut_off", "cutoffs"):
            if key in obj and obj[key] is not None and obj[key] != "":
                return obj[key]
    return None


def _approved_version(notification: Notification) -> DocumentVersion | None:
    approved = [v for v in (notification.versions or []) if v.review_status == "approved"]
    if not approved:
        return None
    return max(approved, key=lambda v: v.id)


def _best_extraction(notification: Notification) -> tuple[DocumentVersion | None, dict, dict]:
    """Prefer approved version's extraction; else latest version for dates only."""
    approved = _approved_version(notification)
    if approved:
        merged = json.loads(approved.extraction_json or "{}")
        return approved, merged.get("deterministic") or {}, merged.get("llm") or {}
    versions = list(notification.versions or [])
    if not versions:
        return None, {}, {}
    latest = max(versions, key=lambda v: v.id)
    merged = json.loads(latest.extraction_json or "{}")
    return latest, merged.get("deterministic") or {}, merged.get("llm") or {}


def build_job_row(notification: Notification, today: date | None = None) -> dict | None:
    """Build one jobs-feed row, or None if the notification should be excluded entirely.

    Inclusion rule: approved eligibility doc OR has parseable application dates.
    """
    today = today or date.today()
    version, det, llm = _best_extraction(notification)
    approved = version is not None and version.review_status == "approved"
    start, end = merge_application_dates(
        notification.application_start,
        notification.application_end,
        det.get("application_start"),
        det.get("application_end"),
    )
    has_dates = start is not None or end is not None
    if not approved and not has_dates:
        return None

    window = classify_window(start, end, today)
    # Narrow closed to last-180-days for the feed's window label consistency.
    if window == "closed" and not in_closed_window(end, today):
        # Older than 6 months — only surface under window=all as closed-but-old?
        # Spec: closed last 6 months filter; for all, still include with window=closed
        # would be misleading. Mark as unknown for feed filtering of "closed".
        # Keep honest: application is closed, but outside product window.
        # We'll still set window="closed" and let list_jobs filter by in_closed_window.
        pass

    row: dict[str, Any] = {
        "id": notification.id,
        "source": notification.source.key if notification.source else None,
        "title": notification.title,
        "notification_number": notification.notification_number,
        "department": (
            notification.recruitment.department
            if notification.recruitment and notification.recruitment.department
            else (llm.get("department") if approved else None)
        ),
        "official_url": notification.official_url,
        "application_start": start.isoformat() if start else None,
        "application_end": end.isoformat() if end else None,
        "window": "unknown" if (start is None and end is None) else (
            "open" if window == "open" else (
                "closed" if in_closed_window(end, today) else "unknown"
            )
        ),
        "eligibility_ready": approved,
        "review_status": version.review_status if version else None,
    }

    if approved:
        row["age_policy"] = det.get("age_policy")
        row["age_rules_llm"] = llm.get("age_rules")
        row["age_policy_llm_fallback"] = age_fallback.build_fallback(llm.get("age_rules"))
        row["qualifications"] = llm.get("qualifications")
        row["district_rules"] = llm.get("district_rules")
        cutoff = extract_cutoff(det, llm)
        if cutoff is not None:
            row["cutoff"] = cutoff
        else:
            row["cutoff"] = None
    else:
        row["age_policy"] = None
        row["age_policy_llm_fallback"] = None
        row["qualifications"] = None
        row["cutoff"] = None

    return row


def list_jobs(
    db: Session,
    window: str = "all",
    source: str | None = None,
    today: date | None = None,
) -> list[dict]:
    today = today or date.today()
    window = (window or "all").lower()
    if window not in ("open", "closed", "all"):
        raise ValueError("window must be open|closed|all")

    q = select(Notification).order_by(Notification.id.desc())
    if source:
        q = q.join(Source).where(Source.key == source)
    notifications = db.scalars(q).unique().all()

    out: list[dict] = []
    for n in notifications:
        row = build_job_row(n, today=today)
        if row is None:
            continue
        w = row["window"]
        if window == "open":
            if w != "open":
                continue
            # Prefer overlap with current month, but still include all open.
            # Spec: "preferably ones whose window overlaps" — we include all open;
            # sort preferred ones first below.
        elif window == "closed":
            if w != "closed":
                continue
        else:  # all — include open, closed (6mo), and dates_unknown
            pass
        out.append(row)

    if window == "open":
        # Prefer month-overlapping first, then by application_end ascending (soonest first).
        def open_key(r: dict):
            s = parse_flexible_date(r.get("application_start"))
            e = parse_flexible_date(r.get("application_end"))
            preferred = overlaps_calendar_month(s, e, today)
            end_ord = e.toordinal() if e else 10**9
            return (0 if preferred else 1, end_ord)

        out.sort(key=open_key)
    elif window == "closed":
        out.sort(key=lambda r: r.get("application_end") or "", reverse=True)
    else:
        # all: open first, then closed, then unknown
        order = {"open": 0, "closed": 1, "unknown": 2}
        out.sort(key=lambda r: (order.get(r["window"], 9), -(r["id"] or 0)))

    return out
