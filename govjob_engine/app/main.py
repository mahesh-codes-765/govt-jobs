import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import check_basic_header, require_admin
from app.logging_config import setup_logging
from app.db import init_db, SessionLocal
from app.models.notification import Source, Recruitment, Notification, DocumentVersion, Lead
from app.services.crawler import crawl
from app.services import review, eventlog, adminsettings, scheduler, telegram_review, age_fallback
from app.services.jobs import build_age_relaxation_summary, extract_cutoff, find_prior_cutoff, list_jobs

setup_logging()
init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    telegram_review.start_background()
    eventlog.emit("api_startup", "API server started.")
    yield

app=FastAPI(title="GovJob Intelligence Engine",version="1.0.0",lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

WEBAPP_DIR = Path(__file__).resolve().parents[1] / "webapp"

@app.middleware("http")
async def protect_admin_static(request: Request, call_next):
    """Fail-closed Basic auth for the admin HTML (StaticFiles has no Depends)."""
    path = request.url.path.rstrip("/") or "/"
    if path == "/app/admin.html" or path.endswith("/admin.html"):
        if request.method == "OPTIONS":
            return await call_next(request)
        auth = request.headers.get("Authorization")
        if not check_basic_header(auth):
            return JSONResponse(
                {"detail": "Not authenticated"},
                status_code=401,
                headers={"WWW-Authenticate": "Basic"},
            )
    return await call_next(request)

if WEBAPP_DIR.exists():
    # Explicit protected route takes precedence over the StaticFiles mount.
    @app.get("/app/admin.html")
    def serve_admin_html(_user: str = Depends(require_admin)):
        return FileResponse(WEBAPP_DIR / "admin.html")

    app.mount("/app", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")

@app.get('/health')
def health(): return {'status':'ok','service':'govjob-engine'}

@app.get('/sources')
def sources():
    db=SessionLocal()
    try: return [{'id':s.id,'key':s.key,'name':s.name,'listing_url':s.listing_url,'enabled':s.enabled} for s in db.scalars(select(Source)).all()]
    finally: db.close()

@app.post('/crawl/{source_key}')
def run_crawl(source_key:str, year:int|None=None, all_years:bool=False, _user: str = Depends(require_admin)):
    try: return crawl(source_key,year=year,all_years=all_years,triggered_by="api_manual")
    except ValueError as e: raise HTTPException(404,str(e))

@app.get('/recruitments')
def recruitments(year:int|None=None, source:str|None=None):
    db=SessionLocal()
    try:
        q=select(Recruitment).order_by(Recruitment.id.desc())
        if year is not None: q=q.where(Recruitment.year==year)
        if source: q=q.join(Source).where(Source.key==source)
        return [{'id':r.id,'source':r.source.key,'year':r.year,'notification_number':r.notification_number,'title':r.title,'status':r.status,'documents':len(r.documents)} for r in db.scalars(q).all()]
    finally: db.close()

@app.get('/notifications')
def notifications(year:int|None=None, source:str|None=None):
    db=SessionLocal()
    try:
        q=select(Notification).order_by(Notification.id.desc())
        if year is not None: q=q.where(Notification.year==year)
        if source: q=q.join(Source).where(Source.key==source)
        return [{'id':n.id,'source':n.source.key,'recruitment_id':n.recruitment_id,'notification_number':n.notification_number,'year':n.year,'title':n.title,'document_type':n.document_type,'official_url':n.official_url,'status':n.status,'application_start':n.application_start,'application_end':n.application_end,'versions':len(n.versions)} for n in db.scalars(q).all()]
    finally: db.close()

@app.get('/notifications/{notification_id}')
def notification(notification_id:int):
    db=SessionLocal()
    try:
        n=db.get(Notification,notification_id)
        if not n: raise HTTPException(404,'Notification not found')
        versions=[{'id':v.id,'sha256':v.sha256,'page_count':v.page_count,'status':v.extraction_status,'review_status':v.review_status,'extraction':json.loads(v.extraction_json or '{}')} for v in n.versions]
        return {'id':n.id,'source':n.source.key,'recruitment_id':n.recruitment_id,'notification_number':n.notification_number,'year':n.year,'title':n.title,'department':n.recruitment.department if n.recruitment else None,'document_type':n.document_type,'official_url':n.official_url,'versions':versions}
    finally: db.close()

# --- Human review queue -----------------------------------------------

@app.get('/review/pending')
def review_pending(_user: str = Depends(require_admin)):
    db=SessionLocal()
    try:
        rows=review.pending(db)
        return [{'document_version_id':v.id,'notification_id':v.notification_id,
                 'notification_number':v.notification.notification_number,'title':v.notification.title,
                 'verdict':json.loads(v.review_verdict_json or '{}'),'card':review.render_card(v)} for v in rows]
    finally: db.close()

class ReviewAction(BaseModel):
    decided_by: str = "operator"
    reason: str | None = None

@app.post('/review/{document_version_id}/approve')
def review_approve(document_version_id:int, action:ReviewAction, _user: str = Depends(require_admin)):
    try: v=review.approve(document_version_id, action.decided_by)
    except ValueError as e: raise HTTPException(404,str(e))
    return {'document_version_id':v.id,'review_status':v.review_status}

@app.post('/review/{document_version_id}/reject')
def review_reject(document_version_id:int, action:ReviewAction, _user: str = Depends(require_admin)):
    try: v=review.reject(document_version_id, action.decided_by, action.reason)
    except ValueError as e: raise HTTPException(404,str(e))
    return {'document_version_id':v.id,'review_status':v.review_status}

# --- Public jobs feed (student product) --------------------------------

@app.get('/jobs')
def jobs(window: str = "all", source: str | None = None):
    """Open this month / closed last 6 months / all (incl. dates_unknown).

    Never invents dates or cutoffs. eligibility_ready is true only when an
    approved DocumentVersion exists.
    """
    db = SessionLocal()
    try:
        try:
            return list_jobs(db, window=window, source=source)
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        db.close()

# --- Public eligibility feed (the product) -----------------------------

@app.get('/eligibility/notifications')
def eligibility_notifications():
    """Only human-approved documents, with the fields the eligibility
    checker needs and honest confidence labels. Never exposes unreviewed
    or rejected extractions — a wrong eligibility answer costs a real
    person a missed application."""
    db=SessionLocal()
    try:
        rows=db.scalars(select(DocumentVersion).where(DocumentVersion.review_status=='approved').order_by(DocumentVersion.id.desc())).all()
        out=[]
        for v in rows:
            merged=json.loads(v.extraction_json or '{}')
            det=merged.get('deterministic',{}) or {}
            llm=merged.get('llm') or {}
            n=v.notification
            cutoff = extract_cutoff(det, llm if isinstance(llm, dict) else {})
            age_policy = det.get('age_policy')
            age_fb = age_fallback.build_fallback(llm.get('age_rules') if isinstance(llm, dict) else None)
            row={
                'notification_id':n.id,'document_version_id':v.id,'source':n.source.key,
                'notification_number':n.notification_number,'title':n.title,
                'department':n.recruitment.department if n.recruitment else (llm.get('department') if llm else None),
                'official_url':n.official_url,
                'application_start':det.get('application_start'),'application_end':det.get('application_end'),
                'year': n.year or (n.recruitment.year if n.recruitment else None),
                'recruitment_key': n.recruitment.recruitment_key if n.recruitment else None,
                'age_policy':age_policy,
                'age_rules_llm':llm.get('age_rules') if isinstance(llm, dict) else None,
                'age_policy_llm_fallback':age_fb,
                'age_relaxation': build_age_relaxation_summary(age_policy, age_fb),
                'qualifications':llm.get('qualifications') if isinstance(llm, dict) else None,
                'district_rules':llm.get('district_rules') if isinstance(llm, dict) else None,
                'cutoff': cutoff,
                'prior_cutoff': None,
            }
            out.append(row)
        # Attach prior_cutoff using the same conservative matcher as /jobs.
        pool = []
        for r in out:
            if r.get('cutoff') is None:
                continue
            pool.append({
                'id': r['notification_id'],
                'source': r['source'],
                'year': r.get('year'),
                'title': r.get('title'),
                'department': r.get('department'),
                'recruitment_key': r.get('recruitment_key'),
                'application_end': r.get('application_end'),
                'cutoff': r.get('cutoff'),
            })
        for r in out:
            cur = {
                'id': r['notification_id'],
                'source': r['source'],
                'year': r.get('year'),
                'title': r.get('title'),
                'department': r.get('department'),
                'recruitment_key': r.get('recruitment_key'),
                'application_end': r.get('application_end'),
            }
            r['prior_cutoff'] = find_prior_cutoff(cur, pool)
        return out
    finally: db.close()

# --- Lead capture (the revenue mechanism) -------------------------------

class LeadIn(BaseModel):
    whatsapp_number: str
    name: str | None = None
    district: str | None = None
    category: str | None = None
    qualification_level: str | None = None
    qualification_discipline: str | None = None
    dob: str | None = None
    gender: str | None = None
    notification_id: int | None = None
    notification_number: str | None = None
    consent: bool = False

@app.post('/leads')
def create_lead(lead:LeadIn):
    if not lead.consent:
        raise HTTPException(400,'Lead capture requires explicit consent')
    if not lead.whatsapp_number or len(lead.whatsapp_number.strip()) < 8:
        raise HTTPException(400,'A valid WhatsApp number is required')
    db=SessionLocal()
    try:
        row=Lead(**lead.model_dump())
        db.add(row); db.commit(); db.refresh(row)
        eventlog.emit("lead_captured", f"New lead: {row.district or '?'} / {row.category or '?'} / {row.qualification_level or '?'}",
                      notification_number=row.notification_number, lead_id=row.id)
        return {'id':row.id,'status':'captured'}
    finally: db.close()

@app.get('/leads')
def list_leads(district:str|None=None, category:str|None=None, sold:bool|None=None, _user: str = Depends(require_admin)):
    db=SessionLocal()
    try:
        q=select(Lead).order_by(Lead.id.desc())
        if district: q=q.where(Lead.district==district)
        if category: q=q.where(Lead.category==category)
        if sold is not None: q=q.where(Lead.sold==sold)
        return [{'id':l.id,'whatsapp_number':l.whatsapp_number,'name':l.name,'district':l.district,
                 'category':l.category,'qualification_level':l.qualification_level,
                 'qualification_discipline':l.qualification_discipline,'notification_number':l.notification_number,
                 'sold':l.sold,'created_at':l.created_at} for l in db.scalars(q).all()]
    finally: db.close()

# --- Admin: scheduling, manual crawl trigger, live log feed -------------

@app.get('/admin/sources')
def admin_sources(_user: str = Depends(require_admin)):
    """The fixed site adapters actually registered — drives the admin
    page's source checkboxes so it never drifts from what's really
    available."""
    from app.adapters.registry import ADAPTERS
    return [{'key':k,'name':a.name,'listing_url':a.listing_url} for k,a in ((k,v()) for k,v in ADAPTERS.items())]

@app.get('/admin/status')
def admin_status(_user: str = Depends(require_admin)):
    from app.services import spend
    from app.config import settings as cfg
    db=SessionLocal()
    try:
        by_source={}
        for s in db.scalars(select(Source)).all():
            by_source[s.key]=len(db.scalars(select(Notification).where(Notification.source_id==s.id)).all())
        counts={
            'notifications_total': len(db.scalars(select(Notification)).all()),
            'notifications_by_source': by_source,
            'pending_review': len(review.pending(db)),
            'approved': len(db.scalars(select(DocumentVersion).where(DocumentVersion.review_status=='approved')).all()),
            'rejected': len(db.scalars(select(DocumentVersion).where(DocumentVersion.review_status=='rejected')).all()),
            'leads_total': len(db.scalars(select(Lead)).all()),
        }
    finally: db.close()
    return {
        'scheduler': scheduler.status(),
        'telegram_enabled': cfg.telegram_enabled,
        'llm_enabled': cfg.llm_enabled,
        'web_discovery_enabled': cfg.web_discovery_enabled,
        'llm_spend_usd_this_month': spend.month_spend_usd('llm'),
        'llm_monthly_cap_usd': cfg.llm_monthly_usd_cap,
        'web_discovery_spend_usd_this_month': spend.month_spend_usd('web_discovery'),
        'web_discovery_monthly_usd_cap': cfg.web_discovery_monthly_usd_cap,
        'counts': counts,
    }

class AdminSettingsIn(BaseModel):
    scheduler_enabled: bool | None = None
    crawl_interval_seconds: float | None = None
    crawl_sources: list[str] | None = None
    crawl_year: int | None = None
    crawl_all_years: bool | None = None
    web_discovery_scheduler_enabled: bool | None = None
    web_discovery_interval_seconds: float | None = None
    web_discovery_queries: list[str] | None = None

@app.get('/admin/settings')
def admin_get_settings(_user: str = Depends(require_admin)):
    return adminsettings.load()

@app.post('/admin/settings')
def admin_update_settings(patch: AdminSettingsIn, _user: str = Depends(require_admin)):
    updated = adminsettings.save({k:v for k,v in patch.model_dump().items() if v is not None})
    eventlog.emit("admin_settings_changed", f"Admin settings updated: {patch.model_dump(exclude_none=True)}")
    return updated

class CrawlNowIn(BaseModel):
    sources: list[str] | None = None
    year: int | None = None
    all_years: bool | None = None

@app.post('/admin/crawl-now')
def admin_crawl_now(body: CrawlNowIn = CrawlNowIn(), _user: str = Depends(require_admin)):
    started = scheduler.trigger_now(sources=body.sources, year=body.year, all_years=body.all_years)
    if not started:
        raise HTTPException(409, 'A crawl is already running')
    return {'status': 'started'}

class WebDiscoveryNowIn(BaseModel):
    queries: list[str] | None = None

@app.post('/admin/web-discovery/run')
def admin_web_discovery_run(body: WebDiscoveryNowIn = WebDiscoveryNowIn(), _user: str = Depends(require_admin)):
    from app.config import settings as cfg
    if not cfg.web_discovery_enabled:
        raise HTTPException(400, 'WEB_DISCOVERY_ENABLED is false in .env - set it true and restart to use this.')
    started = scheduler.trigger_web_discovery_now(queries=body.queries)
    if not started:
        raise HTTPException(409, 'A web-discovery run is already in progress')
    return {'status': 'started'}

@app.get('/admin/logs')
def admin_logs(limit: int=200, event_type: str|None=None, since_id: int|None=None, _user: str = Depends(require_admin)):
    db=SessionLocal()
    try:
        rows=eventlog.recent(db, limit=limit, event_type=event_type, since_id=since_id)
        return [{'id':r.id,'created_at':r.created_at,'level':r.level,'event_type':r.event_type,
                 'source':r.source,'notification_number':r.notification_number,'message':r.message,
                 'data': json.loads(r.data_json) if r.data_json else None} for r in rows]
    finally: db.close()
