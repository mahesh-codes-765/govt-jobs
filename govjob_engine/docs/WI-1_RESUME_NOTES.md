# WI-1 — Government Job Eligibility Engine — RESUME NOTES

**Status:** End-to-end pipeline built and verified live against TGPSC. Not yet earning money — no real Telegram/Anthropic credentials wired in, no real leads captured.
**Last worked:** 10 Sep 2026.
**One-line pitch:** A machine that turns AP/Telangana govt recruitment PDFs into
per-candidate eligibility answers, uses those answers to capture consented leads,
and sells the leads to coaching centres. One dollar/day floor; leads are the real money.

---

## The idea, settled

Every existing job-notification channel *announces* — reads the public PDF aloud, same
as 200 others, same hour, zero information advantage, stuck at 20k subs and no sponsor.
This tool *computes*: it answers the one question an aspirant actually has — "where do I
personally stand in this?" — which is a per-person calculation nobody does manually.

Money flow (validated in-game):
- Videos/Telegram are TOP OF FUNNEL only, never the revenue. If YouTube demonetises, income is unaffected.
- The eligibility web app is the PRODUCT.
- The lead (consented WhatsApp number tagged by district/category/qualification/exam) is the REVENUE.
- A coaching centre pays ₹50–150 per qualified lead. 300 leads/month from one buyer ≈ ₹15,000/month. Floor cleared many times over.

