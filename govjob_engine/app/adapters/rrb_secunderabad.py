import re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from app.config import settings
from . import common
from .base import SourceAdapter, DiscoveredDocument

DATE_RE = re.compile(r"Date\s*:\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
# RRB's feed is a running activity log for every CEN ever issued, not a
# board of new openings — for one live CEN it posts FAQs, exam-schedule
# notices, reschedules, appeal notices, shortlists, DV/medical updates and
# results, and those vastly outnumber the one PDF that's an actual new
# notification. Excluding known-noise doc types (as TGPSC/APPSC do) still
# leaves hundreds of these because "corrigendum" is checked before "result"
# in common.doc_type_from, so "Corrigendum to Provisional Results" comes
# back typed as corrigendum, not result. A positive match on the phrase RRB
# actually uses for a brand-new notification ("Detailed Centralized
# (Employment) Notification") is far more reliable than trying to blocklist
# every status-update phrasing. A corrigendum only stays in if it touches
# the notification/vacancies itself (not a correction to already-published
# results) — those can genuinely change who's eligible.
NEW_NOTIFICATION_RE = re.compile(r"detailed\s+centrali[sz]ed", re.I)
CORRIGENDUM_RELEVANT_RE = re.compile(r"vacanc|notification|advertisement", re.I)
RESULT_NOISE_RE = re.compile(r"result", re.I)


class RRBSecunderabadAdapter(SourceAdapter):
    """Railway Recruitment Board, Secunderabad. The unified rrbapply.gov.in
    portal is a JS single-page app plain requests can't render, so this
    targets a regional RRB's still-static WordPress site instead — the same
    CEN (Centralized Employment Notice) notifications get mirrored across
    every RRB regional site, just relevant to different zones. Verified live."""
    key = "rrb_secunderabad"
    name = "Railway Recruitment Board, Secunderabad"
    listing_url = "https://rrbsecunderabad.gov.in/"
    allowed_host = "rrbsecunderabad.gov.in"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})

    def _get(self, url):
        r = self.session.get(url, timeout=settings.request_timeout)
        r.raise_for_status()
        return r

    def discover(self, year: int | None = None, all_years: bool = False):
        try:
            r = self._get(self.listing_url)
        except Exception:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        found = {}
        for h4 in soup.find_all("h4", class_="mt-2"):
            # The title is the h4's own text; the language links ("English",
            # "Hindi") are inline <a> children whose text isn't part of it —
            # direct (non-recursive) text nodes only pull the title itself.
            title_parts = [t.strip() for t in h4.find_all(string=True, recursive=False) if t.strip()]
            title = " ".join(title_parts) or h4.get_text(" ", strip=True)
            if not title:
                continue
            date_p = h4.find_next_sibling("p")
            date_text = date_p.get_text(" ", strip=True) if date_p else ""
            m = DATE_RE.search(date_text)
            doc_year = int(m.group(3)) if m else common.year_from(title)
            doc_type = common.doc_type_from(title)
            is_new_notification = bool(NEW_NOTIFICATION_RE.search(title))
            is_relevant_corrigendum = (
                doc_type == "corrigendum"
                and CORRIGENDUM_RELEVANT_RE.search(title)
                and not RESULT_NOISE_RE.search(title)
            )
            if not (is_new_notification or is_relevant_corrigendum):
                continue
            for a in h4.find_all("a", href=True):
                href = urljoin(self.listing_url, a["href"].strip())
                if ".pdf" not in href.lower():
                    continue
                host = urlparse(href).netloc
                if host and host != self.allowed_host:
                    continue
                if year is not None and doc_year != year:
                    continue
                if year is None and not all_years:
                    continue
                num = common.notification_number_from(title)
                found[href] = DiscoveredDocument(
                    self.key, title, href, num, None, doc_year, doc_type, num or f"url:{href}",
                )
        return list(found.values())
