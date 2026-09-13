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
from sqlalchemy.orm import Session, selectinload

from app.models.notification import DocumentVersion, Notification, Source
from app.services import age_fallback


def parse_flexible_date(value: Any) -> date | None:
    """Parse datetime/date/str into a date. Returns None if unparseable — never guesses.

    Calendar dates are date-only. ISO YYYY-MM-DD is handled before dayfirst
    parsing so "2026-07-01" stays 1 Jul (dateutil dayfirst would wrongly yield 7 Jan).
    """
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
    # ISO date or datetime (what build_job_row / APIs emit) — never dayfirst these.
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
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




def build_age_relaxation_summary(
    age_policy: dict | None,
    age_policy_llm_fallback: dict | None,
) -> dict | None:
    """Structured age-relaxation table for the student UI.

    Prefer deterministic age_policy; fall back to age_policy_llm_fallback.
    Returns None when nothing usable was extracted (UI shows honest missing copy).
    entries: list of {category, years?, max_age?, min_age?} — never invents.
    """
    policy = age_policy if isinstance(age_policy, dict) else None
    fb = age_policy_llm_fallback if isinstance(age_policy_llm_fallback, dict) else None

    # Prefer deterministic when it has min/max or relaxations.
    use = None
    if policy:
        has_range = policy.get("min_age") is not None or policy.get("max_age") is not None
        has_rel = bool(policy.get("relaxations"))
        has_caps = bool(policy.get("category_caps"))
        if has_range or has_rel or has_caps:
            use = policy
    if use is None and fb:
        has_range = fb.get("min_age") is not None or fb.get("max_age") is not None
        has_rel = bool(fb.get("relaxations"))
        has_caps = bool(fb.get("category_caps"))
        if has_range or has_rel or has_caps:
            use = fb
    if use is None:
        return None

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for r in use.get("relaxations") or []:
        if not isinstance(r, dict):
            continue
        cat = r.get("category")
        if not cat:
            continue
        key = f"rel:{cat}"
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"category": cat}
        if r.get("years") is not None:
            item["years"] = r["years"]
        if r.get("max_age") is not None:
            item["max_age"] = r["max_age"]
        if r.get("min_age") is not None:
            item["min_age"] = r["min_age"]
        entries.append(item)

    caps = use.get("category_caps") or {}
    if isinstance(caps, dict):
        for cat, max_age in caps.items():
            if not cat or max_age is None:
                continue
            key = f"cap:{cat}"
            if key in seen or f"rel:{cat}" in seen:
                # Still add max_age onto existing entry if only years was set.
                for e in entries:
                    if e.get("category") == cat and "max_age" not in e:
                        e["max_age"] = max_age
                continue
            seen.add(key)
            entries.append({"category": cat, "max_age": max_age})

    return {
        "as_on_date": use.get("as_on_date"),
        "min_age": use.get("min_age"),
        "max_age": use.get("max_age"),
        "entries": entries,
        "source": use.get("source") or ("deterministic" if use is policy else "llm_only"),
    }


def _normalize_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    raw = re.sub(r"[^a-z0-9\s]+", " ", text.lower())
    stop = {
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "by",
        "services", "service", "recruitment", "notification", "combined",
        "general", "post", "posts", "vacancy", "vacancies", "exam",
        "examination", "online", "application", "applications",
    }
    return {t for t in raw.split() if len(t) > 2 and t not in stop}


def titles_similar(a: str | None, b: str | None) -> bool:
    """Conservative title overlap: share >=2 significant tokens, or one contains the other."""
    if not a or not b:
        return False
    ta, tb = a.strip().lower(), b.strip().lower()
    if ta == tb:
        return True
    if ta in tb or tb in ta:
        return len(ta) >= 12 and len(tb) >= 12
    sa, sb = _normalize_tokens(a), _normalize_tokens(b)
    if not sa or not sb:
        return False
    overlap = sa & sb
    return len(overlap) >= 2


def recruitment_keys_related(a: str | None, b: str | None) -> bool:
    """Same key ignoring trailing :YEAR suffix (e.g. demo:DEMO-OPEN-2:2026)."""
    if not a or not b:
        return False
    if a == b:
        return True

    def base(k: str) -> str:
        parts = k.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4:
            return parts[0]
        return k

    return base(a) == base(b)


