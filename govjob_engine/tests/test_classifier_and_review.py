from app.extractors.classifier import classify, should_review
from app.extractors.deterministic import extract_age_policy
from app.services.review import automated_verdict

RECRUITMENT_TEXT = (
    "NOTIFICATION NO. 06/G/TP/2026, DATED: 10/07/2026\n"
    "GENERAL RECRUITMENT TO THE POST OF TOWN PLANNING ASSISTANT\n"
    "Age as on \n01/07/2026 \nMin. Max. \n18–44\n"
    "AGE LIMIT: candidates must be within the prescribed age limit.\n"
)

HALL_TICKET_TEXT = "HALL TICKET\nDownload your hall ticket for the written examination.\n"

# Admit cards often mention the parent recruitment — must still be held out.
ADMIT_CARD_TEXT = (
    "ADMIT CARD\n"
    "for the GENERAL RECRUITMENT TO THE POST OF ASSISTANT\n"
    "Download your admit card for the written examination.\n"
)

SCRUTINY_TEXT = (
    "SCRUTINY OF APPLICATIONS\n"
    "List of candidates called for document scrutiny.\n"
)

ANSWER_KEY_TEXT = (
    "ANSWER KEY\n"
    "Provisional answer key for the written examination.\n"
)

RESULT_TEXT = (
    "RESULT NOTIFICATION\n"
    "Declaration of results for the written examination.\n"
)


def test_classify_recruitment_notification():
    pages = [{"page": 1, "text": RECRUITMENT_TEXT}]
    c = classify(pages, title="06/G/TP/2026 - GENERAL RECRUITMENT")
    assert c["label"] == "recruitment"
    assert c["has_age_clause"] is True
    assert should_review(c) is True


def test_classify_hall_ticket_is_held_out():
    pages = [{"page": 1, "text": HALL_TICKET_TEXT}]
    c = classify(pages, title="Hall Ticket for Group-III")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False


def test_classify_admit_card_title_never_pending():
    pages = [{"page": 1, "text": "Download instructions for candidates."}]
    c = classify(pages, title="Admit Card for Group-I Services 2026")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False


def test_classify_admit_card_body_beats_recruitment_mention():
    """Admit card PDFs that say 'for the recruitment of …' must still be held."""
    pages = [{"page": 1, "text": ADMIT_CARD_TEXT}]
    c = classify(pages, title="Admit Card - Assistant")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False
    v = automated_verdict({"deterministic": {}, "llm": None}, c)
    assert v["verdict"] == "held"


def test_classify_scrutiny_title_held_out():
    pages = [{"page": 1, "text": SCRUTINY_TEXT}]
    c = classify(pages, title="Scrutiny of applications — Group II")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False


def test_classify_answer_key_held_out():
    pages = [{"page": 1, "text": ANSWER_KEY_TEXT}]
    c = classify(pages, title="Provisional Answer Key")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False


def test_classify_result_held_out():
    pages = [{"page": 1, "text": RESULT_TEXT}]
    c = classify(pages, title="Result Notification 2026")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False


def test_classify_listing_document_type_admit_card():
    pages = [{"page": 1, "text": RECRUITMENT_TEXT}]  # body looks like recruitment
    c = classify(pages, title="Something bland", document_type="admit_card")
    assert c["label"] == "non_recruitment"
    assert should_review(c) is False


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


def test_recruitment_body_mentioning_scrutiny_still_recruitment():
    """Real notifications often say applications will undergo scrutiny — must not hold."""
    text = (
        "GENERAL RECRUITMENT TO THE POST OF SCIENTIST\n"
        "AGE LIMIT: 18 to 35 years. Age as on 01/07/2026.\n"
        "Applications will undergo scrutiny before shortlisting.\n"
    )
    pages = [{"page": 1, "text": text}]
    c = classify(pages, title="Recruitment to the posts of Scientist/Engineer")
    assert c["label"] == "recruitment"
    assert should_review(c) is True


def test_recruitment_body_result_of_not_held():
    text = (
        "RECRUITMENT TO THE POSTS OF JUNIOR RESEARCH FELLOW\n"
        "AGE LIMIT as on 01/01/2026. Minimum age 21. Maximum age 28.\n"
        "Selection will be made as a result of interview.\n"
    )
    pages = [{"page": 1, "text": text}]
    c = classify(pages, title="Recruitment to the posts of Junior Research Fellow (JRF)")
    assert c["label"] == "recruitment"
    assert should_review(c) is True
