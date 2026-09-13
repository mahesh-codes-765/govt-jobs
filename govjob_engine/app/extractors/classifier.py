import re

# A document is only worth a human's review time if it is an actual
# recruitment notification carrying age/eligibility rules. Hall tickets,
# admit cards, scrutiny lists, answer keys, exam timetables, viva schedules
# and results share the same listing page and the same "notification"-looking
# title text, so title alone is not enough — we classify off title + PDF text.
#
# Rule: admit cards / hall tickets / scrutiny / answer keys / results must
# NEVER enter pending_review. Identify them primarily by listing title and
# document_type. Body markers that also appear inside real recruitment PDFs
# ("final result will be…", "applications will undergo scrutiny") must NOT
# alone hold out a document that clearly matches a recruitment marker.

NON_RECRUITMENT = re.compile(
    r"\b(?:HALL\s*TICKET|ADMIT\s*CARD|TIME\s*TABLE|TIMETABLE|VIVA[- ]?VOCE|"
    r"OMR\s+SHEET|ANSWER\s+KEY|PROVISIONAL\s+(?:KEY|SELECTION)|MERIT\s+LIST|"
    r"SELECTION\s+LIST|RESULT\s+NOTIFICATION|FINAL\s+RESULT|"
    r"DECLARATION\s+OF\s+RESULTS?|RESULTS?\s+(?:OF|FOR|DECLARED)|"
    r"CERTIFICATE\s+VERIFICATION|DOCUMENT\s+SCRUTINY|SCRUTINY\s+OF\s+APPLICATIONS|"
    r"SCRUTINY|CALL\s+LETTER|EXAMINATION\s+SCHEDULE|INTERVIEW\s+SCHEDULE)\b",
    re.I,
)

# Body-leading identity: if the PDF opens as an admit card / hall ticket /
# answer key, hold it out even when it mentions the parent recruitment.
BODY_IDENTITY_NON_RECRUITMENT = re.compile(
    r"\b(?:HALL\s*TICKET|ADMIT\s*CARD|ANSWER\s+KEY|RESULT\s+NOTIFICATION|"
    r"DECLARATION\s+OF\s+RESULTS?|SCRUTINY\s+OF\s+APPLICATIONS)\b",
    re.I,
)

NON_RECRUITMENT_DOC_TYPES = frozenset({
    "admit_card",
    "answer_key",
    "result",
    "verification",
})

RECRUITMENT = re.compile(
    r"\b(GENERAL\s+RECRUITMENT|DIRECT\s+RECRUITMENT|LATERAL\s+RECRUITMENT|"
    r"NOTIFICATION\s+FOR\s+(GENERAL\s+)?RECRUITMENT|"
    r"COMMON\s+RECRUITMENT\s+PROCESS|"
    r"RECRUITMENT\s+TO\s+THE\s+POSTS?\s+OF|"
    r"RECRUITMENT\s+(OF|FOR)\s+(THE\s+)?(POSTS?|VARIOUS\s+POSTS)|"
    r"CENTRALIZED\s+(EMPLOYMENT\s+)?NOTIFICATION|"
    r"EMPLOYMENT\s+NOTIFICATION|CEN\s+(NO\.?\s*)?\d)\b",
    re.I,
)

AGE_CLAUSE = re.compile(
    r"\b(AGE\s+LIMIT|AGE\s+AS\s+ON|LOWER\s+AGE|UPPER\s+AGE|MINIMUM\s+AGE|"
    r"MAXIMUM\s+AGE|SUPERANNUATION|AGE\s+NOT\s+EXCEEDING|"
    r"AGE\s+SHOULD\s+NOT\s+EXCEED|UPTO\s+THE\s+AGE\s+OF|AGE\s+CRITERIA)\b",
    re.I,
)


def classify(pages: list[dict], title: str = "", document_type: str | None = None) -> dict:
    """Decide whether a discovered document is a recruitment notification
    worth routing to the human review queue.

    Returns {"label": "recruitment"|"non_recruitment"|"uncertain",
             "has_age_clause": bool, "reasons": [...]}
    """
    # A short TGPSC/APPSC-style notification states eligibility on page 1-2,
    # but a longer, formally-structured one (verified live: a 76-page IBPS
    # notification with its own table of contents) can put the actual "Age"
    # clause as late as page 7. This is pure regex over already-extracted
    # text — no LLM cost — so scanning generously is cheap; capped only to
    # bound pathological documents (an 85-page SSC PDF was seen live).
    text = " ".join(p.get("text", "") for p in pages[:40])
    haystack = f"{title}\n{text}"
    # First page head — used to spot documents that *are* admit cards etc.
    first_page = (pages[0].get("text", "") if pages else "")[:800]

    reasons = []
    age_hit = AGE_CLAUSE.search(haystack)

    dtype = (document_type or "").strip().lower()
    if dtype in NON_RECRUITMENT_DOC_TYPES:
        reasons.append(f"listing document_type={dtype!r} is non-recruitment")
        return {"label": "non_recruitment", "has_age_clause": bool(age_hit), "reasons": reasons}

    # Title markers always win — listing pages often label admit cards clearly.
    title_non = NON_RECRUITMENT.search(title or "")
    if title_non:
        reasons.append(f"title matched non-recruitment marker: {title_non.group(0)!r}")
        return {"label": "non_recruitment", "has_age_clause": bool(age_hit), "reasons": reasons}

    # PDF opens as admit card / hall ticket / answer key / result / scrutiny list.
    head_id = BODY_IDENTITY_NON_RECRUITMENT.search(first_page)
    if head_id:
        reasons.append(f"document opens as non-recruitment: {head_id.group(0)!r}")
        return {"label": "non_recruitment", "has_age_clause": bool(age_hit), "reasons": reasons}

    non_recruit_hit = NON_RECRUITMENT.search(haystack)
    recruit_hit = RECRUITMENT.search(haystack)

    # Body non-recruitment only holds out when there is no recruitment marker
    # (real notifications often mention scrutiny / final result later on).
    if non_recruit_hit and not recruit_hit:
        reasons.append(f"matched non-recruitment marker: {non_recruit_hit.group(0)!r}")
        return {"label": "non_recruitment", "has_age_clause": bool(age_hit), "reasons": reasons}

    if recruit_hit:
        reasons.append(f"matched recruitment marker: {recruit_hit.group(0)!r}")
        if not age_hit:
            reasons.append("no age clause found — needs manual check before trusting eligibility fields")
            return {"label": "uncertain", "has_age_clause": False, "reasons": reasons}
        return {"label": "recruitment", "has_age_clause": True, "reasons": reasons}

    reasons.append("no recruitment or non-recruitment marker matched")
    return {"label": "uncertain", "has_age_clause": bool(age_hit), "reasons": reasons}


def should_review(classification: dict) -> bool:
    """Only documents classified as recruitment notifications with an age
    clause reach the human review queue; everything else is recorded but
    kept out of the queue so it doesn't get flooded with hall tickets."""
    return classification["label"] == "recruitment" and classification["has_age_clause"]
