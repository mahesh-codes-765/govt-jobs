"""Age relaxation summary, prior_cutoff matching, and UI honesty."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.services.jobs import (
    build_age_relaxation_summary,
    find_prior_cutoff,
    list_jobs,
    titles_similar,
)


def _load_seed_mod():
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seed_mod = _load_seed_mod()


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_age_relaxation_summary_from_policy():
    policy = {
        "as_on_date": "01/07/2026",
        "min_age": 18,
        "max_age": 44,
        "relaxations": [
            {"category": "SC/ST/BC", "years": 5},
            {"category": "PWD", "years": 10},
        ],
    }
    summary = build_age_relaxation_summary(policy, None)
    assert summary is not None
    assert summary["as_on_date"] == "01/07/2026"
    assert summary["min_age"] == 18
    assert summary["max_age"] == 44
    cats = {e["category"]: e for e in summary["entries"]}
    assert cats["SC/ST/BC"]["years"] == 5
    assert cats["PWD"]["years"] == 10


def test_age_relaxation_from_llm_fallback_caps():
    fb = {
        "as_on_date": "01/07/2026",
        "min_age": 18,
        "max_age": 30,
        "category_caps": {"UR": 30, "OBC": 33, "SC/ST": 35},
        "relaxations": [],
        "source": "llm_only",
    }
    summary = build_age_relaxation_summary(None, fb)
    assert summary is not None
    assert summary["source"] == "llm_only"
    caps = {e["category"]: e["max_age"] for e in summary["entries"]}
    assert caps["UR"] == 30
    assert caps["OBC"] == 33


def test_age_relaxation_none_when_missing():
    assert build_age_relaxation_summary(None, None) is None
    assert build_age_relaxation_summary({"relaxations": []}, None) is None


def test_prior_cutoff_null_without_match():
    current = {
        "id": 1,
        "source": "tgpsc",
        "year": 2026,
        "title": "Group-II Services",
        "department": "TGPSC",
        "recruitment_key": "tgpsc:g2:2026",
        "application_end": "2026-09-30",
    }
    candidates = [
        {
            "id": 2,
            "source": "appsc",  # different source
            "year": 2025,
            "title": "Group-II Services",
            "department": "APPSC",
            "recruitment_key": "appsc:g2:2025",
            "application_end": "2025-09-01",
            "cutoff": {"oc": 90},
        }
    ]
    assert find_prior_cutoff(current, candidates) is None


def test_prior_cutoff_matches_year_minus_one_same_key():
    current = {
        "id": 10,
        "source": "demo",
        "year": 2026,
        "title": "IBPS RRB CRP — Office Assistant (Multipurpose)",
        "department": "Institute of Banking Personnel Selection",
        "recruitment_key": "demo:DEMO-OPEN-2:2026",
        "application_end": "2026-09-30",
    }
    prior_cut = {"oc": 68.25, "obc": 64.5, "sc": 58}
    candidates = [
        {
            "id": 9,
            "source": "demo",
            "year": 2025,
            "title": "IBPS RRB CRP — Office Assistant (Multipurpose)",
            "department": "Institute of Banking Personnel Selection",
            "recruitment_key": "demo:DEMO-OPEN-2:2025",
            "application_end": "2025-09-01",
            "cutoff": prior_cut,
        }
    ]
    found = find_prior_cutoff(current, candidates)
    assert found is not None
    assert found["year"] == 2025
    assert found["notification_id"] == 9
    assert found["cutoff"] == prior_cut


def test_titles_similar_conservative():
    assert titles_similar(
        "IBPS RRB CRP — Office Assistant (Multipurpose)",
        "IBPS RRB CRP — Office Assistant (Multipurpose)",
    )
    assert not titles_similar("Group-II Services", "Office Assistant")


def test_seed_age_relaxation_and_prior_cutoff():
    result = seed_mod.seed()
    assert result["status"] == "ok"
    init_db()
    db = SessionLocal()
    try:
        rows = list_jobs(db, window="all")
        by_num = {r["notification_number"]: r for r in rows}
        assert "DEMO-OPEN-1" in by_num
        ar = by_num["DEMO-OPEN-1"]["age_relaxation"]
        assert ar is not None
        assert ar["min_age"] == 18
        assert ar["max_age"] == 44
        cats = {e["category"] for e in ar["entries"]}
        assert "SC/ST/BC" in cats
        assert "PWD" in cats

        # DEMO-OPEN-1 has no prior with cutoff
        assert by_num["DEMO-OPEN-1"]["prior_cutoff"] is None
        assert by_num["DEMO-CLOSED-1"]["prior_cutoff"] is None

        # IBPS pair: current open has prior_cutoff from DEMO-PRIOR-IBPS
        open2 = by_num["DEMO-OPEN-2"]
        assert open2["cutoff"] == {"oc": 72.5, "obc": 69, "sc": 62}
        prior = open2["prior_cutoff"]
        assert prior is not None
        assert prior["cutoff"] == {"oc": 68.25, "obc": 64.5, "sc": 58}
        assert prior["year"] == open2["year"] - 1
    finally:
        db.close()


def test_eligibility_endpoint_includes_age_relaxation(client):
    seed_mod.seed()
    r = client.get("/eligibility/notifications")
    assert r.status_code == 200
    rows = r.json()
    open1 = next(x for x in rows if x.get("notification_number") == "DEMO-OPEN-1")
    assert open1.get("age_relaxation") is not None
    assert open1["age_relaxation"]["min_age"] == 18
    open2 = next(x for x in rows if x.get("notification_number") == "DEMO-OPEN-2")
    assert open2.get("prior_cutoff") is not None


def test_ui_never_fakes_numeric_cutoff():
    html = (Path(__file__).resolve().parents[1] / "webapp" / "eligibility-checker.html").read_text()
    # Honest missing-copy strings must be present.
    assert "Last year cutoff not published yet." in html
    assert "Age relaxation not extracted — check the official PDF." in html
    # No hardcoded demo cutoff numbers as JS defaults / placeholders.
    assert "72.5" not in html
    assert "68.25" not in html
    # No invented default like cutoff: 50 or similar in script defaults.
    assert not re.search(r"cutoff\s*[:=]\s*\d+", html, re.I)
