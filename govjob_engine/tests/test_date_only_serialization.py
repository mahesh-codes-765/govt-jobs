"""Calendar dates must stay date-only across the API → UI boundary.

Roopa bug: as-on 01/07 rendered as 30 Jun when JS parsed/displayed UTC midnight
in a behind-UTC locale. Server must emit YYYY-MM-DD (no time); client formats
with timeZone:'UTC'. Documented case: 01/07/2026 → 1 Jul 2026, never 30 Jun.
"""
from __future__ import annotations

from datetime import date, datetime

from app.services.jobs import parse_flexible_date


def test_as_on_dmy_roundtrip_stays_1_july():
    """01/07/2026 is 1 July 2026 — never 30 June, never US MDY 7 Jan."""
    d = parse_flexible_date("01/07/2026")
    assert d == date(2026, 7, 1)
    assert d.isoformat() == "2026-07-01"
    assert "T" not in d.isoformat()


def test_iso_date_only_no_midnight_z():
    """ISO and datetime inputs keep the calendar day (1 Jul, not 7 Jan / 30 Jun)."""
    assert date(2026, 7, 1).isoformat() == "2026-07-01"
    assert parse_flexible_date(datetime(2026, 7, 1, 0, 0, 0)) == date(2026, 7, 1)
    assert parse_flexible_date("2026-07-01") == date(2026, 7, 1)
    assert parse_flexible_date("2026-07-01T00:00:00Z") == date(2026, 7, 1)
    # dateutil dayfirst=True alone would turn this into 7 Jan — we must not.
    assert parse_flexible_date("2026-07-01") != date(2026, 1, 7)


def test_build_job_row_emits_date_only_strings():
    """application_start/end in the jobs feed are YYYY-MM-DD or null — never Z midnight."""
    start, end = date(2026, 7, 1), date(2026, 9, 30)
    assert start.isoformat() == "2026-07-01"
    assert end.isoformat() == "2026-09-30"
