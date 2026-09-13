"""Turns the LLM reader's age_rules into a usable fallback when the
deterministic (regex) reader finds nothing at all.

Real gap this closes: boards like RRB, IBPS and CERT-In lay out age
eligibility as a category table (Upper Age Limit: UR/EWS 30, SC/ST 35, ...)
rather than TGPSC/APPSC's single "minimum age X, maximum age Y" sentence.
The regex reader is built for the sentence form and finds nothing on a
table; the LLM reader finds the table fine, but before this it was only
ever used to cross-check the regex reader, never shown on its own — real,
correctly-extracted data sat unused while the card said "not yet reliably
extracted".

This is deliberately a *fallback*, not a replacement: it only activates
when the deterministic reader found nothing, and its output is always
tagged source="llm_only" so the confidence label stays honest that a
second, independent reader never confirmed these numbers.

Documents extracted before the LLM prompt was tightened to always use
min_age/max_age/category may still carry old field names (lower_limit,
upper_limit, age_limit, age_group) — normalize() maps those too, so
existing approved-adjacent data doesn't need re-extraction to become
usable.
"""


def _as_int(v):
    """The LLM doesn't always honor the schema's numeric types (a value
    has shown up as the string "30" instead of 30) — coerce defensively
    rather than let a stray string crash every card on the page."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        digits = "".join(ch for ch in v.strip() if ch.isdigit())
        if digits and len(digits) <= 2:
            return int(digits)
    return None


def _normalize_entry(a: dict) -> dict:
    min_age = _as_int(a.get("min_age"))
    max_age = _as_int(a.get("max_age"))
    if max_age is None:
        max_age = _as_int(a.get("upper_limit"))
    if max_age is None:
        max_age = _as_int(a.get("age_limit"))
    if min_age is None:
        min_age = _as_int(a.get("lower_limit"))
    relaxation_years = _as_int(a.get("relaxation_years"))
    if relaxation_years is None:
        relaxation_years = _as_int(a.get("relaxation") or a.get("relaxation_notes"))
    return {
        "min_age": min_age,
        "max_age": max_age,
        "category": a.get("category"),
        "as_on_date": a.get("as_on_date"),
        "relaxation_years": relaxation_years,
        "page": a.get("page") or a.get("page_number"),
        "evidence": a.get("evidence"),
    }


def build_fallback(age_rules_raw: list | None) -> dict | None:
    """Returns None if there's nothing usable; otherwise a dict shaped
    like the deterministic age_policy (as_on_date, min_age, max_age,
    relaxations[]) plus category_caps for per-category ceilings, all
    tagged source="llm_only"."""
    if not age_rules_raw:
        return None
    entries = [_normalize_entry(a) for a in age_rules_raw if isinstance(a, dict)]
    if not entries:
        return None

    base = next((e for e in entries if e["min_age"] is not None and e["max_age"] is not None), None)
    as_on_date = (base or {}).get("as_on_date") or next((e["as_on_date"] for e in entries if e["as_on_date"]), None)

    category_caps = {
        e["category"]: e["max_age"]
        for e in entries
        if isinstance(e["category"], str) and e["category"] and e["max_age"] is not None
    }

    relaxations = []
    for e in entries:
        if e["relaxation_years"] and e["category"]:
            relaxations.append({
                "category": e["category"], "years": e["relaxation_years"],
                "page": e["page"], "evidence": e["evidence"],
            })

    general_cap = None
    for key, val in category_caps.items():
        if any(tok in key.upper() for tok in ("UR", "GEN", "EWS")) and "SC" not in key.upper() and "ST" not in key.upper():
            general_cap = val if general_cap is None else max(general_cap, val)
    if general_cap is None and base:
        general_cap = base["max_age"]

    if base is None and not category_caps:
        return None

    return {
        "as_on_date": as_on_date,
        "min_age": (base or {}).get("min_age"),
        "max_age": general_cap,
        "category_caps": category_caps,
        "relaxations": relaxations,
        "source": "llm_only",
    }