def _approx_year_prior(
    current_year: int | None,
    prior_year: int | None,
    current_end: date | None,
    prior_end: date | None,
) -> bool:
    """True if prior is year-1 or closed ~12 months earlier (±2 months)."""
    if current_year is not None and prior_year is not None:
        if prior_year == current_year - 1:
            return True
    if current_end is not None and prior_end is not None:
        delta = (current_end - prior_end).days
        if 300 <= delta <= 430:  # ~10–14 months
            return True
    return False


def find_prior_cutoff(
    current: dict,
    candidates: list[dict],
) -> dict | None:
    """Find a conservative prior-year cutoff match among other approved jobs.

    Requires: same source, year-1 or ~12mo earlier close, AND
    (related recruitment_key OR similar title OR same non-empty department
    with title overlap). Never invents marks — prior must already have cutoff.
    """
    src = current.get("source")
    if not src:
        return None
    cur_id = current.get("id")
    cur_year = current.get("year")
    cur_end = parse_flexible_date(current.get("application_end"))
    cur_title = current.get("title")
    cur_dept = (current.get("department") or "").strip() or None
    cur_key = current.get("recruitment_key")

    best: dict | None = None
    for c in candidates:
        if c.get("id") == cur_id:
            continue
        if c.get("source") != src:
            continue
        if c.get("cutoff") is None or c.get("cutoff") == "":
            continue
        if not _approx_year_prior(
            cur_year, c.get("year"),
            cur_end, parse_flexible_date(c.get("application_end")),
        ):
            continue

        related = recruitment_keys_related(cur_key, c.get("recruitment_key"))
        title_ok = titles_similar(cur_title, c.get("title"))
        # Conservative: related recruitment_key OR similar title.
        # Same department alone is too loose (many unrelated posts share a board).
        if not (related or title_ok):
            continue

        cand = {
            "year": c.get("year"),
            "notification_id": c.get("id"),
            "title": c.get("title"),
            "cutoff": c.get("cutoff"),
        }
        # Prefer exact year-1 over approximate window if both exist.
        if best is None:
            best = cand
        elif cur_year and cand.get("year") == cur_year - 1:
            best = cand
    return best


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

    row["year"] = notification.year or (
        notification.recruitment.year if notification.recruitment else None
    )
    row["recruitment_key"] = (
        notification.recruitment.recruitment_key if notification.recruitment else None
    )

    if approved:
        row["age_policy"] = det.get("age_policy")
        row["age_rules_llm"] = llm.get("age_rules")
        row["age_policy_llm_fallback"] = age_fallback.build_fallback(llm.get("age_rules"))
        row["qualifications"] = llm.get("qualifications")
        row["district_rules"] = llm.get("district_rules")
        cutoff = extract_cutoff(det, llm)
        row["cutoff"] = cutoff if cutoff is not None else None
        row["age_relaxation"] = build_age_relaxation_summary(
            row["age_policy"], row["age_policy_llm_fallback"]
        )
    else:
        row["age_policy"] = None
        row["age_rules_llm"] = None
        row["age_policy_llm_fallback"] = None
        row["qualifications"] = None
        row["district_rules"] = None
        row["cutoff"] = None
        row["age_relaxation"] = None

    # Filled in by list_jobs once all candidates are known.
    row["prior_cutoff"] = None
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

    q = select(Notification).options(
        selectinload(Notification.versions),
        selectinload(Notification.source),
        selectinload(Notification.recruitment),
    ).order_by(Notification.id.desc())
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

    # Prior-year cutoff: match against the full approved set (not just this window).
    # Candidates need a real cutoff already extracted — never fabricate.
    prior_pool = [r for r in out if r.get("eligibility_ready") and r.get("cutoff") is not None]
    # Also include approved jobs outside the filtered window so year-1 closed
    # priors remain visible when browsing open jobs.
    if window != "all":
        all_rows: list[dict] = []
        for n in notifications:
            row = build_job_row(n, today=today)
            if row is None:
                continue
            all_rows.append(row)
        prior_pool = [
            r for r in all_rows
            if r.get("eligibility_ready") and r.get("cutoff") is not None
        ]

    for r in out:
        if not r.get("eligibility_ready"):
            r["prior_cutoff"] = None
            continue
        r["prior_cutoff"] = find_prior_cutoff(r, prior_pool)

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
