# Govt Jobs — eligibility checker

Browse ongoing government job notifications (open this month) and recently closed ones (last 6 months) across the boards we crawl — TGPSC, APPSC, RRB Secunderabad, IBPS, DRDO, ISRO, plus others via web discovery. Check your eligibility against each notification’s own age limits and category relaxation; we never invent cutoffs or age numbers.

## Quick start

```bash
cd govjob_engine && bash scripts/run.sh
```

Windows (PowerShell):

```powershell
cd govjob_engine; .\scripts\run.ps1
```

The script creates a `.venv`, installs deps, copies `.env.example` → `.env` only if `.env` is missing, then starts the API on port 8000. First run seeds 3 reviewed demo jobs so the student home is not empty; live crawl is separate.

## URLs

| URL | What |
|-----|------|
| http://127.0.0.1:8000/app/ | Eligibility checker (student product) |
| http://127.0.0.1:8000/app/admin.html | Admin dashboard (HTTP Basic) |
| http://127.0.0.1:8000/docs | OpenAPI docs |
| http://127.0.0.1:8000/health | Health check |
| http://127.0.0.1:8000/jobs | Public jobs feed (`?window=open\|closed\|all`) |

## Admin auth

Set in `govjob_engine/.env` (from `.env.example`):

```env
ADMIN_USER=admin
ADMIN_PASS=choose-a-strong-password
```

An **empty `ADMIN_PASS` locks admin** — `/admin/*`, `/review/*`, `GET /leads`, `POST /crawl/*`, and `/app/admin.html` all return 401. Do not leave admin open.

## Security

- Never commit `.env`, `data/logs/`, `*.db`, venv, or tokens.
- Telegram bot token stays local in `.env` only.
- Public routes: health, sources, recruitments, notifications, jobs, eligibility, `POST /leads`, and the eligibility HTML.

## More detail

Crawler, LLM extraction, review queue, and Telegram bot: see [govjob_engine/README.md](govjob_engine/README.md).
