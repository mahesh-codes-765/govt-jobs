# GovJob Intelligence Engine — Year-Wide Edition

This engine discovers official recruitment documents for a source and a requested year, downloads each PDF, fingerprints versions, extracts text, identifies relevant sections, performs deterministic extraction, optionally uses an LLM for difficult structured fields, stores evidence, and exposes the data through FastAPI.

## What it does

`--year 2026` means: crawl the official source listing/archive, discover **all discoverable 2026 documents**, not just the first notification.

It stores both:
- `Recruitment`: the underlying hiring/recruitment entity.
- `Notification`: every discovered official document related to it (original notification, corrigendum, extension, result, verification, etc.).
- `DocumentVersion`: content/hash history so revised PDFs are not silently overwritten.
- `Evidence`: page + text supporting extracted values.

Two source adapters are registered today: TGPSC and APPSC (`app/adapters/registry.py`) — both plain `requests` + BeautifulSoup, no browser automation needed, verified live. Add another board by implementing `SourceAdapter.discover()` without changing the ingestion core (`app/adapters/common.py` has the shared year/doc-type/notification-number helpers).

Boards whose listing pages are JS-rendered (SSC, RRB) aren't adapter-friendly without browser automation — those are covered instead by **web-search discovery** (below), which finds the actual notification URL directly via search rather than needing to parse that board's own listing page.

## Windows setup

Open PowerShell in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## 1. Run tests first

```powershell
pytest -q
```

You should see all tests pass.

## 2. Crawl the complete TGPSC 2026 set

```powershell
python scripts\crawl.py --source tgpsc --year 2026
```

The command prints a JSON report containing the number discovered and the status of each document.

Example shape:

```json
{
  "source": "tgpsc",
  "year": 2026,
  "discovered": 0,
  "results": []
}
```

The exact count depends on what the official website exposes at run time. **Do not hard-code a count.**

## 3. Crawl all discoverable years

```powershell
python scripts\crawl.py --source tgpsc --all-years
```

Use this only when you actually want historical ingestion; it can be much larger.

## 4. Run the API

```powershell
uvicorn app.main:app --reload
```

Open:

`http://127.0.0.1:8000/docs`

Useful endpoints:

- `GET /health`
- `GET /sources`
- `POST /crawl/tgpsc?year=2026`
- `GET /recruitments?source=tgpsc&year=2026`
- `GET /notifications?source=tgpsc&year=2026`
- `GET /notifications/{id}`
- `GET /jobs?window=open|closed|all` (public student feed)
- `GET /eligibility/notifications` (approved only)

## 5. Inspect the database

Default database:

`data/govjobs.db`

Downloaded PDFs:

`data/raw/`

Extracted text:

`data/text/`

## AI mode (Anthropic Claude Haiku 4.5)

AI is optional. Start with `LLM_ENABLED=false` and prove the crawler + extraction pipeline first — everything still routes correctly into the review queue with only the deterministic reader.

Then configure `.env`:

```env
LLM_ENABLED=true
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001
LLM_MONTHLY_USD_CAP=5.0
```

The engine sends only LLM-relevant PDF pages (age/qualification/vacancy/reservation sections), not the whole PDF. It only calls the LLM for documents the classifier has already identified as recruitment notifications with an age clause — hall tickets, results, and timetables never reach it. A persistent spend ledger (`data/llm_spend.json`) tracks month-to-date cost; a call that would exceed `LLM_MONTHLY_USD_CAP` is refused and the document falls back to deterministic-only extraction (routed to review as "flagged", never silently skipped or over-billed). AI output is stored alongside deterministic extraction and evidence, and is used only for a second-reader cross-check plus qualification *discipline* extraction — it never performs the age arithmetic or the final eligibility decision.

## Document classifier

Every discovered PDF is classified before it can reach a human: `app/extractors/classifier.py` looks for recruitment-notification markers vs. non-recruitment markers (hall ticket, admit card, answer key, result, merit list, certificate verification, etc.) and for an age clause. Only documents classified `recruitment` **and** carrying an age clause are queued for review; everything else is recorded (`review_status="skipped_non_recruitment"`) but kept out of the queue.

## Human review queue

Every extracted document gets an automated verdict (`app/services/review.py`):
- `held` — not a recruitment notification (classifier gate).
- `flagged` — a real notification where the deterministic and LLM readers disagree, or only one reader ran (e.g. LLM disabled). Needs a human tap before it's trusted.
- `verified` — both readers agree on the age range. Still queued, but with the least friction.

Review it via the API:

```
GET  /review/pending
POST /review/{document_version_id}/approve   {"decided_by": "mahi"}
POST /review/{document_version_id}/reject    {"decided_by": "mahi", "reason": "..."}
```

