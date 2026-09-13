import re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from app.config import settings
from . import common
from .base import SourceAdapter, DiscoveredDocument

ADVT_NUM_RE = re.compile(r"advt[_-](\d+)", re.I)


class DRDOAdapter(SourceAdapter):
    """DRDO recruitment via the Recruitment & Assessment Centre (RAC). The
    home page is a "currently open" board, not an archive — it typically
    lists a handful of live advertisements at once, each as a PDF link whose
    visible text is literally "Advertisement". Verified live: the page also
    links FAQ/schedule/pay-matrix/brief PDFs for the same advertisement,
    which are deliberately excluded here — only the primary advt_<N>.pdf
    (or _v<N> revision) is a notification.

    The page doesn't reliably expose a per-advertisement year, and RAC keeps
    only a few live postings on this page at any time, so this adapter
    ignores the year/all_years filter and always returns everything
    currently listed — filtering out a live opening because a date regex
    failed would be worse than a handful of extra rows."""
    key = "drdo"
    name = "DRDO Recruitment & Assessment Centre"
    listing_url = "https://rac.gov.in/"
    allowed_host = "rac.gov.in"

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

        heading_map = {}
        for h3 in soup.find_all("h3"):
            spans = h3.find_all("span", class_="advtHeading")
            if len(spans) >= 2:
                title_text = spans[0].get_text(strip=True)
                num_text = spans[-1].get_text(strip=True)
                if num_text.isdigit():
                    heading_map[num_text] = title_text

        found = {}
        seen_nums = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "advt_" not in href.lower():
                continue
            if a.get_text(strip=True).lower() != "advertisement":
                continue
            m = ADVT_NUM_RE.search(href)
            if not m:
                continue
            num = m.group(1)
            if num in seen_nums:
                continue
            seen_nums.add(num)
            full_href = urljoin(self.listing_url, href)
            host = urlparse(full_href).netloc
            if host and host != self.allowed_host:
                continue
            heading = heading_map.get(num)
            title = f"{heading} (RAC Advertisement #{num})" if heading else f"DRDO RAC Advertisement #{num}"
            found[full_href] = DiscoveredDocument(
                self.key, title, full_href, f"RAC/{num}", None, None, "notification", f"RAC/{num}",
            )
        return list(found.values())
