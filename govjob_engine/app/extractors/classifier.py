import re

# A document is only worth a human's review time if it is an actual
# recruitment notification carrying age/eligibility rules. Hall tickets,
# exam timetables, viva schedules and results share the same listing page
# and the same "notification"-looking title text, so title alone is not
# enough — we classify off the extracted PDF text.

NON_RECRUITMENT = re.compile(
    r"\b(HALL\s*TICKET|ADMIT\s*CARD|TIME\s*TABLE|TIMETABLE|VIVA[- ]?VOCE|"
    r"OMR\s+SHEET|ANSWER\s+KEY|PROVISIONAL\s+(KEY|SELECTION)|MERIT\s+LIST|"
    r"SELECTION\s+LIST|RESULT\s+NOTIFICATION|CERTIFICATE\s+VERIFICATION|"
    r"CALL\s+LETTER|EXAMINATION\s+SCHEDULE|INTERVIEW\s+SCHEDULE)\b",
    re.I,
)

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


def classify(pages: list[dict], title: str = "") -> dict:
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

    reasons = []
    non_recruit_hit = NON_RECRUITMENT.search(haystack)
    recruit_hit = RECRUITMENT.search(haystack)
    age_hit = AGE_CLAUSE.search(haystack)

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
