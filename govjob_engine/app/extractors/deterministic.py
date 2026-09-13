import re
from datetime import datetime

DATE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})"

def parse_date(raw):
    if not raw: return None
    raw = raw.replace("-", "/")
    for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
        try: return datetime.strptime(raw, fmt)
        except ValueError: pass
    return None

def evidence_page(pages, needle):
    for p in pages:
        if needle and needle.lower() in p["text"].lower():
            return p["page"], p["text"][:3000]
    return (1, pages[0]["text"][:3000] if pages else None)

def extract_common(pages):
    first = pages[0]["text"] if pages else ""
    out = {}
    m = re.search(r"NOTIFICATION\s+NO\.?\s*[:\-]?\s*([^,\n]+?)\s*,?\s*DATED\s*[:\-]?\s*" + DATE, first, re.I)
    if m:
        out["notification_number"] = m.group(1).strip()
        out["notification_date"] = m.group(2)
    else:
        m = re.search(r"NOTIFICATION\s+NO\.?\s*[:\-]?\s*([^\n]+)", first, re.I)
        if m: out["notification_number"] = m.group(1).strip()
    patterns = [
        r"GENERAL\s+RECRUITMENT\s+TO\s+THE\s+POST(?:S)?\s+OF\s+(.+?)(?:\n|\r)",
        r"NOTIFICATION\s+FOR\s+GENERAL\s+RECRUITMENT\s+(.+?)(?:\n|\r)",
    ]
    for pat in patterns:
        m = re.search(pat, first, re.I | re.S)
        if m:
            out["post_title"] = " ".join(m.group(1).split()); break
    m = re.search(r"Submission\s+of\s+Online\s+Application\s+From\s*" + DATE, first, re.I)
    if m: out["application_start"] = m.group(1)
    m = re.search(r"Last\s+Date.*?(?:Online\s+Application|submission).*?" + DATE + r"(?:\s+at\s+([0-9: ]+(?:AM|PM)))?", first, re.I | re.S)
    if m:
        out["application_end"] = m.group(1)
        if m.group(2): out["application_end_time"] = m.group(2).strip()
    # Generic age range evidence across the document.
    ages=[]
    for p in pages:
        for m in re.finditer(r"(?:AGE|age)[^\n]{0,120}?\b(\d{1,2})\s*(?:to|-|–)\s*(\d{1,2})\b", p["text"]):
            ages.append({"page":p["page"],"min_age":int(m.group(1)),"max_age":int(m.group(2)),"evidence":m.group(0)[:500]})
    if ages: out["age_candidates"] = ages[:30]
    out["age_policy"] = extract_age_policy(pages)
    return out

RELAXATION_PATTERNS = [
    # Patterns run against whitespace-NORMALIZED text (newlines collapsed
    # to spaces) — table cells routinely extract as "category\nnumber"
    # or even split a single category name across several lines (e.g.
    # "Telangana\nState\nGovernment\nEmployees"), so a same-line-only
    # window never matches a real table. Category phrasing also varies
    # ("SC/ST/BCs & EWS" vs "SC, ST, BC and EWS" etc.) — kept loose.
    ("SC/ST/BC/EWS", r"\bSC\s*/\s*ST\s*/\s*BCs?\b(?:\s*(?:&|AND|,)\s*EWS)?.{0,180}?\b(\d{1,2})\s*years?\b"),
    ("PWD", r"\b(?:PH|PWD|PERSONS?\s+WITH\s+DISABILIT(?:Y|IES)|PHYSICALLY\s+(?:CHALLENGED|HANDICAPPED)(?:\s+PERSONS?)?)\b.{0,180}?\b(\d{1,2})\s*years?\b"),
    ("EX_SERVICEMEN", r"\bEX[- ]?SERVICEMEN\b.{0,180}?\b(\d{1,2})\s*years?\b"),
    ("GOVT_EMPLOYEE", r"\b(?:STATE\s+)?GOVERNMENT\s+EMPLOYEES?\b.{0,180}?\b(\d{1,2})\s*years?\b"),
]

