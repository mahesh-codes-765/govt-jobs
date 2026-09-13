from app.extractors.deterministic import extract_common

def test_tgpsc_common():
    pages=[{"page":1,"text":"NOTIFICATION NO. 06/G/TP/2026, DATED: 10/07/2026\nGENERAL RECRUITMENT TO THE POST OF TOWN PLANNING ASSISTANT IN\nTOWN AND COUNTRY PLANNING DEPARTMENT\nSubmission of Online Application From 15/07/2026\nLast Date & Time of submission of Online Application 22/08/2026 at 5:00 PM"}]
    x=extract_common(pages)
    assert x['notification_number']=='06/G/TP/2026'
    assert x['application_start']=='15/07/2026'
    assert x['application_end']=='22/08/2026'
