"""Idempotent demo seed: reviewed notifications so the student home is not empty
(two open, one closed, plus one prior-year IBPS with cutoff for last-year QA).

Safe to re-run. Only upserts DEMO-* rows (and the demo source) — real crawl
notifications are never modified. Dates are computed from *today* so the open
windows stay open this month and closed ones stay in the last 6 months.
"""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db import SessionLocal, init_db
from app.models.notification import DocumentVersion, Notification, Recruitment, Source

DEMO_SOURCE_KEY = "demo"
DEMO_NUMBERS = ("DEMO-OPEN-1", "DEMO-OPEN-2", "DEMO-CLOSED-1", "DEMO-PRIOR-IBPS")


def _dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _as_dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _windows(today: date | None = None) -> dict:
    today = today or date.today()
    last = monthrange(today.year, today.month)[1]
    end_open = date(today.year, today.month, last)
    if end_open < today:
        end_open = today
    start_a = today - timedelta(days=5)
    start_b = today - timedelta(days=3)
    end_closed = today - timedelta(days=90)
    start_closed = end_closed - timedelta(days=21)
    # Prior IBPS cycle: closed ~12 months before the current open IBPS demo.
    end_prior = today - timedelta(days=365)
    start_prior = end_prior - timedelta(days=21)
    as_on = date(today.year, 7, 1)
    as_on_prior = date(today.year - 1, 7, 1)
    return {
        "today": today,
        "year": today.year,
        "prior_year": today.year - 1,
        "as_on_dmy": _dmy(as_on),
        "as_on_prior_dmy": _dmy(as_on_prior),
        "open_a": (start_a, end_open),
        "open_b": (start_b, end_open),
        "closed": (start_closed, end_closed),
        "prior_ibps": (start_prior, end_prior),
    }


def _has_real_notifications(db) -> bool:
    rows = db.scalars(select(Notification)).all()
    return any(not (n.notification_number or "").startswith("DEMO-") for n in rows)


def notification_count(db=None) -> int:
    own = db is None
    if own:
        init_db()
        db = SessionLocal()
    try:
        return int(db.scalar(select(func.count()).select_from(Notification)) or 0)
    finally:
        if own:
            db.close()


def _get_or_create_source(db) -> Source:
    src = db.scalar(select(Source).where(Source.key == DEMO_SOURCE_KEY))
    if src:
        src.name = "Demo reviewed jobs"
        src.listing_url = "https://example.gov/demo/"
        src.adapter = "demo"
        src.enabled = True
        return src
    src = Source(
        key=DEMO_SOURCE_KEY,
        name="Demo reviewed jobs",
        listing_url="https://example.gov/demo/",
        adapter="demo",
        enabled=True,
    )
    db.add(src)
    db.flush()
    return src


