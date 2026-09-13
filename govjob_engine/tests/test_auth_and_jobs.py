"""Admin HTTP Basic auth + jobs window classification."""
from __future__ import annotations

import base64
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.jobs import (
    classify_window,
    extract_cutoff,
    in_closed_window,
    merge_application_dates,
    parse_flexible_date,
)


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client():
    # Import app after fixture setup so monkeypatches to settings are visible
    # (settings is a singleton; we patch attributes on it in each test).
    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_health_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_admin_401_without_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_pass", "secret-for-test")
    monkeypatch.setattr(settings, "admin_user", "admin")
    assert client.get("/admin/status").status_code == 401
    assert client.get("/review/pending").status_code == 401
    assert client.get("/leads").status_code == 401
    assert client.post("/crawl/tgpsc").status_code == 401


def test_admin_401_when_pass_empty(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_pass", "")
    monkeypatch.setattr(settings, "admin_user", "admin")
    # Even with attempted credentials, empty ADMIN_PASS fails closed.
    r = client.get("/admin/status", headers=_basic("admin", ""))
    assert r.status_code == 401
    r2 = client.get("/admin/status", headers=_basic("admin", "anything"))
    assert r2.status_code == 401
    assert client.get("/admin/status").status_code == 401


def test_admin_200_with_correct_basic(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_pass", "correct-horse")
    monkeypatch.setattr(settings, "admin_user", "admin")
    r = client.get("/admin/status", headers=_basic("admin", "correct-horse"))
    assert r.status_code == 200
    assert "scheduler" in r.json()
    # Wrong password still 401
    assert client.get("/admin/status", headers=_basic("admin", "wrong")).status_code == 401


def test_jobs_endpoint_public(client):
    r = client.get("/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    r2 = client.get("/jobs?window=open")
    assert r2.status_code == 200
    r3 = client.get("/jobs?window=closed")
    assert r3.status_code == 200
    bad = client.get("/jobs?window=nope")
    assert bad.status_code == 400


# --- Pure helpers -------------------------------------------------------

def test_classify_window_open_end_null():
    today = date(2026, 9, 13)
    assert classify_window(date(2026, 9, 1), None, today) == "open"


def test_classify_window_open_end_future():
    today = date(2026, 9, 13)
    assert classify_window(date(2026, 9, 1), date(2026, 9, 30), today) == "open"


def test_classify_window_closed_within_180():
    today = date(2026, 9, 13)
    end = today - timedelta(days=30)
    assert classify_window(date(2026, 6, 1), end, today) == "closed"
    assert in_closed_window(end, today) is True


def test_classify_window_closed_older_than_180():
    today = date(2026, 9, 13)
    end = today - timedelta(days=200)
    assert classify_window(None, end, today) == "closed"
    assert in_closed_window(end, today) is False


def test_classify_window_unknown_no_dates():
    today = date(2026, 9, 13)
    assert classify_window(None, None, today) == "unknown"


def test_parse_and_merge_dates_prefer_complete():
    today = date(2026, 9, 13)
    # Deterministic has both; notification has only start → prefer det
    s, e = merge_application_dates("01/09/2026", None, "01/09/2026", "30/09/2026")
    assert s == date(2026, 9, 1)
    assert e == date(2026, 9, 30)
    assert classify_window(s, e, today) == "open"


def test_extract_cutoff_honesty():
    assert extract_cutoff({}, {}) is None
    assert extract_cutoff({"cutoff": 120}, {}) == 120
    assert extract_cutoff({}, {"cut_off": "50%"}) == "50%"
    assert extract_cutoff({"cutoffs": {"gen": 90}}, {}) == {"gen": 90}
    # Do not invent
    assert extract_cutoff({"age_policy": {"max_age": 30}}, {"qualifications": []}) is None


def test_parse_flexible_date_dmy():
    assert parse_flexible_date("15/08/2026") == date(2026, 8, 15)
    assert parse_flexible_date(None) is None
    assert parse_flexible_date("") is None
