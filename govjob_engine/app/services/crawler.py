import hashlib, json, re, time
from pathlib import Path
import requests
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models.notification import Source, Recruitment, Notification, DocumentVersion, Evidence
from app.adapters.registry import get_adapter
from app.extractors.pdf import extract_pdf, relevant_pages
from app.extractors.deterministic import extract_common, parse_date
from app.extractors.classifier import classify
from app.services.llm import extract_with_llm
from app.services import review, eventlog

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"; TEXT = ROOT / "data" / "text"
RAW.mkdir(parents=True, exist_ok=True); TEXT.mkdir(parents=True, exist_ok=True)

def _safe(s): return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "document")[:120]

def _recruitment_key(d):
    return f"{d.source_key}:{d.notification_number or d.title[:100]}:{d.year or 'unknown'}"

def _upsert_recruitment(db, source, d):
    key = _recruitment_key(d)
    r = db.scalar(select(Recruitment).where(Recruitment.recruitment_key == key))
    if not r:
        r = Recruitment(source_id=source.id, recruitment_key=key, year=d.year, notification_number=d.notification_number, title=d.title, department=d.department)
        db.add(r); db.commit(); db.refresh(r)
    else:
        r.title = d.title or r.title; r.department = d.department or r.department; db.commit()
    return r

def _upsert_notification(db, source, recruitment, d):
    n = db.scalar(select(Notification).where(Notification.official_url == d.official_url))
    if not n:
        n = Notification(source_id=source.id, recruitment_id=recruitment.id, notification_number=d.notification_number,
                         year=d.year, title=d.title, document_type=d.document_type, official_url=d.official_url)
        db.add(n); db.commit(); db.refresh(n)
    return n

def _get_or_create_source(db, key, name, listing_url, adapter_name):
    source = db.scalar(select(Source).where(Source.key == key))
    if not source:
        source = Source(key=key, name=name, listing_url=listing_url, adapter=adapter_name)
        db.add(source); db.commit(); db.refresh(source)
    return source

def _process_documents(db, source_key, source, docs):
    """Download -> version -> extract -> classify -> LLM cross-check ->
    review-queue, for a batch of already-discovered documents. Shared by
    the fixed site adapters and by web-search discovery — same pipeline,
    same review queue, regardless of where the URL came from."""
    results = []
    for d in docs:
        recruitment = _upsert_recruitment(db, source, d)
        n = _upsert_notification(db, source, recruitment, d)
        item={"notification": n.id, "number": n.notification_number, "year": n.year, "type": n.document_type, "title": n.title}
        try:
            r = requests.get(d.official_url, timeout=settings.request_timeout, headers={"User-Agent": settings.user_agent})
            r.raise_for_status(); data=r.content
            if not data.startswith(b"%PDF") and b"<html" in data[:500].lower():
                raise RuntimeError("Official URL returned HTML instead of PDF")
            sha=hashlib.sha256(data).hexdigest()
            existing=db.scalar(select(DocumentVersion).where(DocumentVersion.notification_id==n.id, DocumentVersion.sha256==sha))
            if existing:
                item["status"]="unchanged"
                eventlog.emit("document_unchanged", f"{n.notification_number or n.id}: already have this version", source=source_key, notification_number=n.notification_number)
                results.append(item); continue
            pdf=RAW/f"{n.id}_{_safe(d.notification_number or str(n.id))}_{sha[:12]}.pdf"
            txt=TEXT/f"{pdf.stem}.txt"; pdf.write_bytes(data)
            eventlog.emit("document_downloaded", f"{n.notification_number or n.id}: PDF downloaded ({len(data)} bytes)", source=source_key, notification_number=n.notification_number, bytes=len(data))
            pages_count,pages=extract_pdf(str(pdf),str(txt)); common=extract_common(pages)
            classification=classify(pages, title=d.title, document_type=getattr(d, 'document_type', None) or n.document_type)
            llm=None; llm_error=None
            if classification["label"]=="recruitment" and classification["has_age_clause"]:
                try:
                    llm=extract_with_llm(relevant_pages(pages))
                    if llm:
                        eventlog.emit("llm_call", f"{n.notification_number or n.id}: LLM extraction ok (${llm.get('_llm_cost_usd', 0):.4f})",
                                      source=source_key, notification_number=n.notification_number, cost_usd=llm.get("_llm_cost_usd"))
                except Exception as e:
                    llm_error=str(e)
                    eventlog.emit("llm_call_failed", f"{n.notification_number or n.id}: LLM extraction failed: {e}", level="warning", source=source_key, notification_number=n.notification_number)
            merged={"deterministic":common,"llm":llm,"llm_error":llm_error,"relevant_pages":[p["page"] for p in relevant_pages(pages)],"classification":classification}
            v=DocumentVersion(notification_id=n.id,recruitment_id=recruitment.id,sha256=sha,local_pdf_path=str(pdf),text_path=str(txt),page_count=pages_count,extraction_json=json.dumps(merged,ensure_ascii=False),extraction_status="extracted" if llm_error is None else "extracted_without_llm")
            db.add(v); db.commit(); db.refresh(v)
            verdict=review.queue_for_review(db, v, classification)
            # Evidence for deterministic values with the smallest sensible page reference.
            for field,value in common.items():
                if field == "age_candidates":
                    for a in value:
                        db.add(Evidence(document_version_id=v.id,field_name=field,value_json=json.dumps(a),page_number=a.get("page"),evidence_text=a.get("evidence"),method="deterministic"))
                    continue
                needle=str(value).split("T")[0] if value else ""
                page_no=1; ev=None
                for p in pages:
                    if needle and needle.lower() in p["text"].lower(): page_no=p["page"]; ev=p["text"][:3000]; break
                db.add(Evidence(document_version_id=v.id,field_name=field,value_json=json.dumps(value),page_number=page_no,evidence_text=ev,method="deterministic"))
            n.application_start=parse_date(common.get("application_start")); n.application_end=parse_date(common.get("application_end")); n.status="extracted"; n.raw_json=json.dumps(merged,ensure_ascii=False); recruitment.status="extracted"; db.commit()
            item.update({"status":"processed","version":v.id,"pages":pages_count,"llm":bool(llm),"llm_error":llm_error,
                         "classification":classification["label"],"review_status":v.review_status,"verdict":verdict["verdict"]})
            eventlog.emit("document_processed", f"{n.notification_number or n.id}: processed -> {v.review_status} ({verdict['verdict']})",
                          source=source_key, notification_number=n.notification_number, review_status=v.review_status, verdict=verdict["verdict"])
        except Exception as e:
            n.status="error"; db.commit(); item.update({"status":"error","error":str(e)})
            eventlog.emit("document_error", f"{n.notification_number or n.id}: {e}", level="error", source=source_key, notification_number=n.notification_number)
        results.append(item); time.sleep(settings.crawl_delay_seconds)
    return results

