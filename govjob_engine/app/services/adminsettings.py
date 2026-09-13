import json
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings
from app.services.web_discovery import DEFAULT_QUERIES

# Runtime-editable operator settings — separate from .env (which needs a
# restart). The admin page reads/writes these live so Mahi can change the
# crawl schedule or flip the scheduler on/off without touching a config
# file or restarting the server.

_PATH = Path(__file__).resolve().parents[2] / "data" / "admin_settings.json"

DEFAULTS = {
    # Fixed site adapters (TGPSC, APPSC, ...) — reliable, re-crawlable listings.
    "scheduler_enabled": False,
    "crawl_interval_seconds": settings.crawl_poll_interval_seconds,
    "crawl_sources": ["tgpsc", "appsc", "rrb_secunderabad", "ibps", "drdo", "isro"],
    "crawl_year": datetime.now(timezone.utc).year,  # discover() finds nothing with year=None and all_years=False
    "crawl_all_years": False,
    # Web-search discovery — broader net, run less often (costs more per run).
    "web_discovery_scheduler_enabled": False,
    "web_discovery_interval_seconds": 21600.0,  # 6 hours
    "web_discovery_queries": DEFAULT_QUERIES,
}


def load() -> dict:
    if not _PATH.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save(patch: dict) -> dict:
    current = load()
    current.update({k: v for k, v in patch.items() if k in DEFAULTS})
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current
