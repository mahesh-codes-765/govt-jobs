"""Long-poll Telegram review bot — no webhook, no open port.

Posts one card per document that reaches the review queue, with inline
Approve/Reject buttons. Only ever talks to TELEGRAM_CHAT_ID; taps from any
other chat are ignored. Designed to run in a background thread from
scripts/agent.py alongside the crawl scheduler.
"""
import logging
import threading
import requests
from app.config import settings
from app.db import SessionLocal
from app.services import review, eventlog

log = logging.getLogger("telegram_review")
API = "https://api.telegram.org/bot{token}/{method}"
_started = False


def _call(method: str, **params):
    url = API.format(token=settings.telegram_bot_token, method=method)
    r = requests.get(url, params=params, timeout=settings.request_timeout + 30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error on {method}: {data}")
    return data["result"]


def send_review_card(document_version) -> int | None:
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    text = review.render_card(document_version)
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve:{document_version.id}"},
            {"text": "❌ Reject", "callback_data": f"reject:{document_version.id}"},
        ]]
    }
    import json as _json
    result = _call(
        "sendMessage",
        chat_id=settings.telegram_chat_id,
        text=text[:4000],
        reply_markup=_json.dumps(keyboard),
    )
    return result.get("message_id")


def _handle_callback(callback: dict):
    chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
    if chat_id != str(settings.telegram_chat_id):
        log.warning("Ignoring callback from unauthorized chat %s", chat_id)
        return
    data = callback.get("data", "")
    user = callback.get("from", {})
    decided_by = str(user.get("id", "unknown"))
    action, _, id_str = data.partition(":")
    if action not in ("approve", "reject") or not id_str.isdigit():
        return
    document_version_id = int(id_str)
    try:
        if action == "approve":
            v = review.approve(document_version_id, decided_by)
            note = "✅ APPROVED"
        else:
            v = review.reject(document_version_id, decided_by)
            note = "❌ REJECTED"
    except ValueError as e:
        _call("answerCallbackQuery", callback_query_id=callback["id"], text=str(e))
        return

    _call("answerCallbackQuery", callback_query_id=callback["id"], text=note)
    message_id = callback.get("message", {}).get("message_id")
    if message_id:
        original = callback.get("message", {}).get("text", "")
        _call(
            "editMessageText",
            chat_id=settings.telegram_chat_id,
            message_id=message_id,
            text=f"{original}\n\n{note} by {decided_by}"[:4000],
        )


def poll_forever(stop_event=None):
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        log.info("Telegram review bot disabled (TELEGRAM_ENABLED=false or no token) — not polling.")
        return
    offset = None
    log.info("Telegram review bot: long-polling for approve/reject taps.")
    while stop_event is None or not stop_event.is_set():
        try:
            updates = _call("getUpdates", timeout=25, offset=offset, allowed_updates='["callback_query"]')
        except Exception as e:
            log.warning("Telegram poll failed: %s", e)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                try:
                    _handle_callback(cq)
                except Exception as e:
                    log.exception("Error handling callback: %s", e)


def start_background():
    """Idempotent — safe to call every FastAPI startup/reload. No-op if
    Telegram isn't configured."""
    global _started
    if _started or not settings.telegram_enabled or not settings.telegram_bot_token:
        return
    _started = True
    threading.Thread(target=poll_forever, daemon=True).start()


def push_pending_queue():
    """Send a card for every pending_review document that hasn't been
    pushed to Telegram yet (telegram_message_id is null)."""
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return 0
    db = SessionLocal()
    sent = 0
    try:
        for v in review.pending(db):
            if v.telegram_message_id:
                continue
            message_id = send_review_card(v)
            if message_id:
                v.telegram_message_id = message_id
                db.commit()
                sent += 1
                eventlog.emit("telegram_card_sent", f"{v.notification.notification_number or v.id}: review card pushed to Telegram",
                              notification_number=v.notification.notification_number)
    finally:
        db.close()
    return sent