Human effort (the game's 10–20% cap): comes out to ~4 hours/month = ~2%. Itemised below.

---

## What exists RIGHT NOW — all live in `govjob_engine/`, verified against the real TGPSC site

Everything below lives in one codebase now (Mahi's original FastAPI/SQLAlchemy skeleton, extended). The old `/mnt/user-data/outputs/` sandbox assets (Asset A eligibility-checker.html, Asset B notifwatch/, Asset C llm_extract.py) are **superseded** — their designs were ported into this repo, not copied verbatim.

### Ingestion — `app/adapters/`, `app/services/crawler.py`
- TGPSC adapter discovers notifications via plain `requests` + BeautifulSoup, with a real browser `User-Agent`. **Verified live**: a fresh `--year 2026` crawl found all 9 currently-posted TGPSC 2026 notifications and downloaded every PDF successfully.
- **The "TGPSC bot-blocks non-browser clients" problem from the last session did not reproduce** on Mahi's machine/IP with a browser UA — Selenium turned out to be unnecessary. The crawler still fails closed (raises if a "PDF" download is actually an HTML page) so a future block would surface as an `error` status per-document, not a silent bad parse. If TGPSC starts blocking again, that's the first place to look.
- SHA-256 version hashing, `DocumentVersion` per revision — corrigenda never silently overwrite the original.

### Extraction — `app/extractors/`
- `pdf.py` — PyMuPDF text extraction, UTF-8 throughout (no Windows cp1252 issue: this engine never shells out to `pdftotext`, unlike the old notifwatch prototype).
- `deterministic.py` — regex extraction of notification number, dates, and a new `extract_age_policy()`: per-notification `as_on_date` (reckoning date), `min_age`/`max_age`, `superannuation_age`, and category `relaxations` (SC/ST/BC/EWS, PWD, ex-servicemen, govt employee), each with page + evidence. Handles both prose age clauses and the "Age as on <date> / Min. Max." table layout TGPSC actually uses in 2026 notifications (verified against real PDFs — the table numbers are often separated from the word "AGE" by hundreds of characters of table junk, so this needed a dedicated fallback regex).
- `classifier.py` — new. Distinguishes real recruitment notifications from hall tickets/admit cards/answer keys/results/timetables using text markers, and flags whether an age clause is present. Only `recruitment` + `has_age_clause` documents reach the review queue — this was open item #3 from the last session, now closed.

### LLM second reader — `app/services/llm.py`, `app/services/spend.py`
- Anthropic Claude Haiku 4.5 (`claude-haiku-4-5-20251001`), replacing the old OpenAI-shaped stub. Extracts `age_rules` (for cross-checking the deterministic reader) and `qualifications` with a `level`/`discipline` split (the discipline-matching field from the old Asset C design).
- Persistent monthly spend ledger (`data/llm_spend.json`) — a call that would exceed `LLM_MONTHLY_USD_CAP` (default $5) is refused *before* it's made, and the document falls back to deterministic-only extraction rather than silently skipping or overspending.
- **Not yet tested against the real Anthropic API** — no key was exchanged in this session (by design; add one to `.env` and re-run a crawl to verify). `LLM_ENABLED=false` is the current default and the whole pipeline was verified working in that mode.

### Human review — `app/services/review.py`, `app/models/notification.py` (`ReviewDecision`, `DocumentVersion.review_status`)
- `automated_verdict()`: `held` (not a recruitment notification — classifier gate), `flagged` (real notification but readers disagree or only one ran), `verified` (both readers agree). Unit-tested (`tests/test_classifier_and_review.py`), including the exact "clean→verified / disagree→flagged / hall-ticket→held" cases from the original design.
- `POST /review/{id}/approve` / `/reject`, `GET /review/pending` — every decision is logged, never overwritten.
- **Verified live end-to-end**: crawled real TGPSC data → all 9 notifications correctly classified and queued as `flagged` (expected, since LLM is off — only one reader ran) → approved one via the API → it appeared, and only it, in `/eligibility/notifications`.

### Telegram review bot — `app/services/telegram_review.py`, `scripts/agent.py`
- Raw long-poll HTTP (no extra dependency, no webhook/port). Inline Approve/Reject buttons, only responds to `TELEGRAM_CHAT_ID`. `scripts/agent.py` runs the crawl-scheduler thread and the Telegram long-poller thread together; `--once` runs a single cycle without the loop.
- **Not yet tested against a real bot token** — same reasoning as the LLM key. Code path is exercised by construction (mirrors the LLM/review code that *was* live-tested) but the actual Telegram round-trip needs a real `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env`.

### Eligibility checker (the product) — `webapp/eligibility-checker.html`
- Single HTML file, no framework, no build step, served same-origin at `http://127.0.0.1:8000/app/` (FastAPI mounts `webapp/` as static files). Works via `?api=` override if served separately.
- Computes candidate age **on each notification's own `as_on_date`**, applies **that notification's own relaxation rules** (never a global table — this was the core architectural lesson from Telangana's G.O.Ms.No.42 case, and it's preserved: every field lives on the `DocumentVersion`, nothing is hardcoded), checks qualification level + discipline against the LLM-extracted `qualifications` list when present.
- Every result card shows a confidence label (good/partial/placeholder); low-confidence data is called out in red for manual verification rather than presented as a clean yes/no.
- **Verified live** against the real approved notification from this session's crawl — rendering logic traced by hand against the actual returned JSON (age 18–44, as-on 01/07/2026, partial confidence since no category-specific relaxation years were stated in that particular clause).

### Lead capture backend — `POST /leads`, `GET /leads`, `Lead` model
- Was a stub with no backend last session (item #5 — the actual revenue mechanism). Now real: the eligibility checker's "Notify me on WhatsApp" form posts here with `consent: true` required; leads are queryable by district/category/qualification for selling to coaching centres.
- **Verified live**: posted a test lead through the API, confirmed it round-trips through `GET /leads`. The test lead (`+919876543210`) was deleted before ending the session — the local `data/govjobs.db` was wiped clean so Mahi starts with a real, not fake, dataset. Re-run the crawl to repopulate.

---

## CRITICAL OPEN ITEMS (do these first on resume)

1. **Add real Anthropic + Telegram credentials to `.env` and re-verify.** Both integrations are built and unit-adjacent-tested but never round-tripped against the live APIs (no keys were exchanged in this session, by design — see the credentials question at the top of this session). Get an Anthropic key and a Telegram bot token from @BotFather, drop them in `.env`, re-run `python scripts\agent.py --source tgpsc --year 2026 --once`, confirm a Telegram card arrives and tapping Approve/Reject works.
2. **Discipline/field matching is real but untested against live LLM output.** The schema and matching logic in the web app are wired (`app/services/llm.py` schema, `webapp/eligibility-checker.html` → `qualificationVerdict()`), but need item 1's key to actually see LLM-extracted disciplines flow through.
3. **Department extraction gap.** `Recruitment.department` is usually `null` — the TGPSC adapter never populates `DiscoveredDocument.department`, and the deterministic title regex doesn't reliably pull "IN X DEPARTMENT" off the end of the post title. Minor, but shows up as "Department: not extracted" on every card right now. Cheap regex fix or let the LLM's `department` field cover it once item 1 is live.
4. **Relaxation-years extraction is incomplete.** `extract_age_policy()`'s relaxation regexes only catch clauses that state "+N years" directly next to a category name; TGPSC notifications often phrase it as "candidates who avail upper age relaxation will also be considered for OC vacancies" without restating the number in that sentence (the number is usually in a separate standing G.O. reference). Real relaxation numbers mostly come from the LLM reader today — deterministic relaxation extraction is a nice-to-have, not load-bearing.
5. **First real lead → first rupee.** The +10 implementation bounty is still live. Everything needed to earn it now exists (approved notifications → public checker → consented lead capture); what's missing is real traffic (the YouTube/Telegram top-of-funnel from Round 1's pitch) and a real coaching-centre buyer conversation.
6. **If TGPSC ever starts bot-blocking again**, the crawler already fails closed (HTML-saved-as-PDF is caught and raises per-document) rather than silently corrupting data — check `item["status"]=="error"` in a crawl report first, only reach for Selenium if that's actually the failure mode.

---

## Environment / infra facts learned (this session)

- This session ran with a real network connection on Mahi's Windows machine (unlike the earlier sandboxed session) — the crawler, TGPSC site, and Anthropic API were all directly reachable and verified live where credentials existed.
- `pip install anthropic` succeeded into `.venv` with no issues; `requirements.txt` now pins `anthropic>=0.40,<1` in place of the old `openai` dependency.
- No Selenium dependency was added — see critical item 6. Keep it that way unless a real block reproduces; it's extra surface area (browser binary management) the project doesn't currently need.
- SQLite auto-creates new tables via `Base.metadata.create_all` — adding `ReviewDecision` and `Lead` needed no migration, just importing them in `app/db.py::init_db()`.
- `data/govjobs.db` and `data/llm_spend.json` are gitignored/local-only and were wiped clean at the end of this session (they only ever held test data from live verification). Re-run a crawl to repopulate real data.
- Windows console (`cp1252`) chokes on stray Unicode (→, ✅) in printed/logged text — avoided in `review.render_card()`; Telegram-bound emoji in `telegram_review.py` are fine since they go over HTTP JSON, never through the Windows console.

---

## GAME STATE
- Game: "U Suggest, I Reject." Claude pitches auto-able internet businesses; Mahi attacks with pinpoint objections. Idea survives → Claude +2/Mahi −2; killed → reverse. First to 0 loses, first to 100 wins. +10 for a working implementation that earns real money; Mahi +10 if the build fails once started.
- Refer to each other as Claude and Mahi.
- **Score: Claude 22, Mahi 18.**
- Round 1 result: Idea #1 (this eligibility engine) VALIDATED after Mahi's attacks on YouTube AI policy (defended — revenue isn't AdSense), niche (conceded, patched), card-only video (patched to data animations), voice/animation sync (defended — generate audio first, drive animation from word timestamps). Net Claude +4 across the round's scoring.
- The +10 implementation bounty is STILL LIVE — pays out when WI-1 produces a real rupee. Not yet claimed.
- Patching an idea is allowed; dissolving it into a different business to dodge a hit is not.
- When resuming: this is collaborative build territory, not pitch-defence, so don't self-award points for building. Points move only when Mahi swings at a NEW idea or WI-1 earns money.

## HARD BOUNDARY (carry forward, non-negotiable)
Mahi's VS Code has a `cookie-stalker` project open beside this one (server.py labelled cookie-stalker, "Cookie Stealer" tab, gen_payload.py, log.php, cookies_log.txt, track.html). Claude does NOT help build, debug, connect, or advise on that — it has the profile of credential/session theft. This refusal held across the session and continues. The notification engine is fully legitimate and separate; keep helping with that freely.

---

## SUGGESTED RESUME ORDER
1. Add real Anthropic + Telegram credentials to `.env`, re-run `python scripts\agent.py --source tgpsc --year 2026 --once`, confirm the Telegram card + Approve/Reject round-trip (critical item 1).
2. Re-crawl to repopulate `data/govjobs.db` with real data (it was wiped clean this session).
3. Approve a batch of real notifications (via Telegram or `POST /review/{id}/approve`), then open `http://127.0.0.1:8000/app/` and sanity-check the eligibility checker against a few real candidate profiles.
4. Fix the department-extraction gap (critical item 3) — quick regex win, makes cards look complete.
5. Get real top-of-funnel traffic pointed at the eligibility checker (the YouTube/Telegram plan from Round 1) and start a coaching-centre buyer conversation — this is what actually earns the +10, not more engineering.
6. Only after a real lead is sold: revisit deterministic relaxation-years extraction (critical item 4) if the LLM-only path proves too expensive or too slow at higher volume.
