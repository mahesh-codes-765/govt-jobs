import json
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings

# Tiny persistent ledger for API spend, split by component (llm extraction
# vs. web-search discovery — different cost profiles, different caps). One
# JSON file, keyed by calendar month (UTC), so a restart never forgets
# what's already been spent this month. Intentionally not a DB table: this
# must be readable/writable even if the DB is locked, and it's the one
# thing that must never silently reset to zero.

CAPS = {
    "llm": lambda: settings.llm_monthly_usd_cap,
    "web_discovery": lambda: settings.web_discovery_monthly_usd_cap,
}

def _path() -> Path:
    p = Path(settings.llm_spend_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")

def _load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

def _month_bucket(data: dict) -> dict:
    """Old ledgers stored a bare float per month (llm-only). Upgrade
    transparently on read so old data isn't lost or misread."""
    raw = data.get(_month_key(), {})
    if isinstance(raw, (int, float)):
        return {"llm": float(raw)}
    return raw

def month_spend_usd(component: str = "llm") -> float:
    return _month_bucket(_load()).get(component, 0.0)

def remaining_budget_usd(component: str = "llm") -> float:
    cap = CAPS[component]()
    return max(0.0, cap - month_spend_usd(component))

def would_exceed_budget(estimated_usd: float, component: str = "llm") -> bool:
    cap = CAPS[component]()
    return (month_spend_usd(component) + estimated_usd) > cap

def record_spend(usd: float, component: str = "llm") -> float:
    """Add usd to this month's ledger for the given component. Returns
    the new month total for that component."""
    data = _load()
    key = _month_key()
    bucket = _month_bucket(data)
    bucket[component] = bucket.get(component, 0.0) + usd
    data[key] = bucket
    _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return bucket[component]