...or via Telegram (see below) — same effect either way, and every decision is logged in `review_decisions`. Only `approved` documents are ever exposed through `/eligibility/notifications`.

### Telegram review bot

Long-poll bot, no webhook or open port needed. Configure `.env`:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your_bot_token   # from @BotFather
TELEGRAM_CHAT_ID=your_chat_id       # the bot only ever talks to this chat
```

Run the always-on agent (crawls on a timer, pushes review cards with inline Approve/Reject buttons):

```powershell
python scripts\agent.py --source tgpsc --year 2026
```

Or a single crawl-and-push cycle without the polling loop:

```powershell
python scripts\agent.py --source tgpsc --year 2026 --once
```

## Eligibility checker (the product)

First run (`scripts/run.sh` / `scripts/run.ps1`) seeds 3 reviewed demo jobs so `/app/` is not empty; live crawl is separate. A single-file, framework-free web app at `webapp/eligibility-checker.html`. It fetches `/eligibility/notifications` (approved notifications only), computes each candidate's age on that notification's own reckoning date, applies that notification's own relaxation rules, and checks qualification level/discipline — never a global rule table. Every card shows a confidence label (`good`/`partial`/`placeholder`), and low-confidence data is called out for manual verification rather than presented as a clean answer.

Served same-origin by the API — just open:

```
http://127.0.0.1:8000/app/
```

If served elsewhere, pass `?api=http://host:port` (persisted in `localStorage`).

## Admin dashboard (multi-site scheduling, AI web search, manual scrape, live log)

Open (server must be running via `uvicorn app.main:app`):

```
http://127.0.0.1:8000/app/admin.html
```

- **Site crawl** — pick which registered adapters to crawl (TGPSC, APPSC, ...), toggle the schedule on/off, set the interval/year, without restarting the server. Saved to `data/admin_settings.json`, read live by the background scheduler thread every cycle. "Scrape now" fires a manual crawl across the checked sites immediately (`POST /admin/crawl-now`), independent of the schedule.
- **AI web-search discovery** — a separate, broader mechanism: Claude (Sonnet 5, using the `web_search` server tool) searches the open web for fresh notifications and hands back candidate URLs, which go through the exact same download → classify → extract → review pipeline as an adapter (`app/services/web_discovery.py`). This is what covers boards without a listing-page adapter, including JS-only sites a plain scraper can't parse (SSC, RRB). Off by default (`WEB_DISCOVERY_ENABLED=false` in `.env` — a different cost profile than per-document extraction, so it has its own budget cap and its own schedule/query list, editable from the dashboard). **Not yet tested against the live Anthropic API** — no key was exchanged in this session (see the AI mode section above); the code is written strictly per Anthropic's documented `web_search_20260209` tool shape, but verify it once a key is in place.
- **Live activity log** — every crawl start/finish, per-document download/process/error, LLM call (with cost), web-search query (with cost), review decision, and Telegram push, each with an exact timestamp. Filterable by event type, auto-refreshes every 4s. Backed by the `event_log` table (`GET /admin/logs`) and also written to a rotating file at `data/logs/app.log` for `tail`/`grep`.
- **Review queue** — approve/reject straight from the dashboard, same effect as the API or Telegram.

The scheduler (both the site-crawl loop and the web-discovery loop) and, if configured, the Telegram poller all start automatically when the API process starts — `scripts/agent.py` is still available as a standalone CLI alternative (now a thin wrapper around the same `app/services/scheduler.py`), but running `uvicorn app.main:app` is the one thing that needs to stay running for everything (API + web app + admin + scheduler + Telegram) to work.

**Admin auth:** HTTP Basic via `ADMIN_USER` / `ADMIN_PASS` in `.env` (see `.env.example`). Protects `/admin/*`, `/review/*`, `GET /leads`, `POST /crawl/{source}`, and `/app/admin.html`. An empty `ADMIN_PASS` fails closed (401) — admin is locked until you set a password. The admin UI prompts once and keeps credentials in `sessionStorage` only.

## Lead capture (the revenue mechanism)

The eligibility checker's "Notify me on WhatsApp" form posts to `POST /leads` (requires explicit `consent: true`). Captured leads — WhatsApp number, district, category, qualification — are queryable via `GET /leads?district=...&category=...` for selling to coaching centres.

## Important production rule

The official government document is the source of truth. Never publish a field without retaining its source document and page/evidence. If extraction is ambiguous, route it to a review queue instead of guessing.

## Current source

TGPSC official notifications page:
https://websitenew.tgpsc.gov.in/notifications

## Architecture

Official source -> source adapter -> year discovery -> document download -> SHA-256 versioning -> PDF text/OCR -> section detection -> deterministic extraction -> optional LLM extraction -> evidence/validation -> database -> API.
