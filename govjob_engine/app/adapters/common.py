import re

# Some .gov.in sites (e.g. IBPS) send an incomplete certificate chain that
# Python's bundled certifi list can't complete, even though browsers and curl
# resolve it fine via the OS trust store. Inject that store into every ssl
# context in this process instead of disabling verification.
import truststore
truststore.inject_into_ssl()

YEAR_RE = re.compile(r"(?:^|[^0-9])(20\d{2})(?:[^0-9]|$)")
NOTIF_RE = re.compile(r"\b(\d{1,3}\s*/\s*[^\s,;]+\s*/\s*20\d{2})\b", re.I)

def year_from(text: str) -> int | None:
    years = [int(x) for x in YEAR_RE.findall(text or "")]
    return years[-1] if years else None

def doc_type_from(text: str) -> str:
    t = (text or "").lower()
    if any(x in t for x in ["corrigendum", "corrigenda", "addendum", "amendment"]): return "corrigendum"
    if "extension" in t and "last date" in t: return "extension"
    if any(x in t for x in [
        "result", "provisional selection", "selection list", "merit list",
        "shortlist", "shortlisted", "cut-off", "cutoff", "cut off",
        "list of candidates", "consolidated list", "publishing report", "provisional list",
    ]): return "result"
    if any(x in t for x in [
        "certificate verification", "verification of certificates", "document verification",
        "document scrutiny", "scrutiny of", "scrutiny",
        "medical examination", "cbat", "cbt schedule",
    ]): return "verification"
    if any(x in t for x in ["hall ticket", "admit card"]): return "admit_card"
    if any(x in t for x in ["key", "answer key"]): return "answer_key"
    return "notification"

def notification_number_from(text: str) -> str | None:
    m = NOTIF_RE.search(text or "")
    return re.sub(r"\s+", "", m.group(1)) if m else None
