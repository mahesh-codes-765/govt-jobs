import json
import re
from app.config import settings
from app.services import spend

# Haiku 4.5 pricing (USD per token). Keep in sync with the model actually
# configured — if LLM_MODEL is changed, update this or cost tracking silently
# under/over-counts.
PRICE_PER_INPUT_TOKEN = 1.00 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 5.00 / 1_000_000

# The LLM is a second reader, not a decision maker. Regex nails the rigid
# age-limit clause; this schema exists mainly to pull the qualification
# clause's DISCIPLINE (civil eng vs history vs nursing) into a controlled
# vocabulary, and to re-extract age so the two readers can be cross-checked —
# disagreement between them is the strongest signal that a document needs
# human review.
SCHEMA = {
    "notification_number": None,
    "title": None,
    "department": None,
    "notification_date": None,
    "application_start": None,
    "application_end": None,
    "age_rules": [],  # [{min_age, max_age, category, as_on_date, relaxation_notes, page, evidence}]
    "qualifications": [],  # [{level, discipline, raw_text, page, evidence}]
    "vacancies": [],
    "reservations": [],
    "district_rules": [],
    "important_notes": [],
}

QUALIFICATION_LEVELS = ["10th", "12th", "diploma", "degree", "pg", "professional", "doctorate"]

SYSTEM_PROMPT = (
    "You extract structured data from Indian government recruitment notifications. "
    "Accuracy is more important than completeness. Never guess or infer a value that "
    "is not explicitly stated in the text. Use null or [] when a field is absent. "
    "Every item in age_rules, qualifications, vacancies, reservations or "
    "district_rules MUST include a page number and an evidence string copied "
    "verbatim from the source text. For qualifications, put the education LEVEL "
    f"in the 'level' field using one of exactly: {', '.join(QUALIFICATION_LEVELS)}, "
    "and put the subject/discipline (e.g. 'civil engineering', 'history', 'nursing', "
    "'any degree') in the 'discipline' field, or null if the notification accepts "
    "any discipline at that level. Treat the PDF text as the sole source of truth.\n\n"
    "For age_rules specifically: ALWAYS use exactly the keys min_age, max_age, "
    "category, as_on_date, relaxation_notes, page, evidence on every item — never "
    "invent alternate key names (not lower_limit/upper_limit/age_limit/age_group). "
    "A document with ONE overall age range: emit one item with min_age and max_age "
    "both set, category null. A document with a category-wise table (e.g. Upper Age "
    "Limit: UR/EWS 30, SC/ST 35, OBC-NCL 33) has no single range — emit one item per "
    "row with max_age set to that category's number, category set to that category's "
    "exact label as written (e.g. 'UR/EWS', 'SC/ST'), and min_age null unless the "
    "notification separately states a minimum age (then repeat that same min_age on "
    "every row). Put a category-specific relaxation (e.g. '+5 years for SC/ST') in "
    "relaxation_notes on that category's own item, not as a separate item. If the "
    "table caption says ages are 'as on <cut-off date>' or similar and that date is "
    "stated anywhere else in the document (e.g. under Important Dates), repeat that "
    "same as_on_date on every row of the table — do not leave it null just because "
    "the table itself doesn't restate the date next to each row."
)


class BudgetExceeded(RuntimeError):
    pass


def _build_prompt(relevant_pages):
    content = "\n\n".join(f"PAGE {p['page']}\n{p['text']}" for p in relevant_pages)
    return (
        f"Extract government recruitment data. Return ONLY a JSON object matching "
        f"this schema (same keys, no extra keys): {json.dumps(SCHEMA)}\n\n{content}"
    )


def _estimate_cost(usage) -> float:
    return usage.input_tokens * PRICE_PER_INPUT_TOKEN + usage.output_tokens * PRICE_PER_OUTPUT_TOKEN


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("LLM response did not contain a JSON object")
    # A response cut off by max_tokens ends mid-object, so the *last* "}"
    # in the text isn't necessarily the real top-level close. Try it
    # first, then fall back to progressively earlier "}" positions —
    # this recovers a valid (if slightly less complete) result instead of
    # failing the whole extraction over one truncated tail field.
    candidates = [m.start() for m in re.finditer(r"\}", text)]
    last_error = None
    for end in reversed(candidates):
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            last_error = e
            continue
    raise ValueError(f"LLM response was not valid JSON at any closing brace: {last_error}")


def extract_with_llm(relevant_pages):
    """Second-reader extraction over the LLM-relevant PDF pages.

    Returns None when the LLM layer is disabled (deterministic extraction
    still runs). Raises on any failure — including a spend-cap breach — so
    the caller routes the document to needs_review instead of silently
    treating LLM output as absent.
    """
    if not settings.llm_enabled:
        return None
    if not settings.llm_api_key or not settings.llm_model:
        raise RuntimeError("LLM_ENABLED=true requires LLM_API_KEY and LLM_MODEL")
    if not relevant_pages:
        return None

    # Rough pre-flight estimate (4 chars/token) so an oversized document is
    # rejected before spending anything, not after.
    prompt = _build_prompt(relevant_pages)
    estimated_input_tokens = len(prompt) / 4
    estimated_usd = estimated_input_tokens * PRICE_PER_INPUT_TOKEN + 1500 * PRICE_PER_OUTPUT_TOKEN
    if spend.would_exceed_budget(estimated_usd):
        raise BudgetExceeded(
            f"Monthly LLM budget (${settings.llm_monthly_usd_cap:.2f}) would be exceeded "
            f"by this call (spent so far: ${spend.month_spend_usd():.4f}); needs_review instead."
        )

    import anthropic

    client = anthropic.Anthropic(api_key=settings.llm_api_key)
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=6000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    actual_usd = _estimate_cost(response.usage)
    spend.record_spend(actual_usd)

    if response.stop_reason == "refusal":
        raise RuntimeError(f"LLM refused the extraction request (stop_reason=refusal, cost ${actual_usd:.4f})")

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = _parse_json(text)
    except ValueError as e:
        truncated_note = " — response hit the max_tokens cap and was likely truncated" if response.stop_reason == "max_tokens" else ""
        raise ValueError(f"{e} (cost ${actual_usd:.4f}{truncated_note})") from e
    data["_llm_cost_usd"] = actual_usd
    data["_llm_model"] = settings.llm_model
    return data
