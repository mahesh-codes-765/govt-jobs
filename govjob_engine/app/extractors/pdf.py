import re
from pathlib import Path
import fitz

KEYWORDS = [
    "AGE", "EDUCATIONAL QUALIFICATIONS", "QUALIFICATION", "VACANC", "RESERVATION",
    "LOCAL CANDIDATE", "DISTRICT", "BC-A", "BC-B", "BC-C", "BC-D", "BC-E",
    "SC", "ST", "EWS", "APPLICATION", "IMPORTANT DATES", "FEE", "SCALE OF PAY",
    "COMMUNITY", "ROSTER", "MULTIPLE ZONES"
]
# Word-boundary regex per keyword — a bare substring check (the old
# approach) makes short keywords like "SC"/"ST"/"FEE" match almost any
# page (DESCRIPTION, ASSOCIATE, SCALE, ...), which defeats the point of
# only sending "relevant" pages to the LLM and can blow up cost/response
# size on large documents.
_KEYWORD_RE = [(k, re.compile(r"\b" + re.escape(k) + r"\b")) for k in KEYWORDS]

# Hard cap regardless of how many pages match — protects both LLM cost
# and response size (a too-large extraction reliably gets truncated by
# max_tokens and comes back as invalid JSON) on big, keyword-dense PDFs.
MAX_RELEVANT_PAGES = 20

def extract_pdf(pdf_path: str, text_path: str):
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        pages.append({"page": i + 1, "text": text})
    Path(text_path).write_text("\n\n".join(f"===== PAGE {p['page']} =====\n{p['text']}" for p in pages), encoding="utf-8")
    return len(doc), pages

def relevant_pages(pages, max_pages: int = MAX_RELEVANT_PAGES):
    scored = []
    for p in pages:
        upper = p["text"].upper()
        hits = [k for k, pattern in _KEYWORD_RE if pattern.search(upper)]
        if hits:
            scored.append({**p, "hits": hits})
    scored.sort(key=lambda p: len(p["hits"]), reverse=True)
    result = scored[:max_pages]
    result.sort(key=lambda p: p["page"])
    return result
