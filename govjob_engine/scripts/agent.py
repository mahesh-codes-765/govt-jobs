"""Standalone CLI alternative to running the full API: starts the same
crawl scheduler + web-discovery loop + Telegram poller that
`uvicorn app.main:app` starts automatically, without the HTTP server or
admin dashboard. Prefer running the API (it gives you the admin
dashboard for scheduling/logs/manual scrape); use this only when you
specifically don't want the HTTP server running.

Usage (PowerShell, from the govjob_engine folder):
    .\\.venv\\Scripts\\Activate.ps1
    python scripts\\agent.py                  # run forever, per data/admin_settings.json
    python scripts\\agent.py --once --source tgpsc --year 2026   # one crawl cycle, then exit
"""
import argparse
import time

from app.logging_config import setup_logging
from app.db import init_db
from app.services import scheduler, telegram_review


def main():
    p = argparse.ArgumentParser(description="Standalone crawl + review agent")
    p.add_argument("--once", action="store_true", help="Run a single crawl cycle and exit (ignores the schedule)")
    p.add_argument("--source", default="tgpsc")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--year", type=int)
    g.add_argument("--all-years", action="store_true")
    args = p.parse_args()

    setup_logging()
    init_db()

    if args.once:
        from app.services.crawler import crawl
        report = crawl(args.source, year=args.year, all_years=args.all_years, triggered_by="cli_once")
        print(report)
        telegram_review.push_pending_queue()
        return

    scheduler.start()
    telegram_review.start_background()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