def _specs(w: dict) -> list[dict]:
    as_on = w["as_on_dmy"]
    as_on_prior = w["as_on_prior_dmy"]
    year = w["year"]
    prior_year = w["prior_year"]
    a_start, a_end = w["open_a"]
    b_start, b_end = w["open_b"]
    c_start, c_end = w["closed"]
    p_start, p_end = w["prior_ibps"]

    open1_det = {
        "notification_number": "DEMO-OPEN-1",
        "notification_date": _dmy(a_start),
        "post_title": "Group-II Services",
        "application_start": _dmy(a_start),
        "application_end": _dmy(a_end),
        "age_policy": {
            "as_on_date": as_on,
            "min_age": 18,
            "max_age": 44,
            "relaxations": [
                {
                    "category": "SC/ST/BC",
                    "years": 5,
                    "page": 1,
                    "evidence": "SC/ST/BCs: 5 years relaxation",
                },
                {
                    "category": "PWD",
                    "years": 10,
                    "page": 1,
                    "evidence": "Persons with Disability: 10 years relaxation",
                },
            ],
            "confidence": "good",
        },
    }
    open1_llm = {
        "department": "Telangana Public Service Commission",
        "qualifications": [{"level": "degree"}],
        "age_rules": [
            {"min_age": 18, "max_age": 44, "category": "OC", "as_on_date": as_on},
        ],
    }

    open2_det = {
        "notification_number": "DEMO-OPEN-2",
        "notification_date": _dmy(b_start),
        "post_title": "Office Assistant (Multipurpose)",
        "application_start": _dmy(b_start),
        "application_end": _dmy(b_end),
        "age_policy": {
            "as_on_date": as_on,
            "min_age": None,
            "max_age": None,
            "relaxations": [],
            "confidence": "placeholder",
        },
    }
    open2_llm = {
        # Honest: this cutoff and the category-cap table come from the
        # reviewed demo fixture (IBPS/RRB-style), not a live crawl.
        "source": "official_notification",
        "department": "Institute of Banking Personnel Selection",
        "qualifications": [{"level": "degree"}],
        "age_rules": [
            {"min_age": 18, "max_age": 30, "category": "UR", "as_on_date": as_on},
            {"min_age": 18, "max_age": 33, "category": "OBC", "as_on_date": as_on},
            {"min_age": 18, "max_age": 35, "category": "SC/ST", "as_on_date": as_on},
        ],
        "cutoff": {"oc": 72.5, "obc": 69, "sc": 62},
    }

    closed_det = {
        "notification_number": "DEMO-CLOSED-1",
        "notification_date": _dmy(c_start),
        "post_title": "Group-I Services",
        "application_start": _dmy(c_start),
        "application_end": _dmy(c_end),
        "age_policy": {
            "as_on_date": as_on,
            "min_age": 18,
            "max_age": 42,
            "relaxations": [
                {
                    "category": "SC/ST/BC",
                    "years": 5,
                    "page": 1,
                    "evidence": "SC/ST/BCs: 5 years relaxation",
                },
                {
                    "category": "PWD",
                    "years": 10,
                    "page": 1,
                    "evidence": "Persons with Disability: 10 years relaxation",
                },
            ],
            "confidence": "good",
        },
    }
    closed_llm = {
        "department": "Andhra Pradesh Public Service Commission",
        "qualifications": [{"level": "degree"}],
        "age_rules": [
            {"min_age": 18, "max_age": 42, "category": "OC", "as_on_date": as_on},
        ],
    }


    # Prior-year IBPS (closed) — published cutoff so DEMO-OPEN-2 can show last-year.
    # Honest demo fixture only; not a live crawl mark.
    prior_det = {
        "notification_number": "DEMO-PRIOR-IBPS",
        "notification_date": _dmy(p_start),
        "post_title": "Office Assistant (Multipurpose)",
        "application_start": _dmy(p_start),
        "application_end": _dmy(p_end),
        "age_policy": {
            "as_on_date": as_on_prior,
            "min_age": None,
            "max_age": None,
            "relaxations": [],
            "confidence": "placeholder",
        },
    }
    prior_llm = {
        "source": "official_notification",
        "department": "Institute of Banking Personnel Selection",
        "qualifications": [{"level": "degree"}],
        "age_rules": [
            {"min_age": 18, "max_age": 30, "category": "UR", "as_on_date": as_on_prior},
            {"min_age": 18, "max_age": 33, "category": "OBC", "as_on_date": as_on_prior},
            {"min_age": 18, "max_age": 35, "category": "SC/ST", "as_on_date": as_on_prior},
        ],
        "cutoff": {"oc": 68.25, "obc": 64.5, "sc": 58},
    }

    return [
        {
            "number": "DEMO-OPEN-1",
            "title": "TGPSC Group-II Services — General Recruitment",
            "department": "Telangana Public Service Commission",
            "url": "https://example.gov/demo/DEMO-OPEN-1",
            "start": a_start,
            "end": a_end,
            "year": year,
            "extraction": {"deterministic": open1_det, "llm": open1_llm},
        },
        {
            "number": "DEMO-OPEN-2",
            "title": "IBPS RRB CRP — Office Assistant (Multipurpose)",
            "department": "Institute of Banking Personnel Selection",
            "url": "https://example.gov/demo/DEMO-OPEN-2",
            "start": b_start,
            "end": b_end,
            "year": year,
            # Same base key as prior year so prior_cutoff matching is conservative.
            "recruitment_key_base": "DEMO-OPEN-2",
            "extraction": {"deterministic": open2_det, "llm": open2_llm},
        },
        {
            "number": "DEMO-CLOSED-1",
            "title": "APPSC Group-I Services — Combined Notification",
            "department": "Andhra Pradesh Public Service Commission",
            "url": "https://example.gov/demo/DEMO-CLOSED-1",
            "start": c_start,
            "end": c_end,
            "year": year,
            "extraction": {"deterministic": closed_det, "llm": closed_llm},
        },
        {
            "number": "DEMO-PRIOR-IBPS",
            "title": "IBPS RRB CRP — Office Assistant (Multipurpose)",
            "department": "Institute of Banking Personnel Selection",
            "url": "https://example.gov/demo/DEMO-PRIOR-IBPS",
            "start": p_start,
            "end": p_end,
            "year": prior_year,
            "recruitment_key_base": "DEMO-OPEN-2",
            "extraction": {"deterministic": prior_det, "llm": prior_llm},
        },
    ]


