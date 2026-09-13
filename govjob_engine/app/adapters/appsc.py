from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from app.config import settings
from . import common
from .base import SourceAdapter, DiscoveredDocument


class APPSCAdapter(SourceAdapter):
    """Andhra Pradesh Public Service Commission. Listing page is plain
    server-rendered HTML (a table with an <a> per notification linking
    straight to the PDF) — verified live, no JS rendering needed."""
    key = "appsc"
    name = "Andhra Pradesh Public Service Commission"
    listing_url = "https://appsc.gov.in/Index/sub_page/doc12233/Notifications"
    allowed_host = "appsc.gov.in"

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
        for a in soup.find_all("a", href=True):
            href = urljoin(self.listing_url, a["href"].strip())
            if ".pdf" not in href.lower():
                continue
            host = urlparse(href).netloc
            if host and host != self.allowed_host:
                continue
            text = " ".join(a.get_text(" ", strip=True).split())
            if not text:
                continue
            doc_year = common.year_from(text + " " + href)
            if year is not None and doc_year != year:
                continue
            if year is None and not all_years:
                continue
            num = common.notification_number_from(text)
            key = num or href
            found[key] = DiscoveredDocument(
                self.key, text, href, num, None, doc_year,
                common.doc_type_from(text), num or f"url:{href}",
            )
        return list(found.values())
