import json
import logging
from app.db import SessionLocal
from app.models.notification import EventLog

log = logging.getLogger("govjob")


def emit(event_type: str, message: str, level: str = "info", source: str | None = None,
          notification_number: str | None = None, **data):
    """Record one admin-timeline event — persisted to the DB (for the
    admin page's live feed) and to the rotating logfile (for grep/tail).
    Never raises: a logging failure must not break the crawl it's
    describing."""
    getattr(log, level if level in ("info", "warning", "error") else "info")(
        "%s | %s%s", event_type, message, f" | {data}" if data else ""
    )
    try:
        db = SessionLocal()
        try:
            db.add(EventLog(
                level=level, event_type=event_type, source=source,
                notification_number=notification_number, message=message,
                data_json=json.dumps(data, ensure_ascii=False, default=str) if data else None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("Failed to persist event log entry (event_type=%s)", event_type)


def recent(db, limit: int = 200, event_type: str | None = None, since_id: int | None = None):
    from sqlalchemy import select
    from app.models.notification import EventLog as E
    q = select(E).order_by(E.id.desc())
    if event_type:
        q = q.where(E.event_type == event_type)
    if since_id:
        q = q.where(E.id > since_id)
    q = q.limit(limit)
    return list(db.scalars(q))
