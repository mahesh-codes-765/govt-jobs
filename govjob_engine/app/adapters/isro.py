import time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from app.config import settings
from . import common
from .base import SourceAdapter, DiscoveredDocument


class ISROAdapter(SourceAdapter):
    """ISRO and its centres (URSC, ISTRAC, HSFC, IIST, IPRC, DOS, ICRB, ...).
    CurrentOpportunities.html is genuinely "currently open only", not an
    archive, which is exactly what the eligibility checker needs — but it's
    a two-tier site: the index page only links to a per-centre .html detail
    page (empty link text, an eye icon), and the actual PDFs — including
    result panels, interview schedules and biodata forms mixed in with the
    real notification — live on that detail page. Only a PDF whose filename
    contains "advt" is treated as the notification; a detail page with none
    is skipped rather than guessing. Verified live."""
    key = "isro"
    name = "ISRO"
    listing_url = "https://www.isro.gov.in/CurrentOpportunities.html"
    allowed_host = "www.isro.gov.in"

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

        detail_pages = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.lower().endswith(".html") or href.startswith(("http://", "https://")):
                continue
            if href in seen:
                continue
            seen.add(href)
            detail_pages.append(href)

        found = {}
        for href in detail_pages[: settings.max_listing_pages]:
            detail_url = urljoin(self.listing_url, href)
            try:
                dr = self._get(detail_url)
            except Exception:
                continue
            time.sleep(settings.crawl_delay_seconds)
            dsoup = BeautifulSoup(dr.text, "html.parser")
            title = dsoup.title.get_text(strip=True) if dsoup.title else href

            pdf_url = None
            for pa in dsoup.find_all("a", href=True):
                phref = pa["href"].strip()
                if ".pdf" not in phref.lower():
                    continue
                if "advt" in phref.lower():
                    pdf_url = urljoin(detail_url, phref)
                    break
            if not pdf_url:
                continue
            host = urlparse(pdf_url).netloc
            if host and host != self.allowed_host:
                continue

            doc_year = common.year_from(pdf_url) or common.year_from(title)
            if year is not None and doc_year != year:
                continue
            if year is None and not all_years:
                continue
            num = common.notification_number_from(title)
            found[pdf_url] = DiscoveredDocument(
                self.key, title, pdf_url, num, None, doc_year,
                common.doc_type_from(title), num or f"url:{pdf_url}",
            )
        return list(found.values())