def _finish(source_key, docs, results):
    summary={"source":source_key,"discovered":len(docs),"results":results}
    counts={"processed":sum(1 for r in results if r.get("status")=="processed"),
            "unchanged":sum(1 for r in results if r.get("status")=="unchanged"),
            "errors":sum(1 for r in results if r.get("status")=="error")}
    eventlog.emit("crawl_finished", f"Crawl finished: {counts}", source=source_key, **counts)
    return summary

def crawl(source_key: str, year: int | None = None, all_years: bool = False, triggered_by: str = "api"):
    adapter = get_adapter(source_key)
    eventlog.emit("crawl_started", f"Crawl started (year={year}, all_years={all_years}, triggered_by={triggered_by})", source=source_key)
    db = SessionLocal()
    try:
        source = _get_or_create_source(db, adapter.key, adapter.name, adapter.listing_url, adapter.__class__.__name__)
        try:
            docs = adapter.discover(year=year, all_years=all_years)
        except Exception as e:
            eventlog.emit("crawl_failed", f"Listing discovery failed: {e}", level="error", source=source_key)
            raise
        eventlog.emit("crawl_discovered", f"{len(docs)} document(s) discovered on the listing page", source=source_key, count=len(docs))
        results = _process_documents(db, source_key, source, docs)
        summary = _finish(source_key, docs, results)
        summary.update({"year": year, "all_years": all_years})
        return summary
    finally: db.close()

def crawl_documents(source_key: str, docs: list, triggered_by: str = "web_discovery",
                     source_name: str = "Web Search Discovery", listing_url: str = "(dynamic — Claude web search)"):
    """Run the same download/extract/classify/review pipeline over a list
    of already-discovered documents that didn't come from a registered
    site adapter — e.g. candidates found by web-search discovery."""
    eventlog.emit("crawl_started", f"Processing {len(docs)} discovered document(s) (triggered_by={triggered_by})", source=source_key)
    db = SessionLocal()
    try:
        source = _get_or_create_source(db, source_key, source_name, listing_url, "WebDiscovery")
        eventlog.emit("crawl_discovered", f"{len(docs)} document(s) to process", source=source_key, count=len(docs))
        results = _process_documents(db, source_key, source, docs)
        return _finish(source_key, docs, results)
    finally: db.close()
