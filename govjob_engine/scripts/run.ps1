$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path .venv)) {
  python -m venv .venv
}
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host "Created .env from .env.example — set ADMIN_PASS before using /app/admin.html"
}
# First run (missing DB or zero notifications) seeds 3 reviewed demo jobs.
# seed_demo.py refuses to overwrite a DB that already has real crawl rows.
python scripts/seed_demo.py --if-empty
uvicorn app.main:app --host 127.0.0.1 --port 8000
