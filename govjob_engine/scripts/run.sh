#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — set ADMIN_PASS before using /app/admin.html"
fi
# First run (missing DB or zero notifications) seeds 3 reviewed demo jobs.
# seed_demo.py refuses to overwrite a DB that already has real crawl rows.
python scripts/seed_demo.py --if-empty
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
