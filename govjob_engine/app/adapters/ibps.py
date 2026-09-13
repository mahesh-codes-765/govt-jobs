import re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from app.config import settings
from . import common
from .base import SourceAdapter, DiscoveredDocument

DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})")
# Administrative housekeeping notices, not recruitment — never an eligibility
# question a candidate needs answered.
EXCLUDE_KEYWORDS = [
    "caution", "fraud", "trademark", "iso 9001", "normated standards",
    "tentative calendar", "rfp", "request for proposal",
]


class IBPSAdapter(SourceAdapter):
    """Institute of Banking Personnel Selection — runs the Common
    Recruitment Process (CRP) for public-sector banks and RRB bank posts.
    Each listing page is server-rendered (WordPress/Elementor); each notice
    is a whole-row <a> wrapping a date + title pair. Verified live — note
    this host needs the truststore SSL fix in adapters/common.py to load at
    all.

    /crp-updates is a general activity log across every CRP program and
    doesn't reliably include a still-open program's own root notification
    once it scrolls past the log's visible window — verified live: CRP-RRBs
    XV's corrigenda and vacancy updates show up there, but not the original
    "Notification for Common Recruitment Process for CRP-RRB-XV" PDF itself,
    even while applications were still open. Its own per-program page does
    have it, using the same markup, so that page is crawled too. IBPS has no
    single index of "the currently active per-program page" — these are
    pinned by hand and need updating whenever IBPS moves e.g. RRB from XV to
    XVI (its stable hub pages like /regional-rural-bank/ render empty via
    plain requests, likely JS-populated)."""
    key = "ibps"
    name = "Institute of Banking Personnel Selection"
    listing_url = "https://www.ibps.in/index.php/crp-updates"
    extra_listing_urls = ["https://www.ibps.in/index.php/rural-bank-xv/"]
    allowed_host = "www.ibps.in"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})

    def _get(self, url):
        r = self.session.get(url, timeout=settings.request_timeout)
        r.raise_for_status()
        return r

    def _discover_page(self, page_url, year, all_years, found):
        try:
            r = self._get(page_url)
        except Exception:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(page_url, a["href"].strip())
            if ".pdf" not in href.lower():
                continue
            host = urlparse(href).netloc
            if host and host not in (self.allowed_host, "ibps.in"):
                continue
            row = a.find("div", class_="detail-list")
            if not row:
                continue
            date_div = row.find("div", class_="detail-first-heading")
            title_div = row.find("div", class_="detail-second-heading")
            title = title_div.get_text(" ", strip=True) if title_div else ""
            date_text = date_div.get_text(" ", strip=True) if date_div else ""
            if not title or title.lower() == "details":
                continue
            if any(k in title.lower() for k in EXCLUDE_KEYWORDS):
                continue
            m = DATE_RE.search(date_text)
            doc_year = None
            if m:
                yy = int(m.group(3))
                doc_year = yy if yy > 100 else 2000 + yy
            if doc_year is None:
                doc_year = common.year_from(title)
            if year is not None and doc_year != year:
                continue
            if year is None and not all_years:
                continue
            doc_type = common.doc_type_from(title)
            num = common.notification_number_from(title)
            found[href] = DiscoveredDocument(
                self.key, title, href, num, None, doc_year, doc_type, num or f"url:{href}",
            )

    def discover(self, year: int | None = None, all_years: bool = False):
        found = {}
        for page_url in [self.listing_url, *self.extra_listing_urls]:
            self._discover_page(page_url, year, all_years, found)
        return list(found.values())