def _upsert_job(db, source: Source, spec: dict) -> dict:
    base = spec.get("recruitment_key_base") or spec["number"]
    key = f"{DEMO_SOURCE_KEY}:{base}:{spec['year']}"
    rec = db.scalar(select(Recruitment).where(Recruitment.recruitment_key == key))
    if rec is None:
        rec = db.scalar(
            select(Recruitment).where(Recruitment.notification_number == spec["number"])
        )
    if rec is None:
        rec = Recruitment(
            source_id=source.id,
            recruitment_key=key,
            year=spec["year"],
            notification_number=spec["number"],
            title=spec["title"],
            department=spec["department"],
            status="extracted",
        )
        db.add(rec)
        db.flush()
    else:
        rec.recruitment_key = key
        rec.source_id = source.id
        rec.year = spec["year"]
        rec.notification_number = spec["number"]
        rec.title = spec["title"]
        rec.department = spec["department"]
        rec.status = "extracted"

    n = db.scalar(
        select(Notification).where(
            or_(
                Notification.notification_number == spec["number"],
                Notification.official_url == spec["url"],
            )
        )
    )
    if n is None:
        n = Notification(
            source_id=source.id,
            recruitment_id=rec.id,
            notification_number=spec["number"],
            year=spec["year"],
            title=spec["title"],
            document_type="notification",
            official_url=spec["url"],
            application_start=_as_dt(spec["start"]),
            application_end=_as_dt(spec["end"]),
            status="extracted",
            raw_json=json.dumps(spec["extraction"], ensure_ascii=False),
        )
        db.add(n)
        db.flush()
    else:
        n.source_id = source.id
        n.recruitment_id = rec.id
        n.notification_number = spec["number"]
        n.year = spec["year"]
        n.title = spec["title"]
        n.document_type = "notification"
        n.official_url = spec["url"]
        n.application_start = _as_dt(spec["start"])
        n.application_end = _as_dt(spec["end"])
        n.status = "extracted"
        n.raw_json = json.dumps(spec["extraction"], ensure_ascii=False)

    sha = hashlib.sha256(f"demo:{spec['number']}".encode("utf-8")).hexdigest()
    v = db.scalar(select(DocumentVersion).where(DocumentVersion.notification_id == n.id))
    extraction_json = json.dumps(spec["extraction"], ensure_ascii=False)
    verdict = json.dumps(
        {"verdict": "verified", "reasons": ["demo reviewed fixture"]},
        ensure_ascii=False,
    )
    if v is None:
        v = DocumentVersion(
            notification_id=n.id,
            recruitment_id=rec.id,
            sha256=sha,
            local_pdf_path=f"data/raw/demo_{spec['number']}.pdf",
            text_path=None,
            page_count=1,
            extraction_json=extraction_json,
            extraction_status="extracted",
            classification_label="recruitment",
            classification_reasons=json.dumps(["demo seed"]),
            review_status="approved",
            review_verdict_json=verdict,
        )
        db.add(v)
        db.flush()
    else:
        v.recruitment_id = rec.id
        v.sha256 = sha
        v.local_pdf_path = f"data/raw/demo_{spec['number']}.pdf"
        v.extraction_json = extraction_json
        v.extraction_status = "extracted"
        v.classification_label = "recruitment"
        v.review_status = "approved"
        v.review_verdict_json = verdict
    return {
        "notification_id": n.id,
        "notification_number": spec["number"],
        "document_version_id": v.id,
    }


def seed(db=None, today: date | None = None) -> dict:
    """Insert or refresh the demo reviewed jobs.

    Returns {"status": "ok"|"skipped", ...}. Never touches non-DEMO rows.
    """
    own = db is None
    if own:
        init_db()
        db = SessionLocal()
    try:
        # Upsert DEMO rows only. Real crawl notifications are left untouched
        # (_upsert_job keys exclusively on DEMO numbers / demo URLs).
        source = _get_or_create_source(db)
        rows = [_upsert_job(db, source, spec) for spec in _specs(_windows(today))]
        db.commit()
        return {"status": "ok", "jobs": rows}
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed reviewed demo jobs.")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Only seed when the DB is missing or has zero notifications.",
    )
    args = parser.parse_args(argv)
    os.chdir(_ROOT)
    if args.if_empty and notification_count() > 0:
        print("seed_demo: skipped (notifications already present)")
        return 0
    result = seed()
    nums = ", ".join(j["notification_number"] for j in result["jobs"])
    print(f"seed_demo: reviewed demo jobs ready ({nums})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
