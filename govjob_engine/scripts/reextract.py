"""Re-run DETERMINISTIC extraction (regex only, no LLM, no re-download)
over every existing DocumentVersion's already-saved text file. For when
a deterministic-extractor bug (like the age-relaxation table-matching
fix) means data already in the database is stale/wrong and needs
correcting without spending LLM budget or re-crawling.

Leaves review_status untouched — this corrects facts on documents a
human already vetted, it doesn't re-litigate whether the document itself
belongs in the queue.

Usage:
    python scripts\\reextract.py            # all documents
    python scripts\\reextract.py --id 1      # just one, for spot-checking
"""
import argparse
import json
from pathlib import Path

from app.logging_config import setup_logging
from app.db import init_db, SessionLocal
from app.extractors.deterministic import extract_common, parse_date
from app.models.notification import DocumentVersion


def _load_pages(text_path: str) -> list[dict]:
    raw = Path(text_path).read_text(encoding="utf-8")
    pages = []
    for chunk in raw.split("===== PAGE ")[1:]:
        num_str, rest = chunk.split(" =====", 1)
        pages.append({"page": int(num_str), "text": rest})
    return pages


def main():
    p = argparse.ArgumentParser(description="Re-run deterministic extraction over saved documents")
    p.add_argument("--id", type=int, help="Only re-extract this DocumentVersion id")
    args = p.parse_args()

    setup_logging()
    init_db()
    db = SessionLocal()
    try:
        q = db.query(DocumentVersion)
        if args.id:
            q = q.filter(DocumentVersion.id == args.id)
        versions = q.all()
        changed = 0
        for v in versions:
            if not v.text_path or not Path(v.text_path).exists():
                print(f"  {v.id}: no saved text file, skipping")
                continue
            pages = _load_pages(v.text_path)
            new_common = extract_common(pages)
            merged = json.loads(v.extraction_json or "{}")
            old_common = merged.get("deterministic", {})
            merged["deterministic"] = new_common
            v.extraction_json = json.dumps(merged, ensure_ascii=False)
            v.notification.application_start = parse_date(new_common.get("application_start"))
            v.notification.application_end = parse_date(new_common.get("application_end"))
            v.notification.raw_json = v.extraction_json
            if old_common.get("age_policy") != new_common.get("age_policy"):
                changed += 1
                print(f"  {v.id} ({v.notification.notification_number}): age_policy changed")
                print(f"    old: {old_common.get('age_policy')}")
                print(f"    new: {new_common.get('age_policy')}")
        db.commit()
        print(f"Re-extracted {len(versions)} document(s), {changed} with a changed age_policy.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
