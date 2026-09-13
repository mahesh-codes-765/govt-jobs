import time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from app.config import settings
from . import common
from .base import SourceAdapter, DiscoveredDocument

NOTIF_RE = common.NOTIF_RE

class TGPSCAdapter(SourceAdapter):
    key = "tgpsc"
    name = "Telangana Public Service Commission"
    listing_url = "https://websitenew.tgpsc.gov.in/notifications"
    allowed_host = "websitenew.tgpsc.gov.in"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})

    def _get(self, url):
        r = self.session.get(url, timeout=settings.request_timeout)
        r.raise_for_status()
        return r

    def _year_from(self, text):
        return common.year_from(text)

    def _doc_type(self, text):
        return common.doc_type_from(text)

    def _is_pdfish(self, href, text):
        h = href.lower()
        t = text.lower()
        return ("preview/" in h or ".pdf" in h or "pdf" in t or "notification" in t or bool(NOTIF_RE.search(text)))

    def discover(self, year: int | None = None, all_years: bool = False):
        queue = [self.listing_url]
        visited = set()
        found = {}
        pages_seen = 0
        while queue and pages_seen < settings.max_listing_pages:
            url = queue.pop(0)
            if url in visited: continue
            visited.add(url); pages_seen += 1
            try:
                r = self._get(url)
            except Exception:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                text = " ".join(a.get_text(" ", strip=True).split())
                href = urljoin(url, a["href"].strip())
                if not text or urlparse(href).netloc and urlparse(href).netloc != self.allowed_host:
                    continue
                if self._is_pdfish(href, text):
                    doc_year = self._year_from(text + " " + href)
                    if year is not None and doc_year != year: continue
                    if year is None and not all_years:
                        # default behavior is current year only; caller should pass year explicitly in production
                        continue
                    num = common.notification_number_from(text)
                    key = num or href
                    title = text
                    found[key] = DiscoveredDocument(self.key, title, href, num, None, doc_year, self._doc_type(text), num or f"url:{href}")
                # Follow obvious archive/pagination links on the same host.
                if any(x in (text + " " + href).lower() for x in ["next", "older", "previous", "page="]):
                    if href not in visited and href not in queue: queue.append(href)
            time.sleep(settings.crawl_delay_seconds)
        return list(found.values())