AS_ON_RE = re.compile(r"\bAS\s+ON\s*[:\-]?\s*" + DATE, re.I)
# The number can land either side of the keyword in real notifications —
# "crossed 61 years of age (Superannuation age)" as well as "Superannuation
# age: 61 years" — check both directions.
SUPERANNUATION_RE = re.compile(
    r"(?:\b(\d{1,2})\s*years?\s*(?:of\s+age\s*)?\(?\s*SUPERANNUATION|SUPERANNUATION[^\n]{0,60}?\b(\d{1,2})\b)",
    re.I,
)
BASE_AGE_RE = re.compile(r"\b(?:MINIMUM|LOWER)\s+AGE\b[^\n]{0,60}?\b(\d{1,2})\b.{0,200}?\b(?:MAXIMUM|UPPER)\s+AGE\b[^\n]{0,60}?\b(\d{1,2})\b", re.I | re.S)
# Fallback for "Age as on <date> / Min. Max." table layouts where the two
# numbers land far from any AGE keyword after PDF text extraction — the
# separator glyph (en-dash, em-dash, or a mis-decoded replacement char)
# varies by document, so match on two plausible ages either side of it.
AGE_PAIR_RE = re.compile(r"\b(1[4-9]|[2-6][0-9])\s*[-–—�]\s*(1[4-9]|[2-6][0-9])\b")

def extract_age_policy(pages):
    """Deterministic, per-notification age policy: base range, the
    reckoning ('as on') date age is computed against, category
    relaxations, and superannuation cap. Every field carries its own
    page+evidence so nothing here is a global assumption — Telangana's
    G.O.Ms.No.42 proved a global relaxation table gives thousands of
    people the wrong answer.

    Two passes over the base age range, deliberately: a clean prose
    statement ("minimum age (18 years) ... maximum age (44 years)") is
    far more trustworthy than the "Age as on <date> / Min. Max." table
    fallback, but the two can appear on either page in either order —
    scan every page for the strong pattern first, and only reach for the
    weaker table heuristic if no page had one."""
    policy = {"as_on_date": None, "min_age": None, "max_age": None, "superannuation_age": None, "relaxations": []}

    for p in pages:
        if policy["as_on_date"] is not None:
            break
        m = AS_ON_RE.search(p["text"])
        if m:
            policy["as_on_date"] = m.group(1)
            policy["as_on_page"] = p["page"]
            policy["as_on_evidence"] = m.group(0)[:300]

    for p in pages:
        m = BASE_AGE_RE.search(p["text"])
        if m:
            policy["min_age"] = int(m.group(1))
            policy["max_age"] = int(m.group(2))
            policy["base_age_page"] = p["page"]
            policy["base_age_evidence"] = m.group(0)[:300]
            break

    if policy["min_age"] is None:
        for p in pages:
            as_on_match = AS_ON_RE.search(p["text"])
            if not as_on_match:
                continue
            window = p["text"][as_on_match.end(): as_on_match.end() + 800]
            pair = AGE_PAIR_RE.search(window)
            if pair and int(pair.group(1)) < int(pair.group(2)):
                policy["min_age"] = int(pair.group(1))
                policy["max_age"] = int(pair.group(2))
                policy["base_age_page"] = p["page"]
                policy["base_age_evidence"] = pair.group(0)[:300]
                policy["base_age_low_confidence"] = True
                break

    for p in pages:
        if policy["superannuation_age"] is not None:
            break
        m = SUPERANNUATION_RE.search(p["text"])
        if m:
            policy["superannuation_age"] = int(m.group(1) or m.group(2))

    for p in pages:
        normalized = re.sub(r"\s+", " ", p["text"])
        for category, pattern in RELAXATION_PATTERNS:
            if any(r["category"] == category for r in policy["relaxations"]):
                continue
            m = re.search(pattern, normalized, re.I)
            if m:
                policy["relaxations"].append({
                    "category": category, "years": int(m.group(1)),
                    "page": p["page"], "evidence": m.group(0)[:300],
                })

    has_solid_age = policy["min_age"] is not None and not policy.get("base_age_low_confidence")
    if has_solid_age and policy["as_on_date"] is not None and policy["relaxations"]:
        policy["confidence"] = "good"
    elif policy["min_age"] is not None or policy["as_on_date"] is not None:
        policy["confidence"] = "partial"
    else:
        policy["confidence"] = "placeholder"
    return policy
