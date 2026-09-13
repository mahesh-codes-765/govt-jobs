import threading
from datetime import datetime, timezone

from app.services import adminsettings, eventlog

_lock = threading.Lock()
_state = {
    "thread_started": False,
    "is_running": False,           # a crawl (one or more sources) is in progress right now
    "last_run_started": None,
    "last_run_finished": None,
    "last_run_summary": None,      # {"tgpsc": {...}, "appsc": {...}}
    "last_run_error": None,
    "next_scheduled_at": None,
}

_wd_lock = threading.Lock()
_wd_state = {
    "thread_started": False,
    "is_running": False,
    "last_run_started": None,
    "last_run_finished": None,
    "last_run_summary": None,      # {"candidates": N, "processed": N, ...}
    "last_run_error": None,
    "next_scheduled_at": None,
}

_stop_event = threading.Event()


def _summarize(report: dict) -> dict:
    results = report.get("results", [])
    return {
        "discovered": report.get("discovered", 0),
        "processed": sum(1 for r in results if r.get("status") == "processed"),
        "unchanged": sum(1 for r in results if r.get("status") == "unchanged"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
    }


def _push_telegram_safely():
    try:
        from app.services.telegram_review import push_pending_queue
        push_pending_queue()
    except Exception:
        eventlog.emit("telegram_push_failed", "Pushing pending review cards to Telegram failed after crawl.", level="warning")


# --- Fixed site adapters (TGPSC, APPSC, ...) ----------------------------

def _run_crawl_once(sources: list[str], year: int | None, all_years: bool, triggered_by: str):
    from app.services.crawler import crawl  # deferred: avoids import cycle at module load

    with _lock:
        if _state["is_running"]:
            eventlog.emit("crawl_skipped", "A crawl is already in progress — skipped.", level="warning")
            return
        _state["is_running"] = True
        _state["last_run_started"] = datetime.now(timezone.utc).isoformat()

    summary = {}
    error = None
    for source in sources:
        try:
            report = crawl(source, year=year, all_years=all_years, triggered_by=triggered_by)
            summary[source] = _summarize(report)
        except Exception as e:
            summary[source] = {"error": str(e)}
            error = str(e)
    with _lock:
        _state["last_run_summary"] = summary
        _state["last_run_error"] = error
        _state["is_running"] = False
        _state["last_run_finished"] = datetime.now(timezone.utc).isoformat()
    _push_telegram_safely()


def trigger_now(sources: list[str] | None = None, year: int | None = None, all_years: bool | None = None) -> bool:
    """Manual scrape across one or more sources — fire-and-forget in a
    background thread. Returns False (no-op) if a crawl is already
    running."""
    cfg = adminsettings.load()
    sources = sources or cfg["crawl_sources"]
    year = cfg["crawl_year"] if year is None else year
    all_years = cfg["crawl_all_years"] if all_years is None else all_years
    if _state["is_running"]:
        return False
    threading.Thread(
        target=_run_crawl_once, args=(sources, year, all_years, "manual"), daemon=True
    ).start()
    return True


def _scheduler_loop():
    while not _stop_event.is_set():
        cfg = adminsettings.load()
        if not cfg["scheduler_enabled"]:
            _state["next_scheduled_at"] = None
            _stop_event.wait(5)  # poll for the operator flipping it on
            continue
        interval = max(60.0, float(cfg["crawl_interval_seconds"]))
        _run_crawl_once(cfg["crawl_sources"], cfg["crawl_year"], cfg["crawl_all_years"], "scheduled")
        next_at = datetime.now(timezone.utc).timestamp() + interval
        _state["next_scheduled_at"] = datetime.fromtimestamp(next_at, tz=timezone.utc).isoformat()
        _stop_event.wait(interval)


# --- Web-search discovery (broader net, runs less often) ----------------

def _run_web_discovery_once(queries: list[str], triggered_by: str):
    from app.services import web_discovery
    from app.services.crawler import crawl_documents

    with _wd_lock:
        if _wd_state["is_running"]:
            eventlog.emit("web_discovery_skipped_overlap", "A web-discovery run is already in progress — skipped.", level="warning", source="web_discovery")
            return
        _wd_state["is_running"] = True
        _wd_state["last_run_started"] = datetime.now(timezone.utc).isoformat()

    summary = {}
    error = None
    try:
        docs = web_discovery.run(queries)
        summary["candidates"] = len(docs)
        if docs:
            report = crawl_documents("web_discovery", docs, triggered_by=triggered_by)
            summary.update(_summarize(report))
    except Exception as e:
        error = str(e)
        eventlog.emit("web_discovery_failed", f"Web discovery run raised: {e}", level="error", source="web_discovery")
    with _wd_lock:
        _wd_state["last_run_summary"] = summary
        _wd_state["last_run_error"] = error
        _wd_state["is_running"] = False
        _wd_state["last_run_finished"] = datetime.now(timezone.utc).isoformat()
    if summary.get("processed"):
        _push_telegram_safely()


def trigger_web_discovery_now(queries: list[str] | None = None) -> bool:
    cfg = adminsettings.load()
    queries = queries or cfg["web_discovery_queries"]
    if _wd_state["is_running"]:
        return False
    threading.Thread(target=_run_web_discovery_once, args=(queries, "manual"), daemon=True).start()
    return True


def _web_discovery_loop():
    while not _stop_event.is_set():
        cfg = adminsettings.load()
        if not cfg["web_discovery_scheduler_enabled"]:
            _wd_state["next_scheduled_at"] = None
            _stop_event.wait(5)
            continue
        interval = max(300.0, float(cfg["web_discovery_interval_seconds"]))
        _run_web_discovery_once(cfg["web_discovery_queries"], "scheduled")
        next_at = datetime.now(timezone.utc).timestamp() + interval
        _wd_state["next_scheduled_at"] = datetime.fromtimestamp(next_at, tz=timezone.utc).isoformat()
        _stop_event.wait(interval)


def start():
    """Idempotent — safe to call from FastAPI startup every reload."""
    with _lock:
        if not _state["thread_started"]:
            _state["thread_started"] = True
            _stop_event.clear()
            threading.Thread(target=_scheduler_loop, daemon=True).start()
    with _wd_lock:
        if not _wd_state["thread_started"]:
            _wd_state["thread_started"] = True
            threading.Thread(target=_web_discovery_loop, daemon=True).start()
    eventlog.emit("scheduler_thread_started", "Scheduler background threads started (crawl + web-discovery).")


def status() -> dict:
    cfg = adminsettings.load()
    return {**cfg, "crawl": dict(_state), "web_discovery": dict(_wd_state)}
