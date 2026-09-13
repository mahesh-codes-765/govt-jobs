import json
from sqlalchemy import select
from app.db import SessionLocal
from app.models.notification import DocumentVersion, ReviewDecision
from app.services import eventlog


def _age_ranges(ages: list[dict]) -> set[tuple]:
    return {
        (a.get("min_age"), a.get("max_age"))
        for a in ages
        if a.get("min_age") is not None and a.get("max_age") is not None
    }


def automated_verdict(merged: dict, classification: dict) -> dict:
    """Combine the document classifier with a deterministic-vs-LLM
    cross-check to decide how urgently a document needs a human's eyes.

    verdict is one of:
      - "held": not a recruitment notification (or no age clause at all
        per the classifier) — kept out of the review queue entirely so
        hall tickets and results don't flood it.
      - "flagged": a real recruitment notification where either reader
        came up empty, or the two readers disagree — strongest signal
        something's off, needs a human before it's trusted.
      - "verified": both readers independently agree on the age range —
        still queued for a human tap, but with the least friction.
    """
    reasons = []
    if classification.get("label") != "recruitment" or not classification.get("has_age_clause"):
        reasons.append("classifier: " + "; ".join(classification.get("reasons", [])))
        return {"verdict": "held", "reasons": reasons}

    det = (merged.get("deterministic") or {})
    age_policy = det.get("age_policy") or {}
    det_ages = det.get("age_candidates") or []
    if age_policy.get("min_age") is not None and age_policy.get("max_age") is not None:
        det_set = {(age_policy["min_age"], age_policy["max_age"])}
    else:
        det_set = _age_ranges(det_ages)

    if not det_set:
        reasons.append("classifier found an age clause but the deterministic reader could not pin down a numeric range (likely a table layout) — needs a human read")
        return {"verdict": "flagged", "reasons": reasons}

    llm = merged.get("llm")
    if not llm:
        reasons.append("LLM reader unavailable (disabled, errored, or budget-capped) — single-reader extraction")
        return {"verdict": "flagged", "reasons": reasons}

    llm_ages = llm.get("age_rules") or []
    if not llm_ages:
        reasons.append("LLM reader returned no age_rules while the deterministic reader found an age clause")
        return {"verdict": "flagged", "reasons": reasons}

    llm_set = _age_ranges(llm_ages)
    agree = any(
        abs(d[0] - l[0]) <= 1 and abs(d[1] - l[1]) <= 1
        for d in det_set for l in llm_set
    )
    if agree:
        reasons.append(f"deterministic {sorted(det_set)} and LLM {sorted(llm_set)} age ranges agree")
        return {"verdict": "verified", "reasons": reasons}

    reasons.append(f"deterministic {sorted(det_set)} disagrees with LLM {sorted(llm_set)} age ranges")
    return {"verdict": "flagged", "reasons": reasons}


def render_card(document_version: DocumentVersion) -> str:
    """Plain-text summary shown on the Telegram review card."""
    n = document_version.notification
    merged = json.loads(document_version.extraction_json or "{}")
    det = merged.get("deterministic", {})
    verdict = json.loads(document_version.review_verdict_json or "{}")
    ages = det.get("age_candidates") or []
    age_line = ", ".join(f"{a['min_age']}-{a['max_age']} (p{a['page']})" for a in ages[:3]) or "none found"
    lines = [
        f"#{n.notification_number or n.id} — {n.title[:200]}",
        f"Department: {n.recruitment.department if n.recruitment else '—'}",
        f"Verdict: {verdict.get('verdict', '?').upper()}",
        f"Ages: {age_line}",
        f"Applications: {det.get('application_start', '?')} -> {det.get('application_end', '?')}",
        f"Source: {n.official_url}",
    ]
    for r in verdict.get("reasons", []):
        lines.append(f"• {r}")
    return "\n".join(lines)


def queue_for_review(db, document_version: DocumentVersion, classification: dict) -> dict:
    merged = json.loads(document_version.extraction_json or "{}")
    verdict = automated_verdict(merged, classification)
    document_version.classification_label = classification.get("label")
    document_version.classification_reasons = json.dumps(classification.get("reasons", []))
    document_version.review_verdict_json = json.dumps(verdict)
    document_version.review_status = "skipped_non_recruitment" if verdict["verdict"] == "held" else "pending_review"
    db.add(ReviewDecision(
        document_version_id=document_version.id,
        decision="auto_flagged" if verdict["verdict"] != "held" else "held",
        decided_by="auto",
        reason="; ".join(verdict["reasons"]),
        snapshot_json=document_version.extraction_json,
    ))
    db.commit()
    eventlog.emit("review_queued", f"{document_version.notification.notification_number or document_version.id}: {verdict['verdict']} -> {document_version.review_status}",
                  notification_number=document_version.notification.notification_number, verdict=verdict["verdict"], review_status=document_version.review_status)
    return verdict


def pending(db) -> list[DocumentVersion]:
    return list(db.scalars(
        select(DocumentVersion).where(DocumentVersion.review_status == "pending_review").order_by(DocumentVersion.id)
    ))


def _decide(db, document_version_id: int, decision: str, decided_by: str, reason: str | None = None) -> DocumentVersion:
    v = db.get(DocumentVersion, document_version_id)
    if not v:
        raise ValueError(f"No DocumentVersion with id {document_version_id}")
    v.review_status = "approved" if decision == "approved" else "rejected"
    db.add(ReviewDecision(
        document_version_id=v.id, decision=decision, decided_by=decided_by,
        reason=reason, snapshot_json=v.extraction_json,
    ))
    db.commit()
    db.refresh(v)
    eventlog.emit("review_decision", f"{v.notification.notification_number or v.id}: {decision} by {decided_by}",
                  notification_number=v.notification.notification_number, decision=decision, decided_by=decided_by, reason=reason)
    return v


def approve(document_version_id: int, decided_by: str) -> DocumentVersion:
    db = SessionLocal()
    try:
        return _decide(db, document_version_id, "approved", decided_by)
    finally:
        db.close()


def reject(document_version_id: int, decided_by: str, reason: str | None = None) -> DocumentVersion:
    db = SessionLocal()
    try:
        return _decide(db, document_version_id, "rejected", decided_by, reason)
    finally:
        db.close()
