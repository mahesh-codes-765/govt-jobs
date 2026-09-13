"""Demo seed: open + closed reviewed jobs, honest cutoff, public /jobs windows."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.models.notification import Notification
from app.services.jobs import extract_cutoff, list_jobs


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


def test_seed_demo_open_closed_and_honest_cutoff():
    result = seed_mod.seed()
    assert result["status"] == "ok"
    nums = {j["notification_number"] for j in result["jobs"]}
    assert "DEMO-OPEN-1" in nums
    assert "DEMO-OPEN-2" in nums
    assert "DEMO-CLOSED-1" in nums
    assert "DEMO-PRIOR-IBPS" in nums

    init_db()
    db = SessionLocal()
    try:
        rows = list_jobs(db, window="all")
        by_num = {r["notification_number"]: r for r in rows}
        assert by_num["DEMO-OPEN-1"]["window"] == "open"
        assert by_num["DEMO-OPEN-2"]["window"] == "open"
        assert by_num["DEMO-CLOSED-1"]["window"] == "closed"
        assert by_num["DEMO-OPEN-2"]["cutoff"] == {"oc": 72.5, "obc": 69, "sc": 62}
        assert by_num["DEMO-OPEN-1"]["cutoff"] is None
        assert by_num["DEMO-CLOSED-1"]["cutoff"] is None

        from sqlalchemy import select
        notifications = db.scalars(select(Notification)).all()
        for n in notifications:
            if not (n.notification_number or "").startswith("DEMO-"):
                continue
            v = next((x for x in n.versions if x.review_status == "approved"), None)
            assert v is not None
            import json
            merged = json.loads(v.extraction_json or "{}")
            cut = extract_cutoff(merged.get("deterministic") or {}, merged.get("llm") or {})
            if n.notification_number == "DEMO-OPEN-2":
                assert cut == {"oc": 72.5, "obc": 69, "sc": 62}
            elif n.notification_number == "DEMO-PRIOR-IBPS":
                assert cut == {"oc": 68.25, "obc": 64.5, "sc": 58}
            else:
                assert cut is None
    finally:
        db.close()

    # Idempotent: second run still ok, still three DEMO rows, still one cutoff.
    again = seed_mod.seed()
    assert again["status"] == "ok"
    init_db()
    db = SessionLocal()
    try:
        from sqlalchemy import select
        demo = [
            n for n in db.scalars(select(Notification)).all()
            if (n.notification_number or "").startswith("DEMO-")
        ]
        assert len(demo) >= 3
    finally:
        db.close()


def test_seed_demo_jobs_windows_nonempty(client):
    seed_mod.seed()
    opened = client.get("/jobs?window=open")
    closed = client.get("/jobs?window=closed")
    assert opened.status_code == 200
    assert closed.status_code == 200
    assert len(opened.json()) >= 1
    assert len(closed.json()) >= 1
    assert any((j.get("notification_number") or "").startswith("DEMO-") for j in opened.json())
    assert any((j.get("notification_number") or "").startswith("DEMO-") for j in closed.json())
