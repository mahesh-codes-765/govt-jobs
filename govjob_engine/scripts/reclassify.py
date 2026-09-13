"""Re-run classification (free, regex-only) over already-downloaded
DocumentVersions currently marked "skipped_non_recruitment", and for any
that now qualify as recruitment+age-clause under a fixed classifier, run
the LLM cross-check and re-queue for review — same as a fresh crawl would,
without re-downloading anything.

For when a classifier bug (too-narrow RECRUITMENT regex, too-short page
window, ...) wrongly held back real documents that are already on disk.
Only touches documents currently "held"; anything already pending/approved/
rejected is left alone. This step DOES call the LLM for anything that newly
qualifies, so it costs real money (bounded by the same monthly budget cap
every other LLM call respects).

Usage:
    python scripts\\reclassify.py
"""
import json
from pathlib import Path
from app.logging_config import setup_logging
from app.db import init_db, SessionLocal
from app.extractors.classifier import classify
from app.extractors.pdf import relevant_pages
from app.services.llm import extract_with_llm
from app.services import review, eventlog
from app.models.notification import DocumentVersion


def _load_pages(text_path: str) -> list[dict]:
    raw = Path(text_path).read_text(encoding="utf-8")
    pages = []
    for chunk in raw.split("===== PAGE ")[1:]:
        num_str, rest = chunk.split(" =====", 1)
        pages.append({"page": int(num_str), "text": rest})
    return pages


def main():
    setup_logging()
    init_db()
    db = SessionLocal()
    try:
        versions = db.query(DocumentVersion).filter(
            DocumentVersion.review_status == "skipped_non_recruitment"
        ).all()
        print(f"{len(versions)} held document(s) to re-check")
        requalified = 0
        for v in versions:
            if not v.text_path or not Path(v.text_path).exists():
                continue
            pages = _load_pages(v.text_path)
            classification = classify(pages, title=v.notification.title)
            if not (classification["label"] == "recruitment" and classification["has_age_clause"]):
                continue

            merged = json.loads(v.extraction_json or "{}")
            llm = None
            llm_error = None
            try:
                llm = extract_with_llm(relevant_pages(pages))
                if llm:
                    eventlog.emit("llm_call", f"{v.notification.notification_number or v.id}: LLM extraction ok (${llm.get('_llm_cost_usd', 0):.4f}) [reclassify]",
                                  notification_number=v.notification.notification_number, cost_usd=llm.get("_llm_cost_usd"))
            except Exception as e:
                llm_error = str(e)
                eventlog.emit("llm_call_failed", f"{v.notification.notification_number or v.id}: LLM extraction failed: {e} [reclassify]",
                              level="warning", notification_number=v.notification.notification_number)

            merged["llm"] = llm
            merged["llm_error"] = llm_error
            merged["classification"] = classification
            v.extraction_json = json.dumps(merged, ensure_ascii=False)
            v.notification.raw_json = v.extraction_json
            verdict = review.queue_for_review(db, v, classification)
            requalified += 1
            print(f"  {v.id} {v.notification.notification_number or '?'}: -> {verdict['verdict']} ({v.review_status}) — {v.notification.title[:80]}")
        db.commit()
        print(f"Re-classified and re-queued {requalified} document(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
