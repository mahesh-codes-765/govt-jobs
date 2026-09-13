from app.extractors.classifier import classify
from app.extractors.deterministic import extract_age_policy
from app.services.review import automated_verdict

RECRUITMENT_TEXT = (
    "NOTIFICATION NO. 06/G/TP/2026, DATED: 10/07/2026\n"
    "GENERAL RECRUITMENT TO THE POST OF TOWN PLANNING ASSISTANT\n"
    "Age as on \n01/07/2026 \nMin. Max. \n18–44\n"
    "AGE LIMIT: candidates must be within the prescribed age limit.\n"
)

HALL_TICKET_TEXT = "HALL TICKET\nDownload your hall ticket for the written examination.\n"


def test_classify_recruitment_notification():
    pages = [{"page": 1, "text": RECRUITMENT_TEXT}]
    c = classify(pages, title="06/G/TP/2026 - GENERAL RECRUITMENT")
    assert c["label"] == "recruitment"
    assert c["has_age_clause"] is True


def test_classify_hall_ticket_is_held_out():
    pages = [{"page": 1, "text": HALL_TICKET_TEXT}]
    c = classify(pages, title="Hall Ticket for Group-III")
    assert c["label"] == "non_recruitment"


def test_age_policy_extracts_table_layout_ages():
    pages = [{"page": 2, "text": RECRUITMENT_TEXT}]
    policy = extract_age_policy(pages)
    assert policy["min_age"] == 18
    assert policy["max_age"] == 44
    assert policy["as_on_date"] == "01/07/2026"


def test_verdict_held_for_hall_ticket():
    classification = {"label": "non_recruitment", "has_age_clause": False, "reasons": ["hall ticket"]}
    v = automated_verdict({"deterministic": {}, "llm": None}, classification)
    assert v["verdict"] == "held"


def test_verdict_flagged_when_llm_missing():
    classification = {"label": "recruitment", "has_age_clause": True, "reasons": []}
    merged = {"deterministic": {"age_policy": {"min_age": 18, "max_age": 44}}, "llm": None}
    v = automated_verdict(merged, classification)
    assert v["verdict"] == "flagged"


def test_verdict_flagged_when_readers_disagree():
    classification = {"label": "recruitment", "has_age_clause": True, "reasons": []}
    merged = {
        "deterministic": {"age_policy": {"min_age": 18, "max_age": 44}},
        "llm": {"age_rules": [{"min_age": 21, "max_age": 30}]},
    }
    v = automated_verdict(merged, classification)
    assert v["verdict"] == "flagged"


def test_verdict_verified_when_readers_agree():
    classification = {"label": "recruitment", "has_age_clause": True, "reasons": []}
    merged = {
        "deterministic": {"age_policy": {"min_age": 18, "max_age": 44}},
        "llm": {"age_rules": [{"min_age": 18, "max_age": 44}]},
    }
    v = automated_verdict(merged, classification)
    assert v["verdict"] == "verified"
