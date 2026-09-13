import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "logs"
LOG_FILE = LOG_DIR / "app.log"

_configured = False

def setup_logging():
    """Rotating file (data/logs/app.log, 5 x 2MB) plus console. Idempotent —
    safe to call from both the API process and standalone scripts without
    double-attaching handlers.

    Log messages routinely contain non-ASCII punctuation (em dashes, arrows).
    A default Windows console is cp1252 and raises UnicodeEncodeError on
    those — the exact failure mode that bit an earlier version of this
    project's tooling. Force stdout/stderr to UTF-8 (Python 3.7+) so a
    stray dash never takes the process down."""
    global _configured
    if _configured:
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    _configured = True
