"""Broader net than the fixed site adapters: asks Claude to search the
web for fresh Indian government recruitment notifications and hand back
candidate PDF/notice URLs. This is what covers boards whose listing
pages are JS-only (SSC, RRB) or aren't wired up as an adapter yet —
Claude's web search finds the notice directly, we don't need to be able
to parse that board's own listing page at all.

Every candidate still goes through the exact same download -> classify
-> extract -> review pipeline as an adapter-discovered document (see
crawler.crawl_documents). This module's only job is turning a handful
of search themes into a list of DiscoveredDocument candidates.
"""
import json
import logging
from app.config import settings
from app.adapters.base import DiscoveredDocument
from app.services import spend, eventlog

log = logging.getLogger("web_discovery")

# Haiku 4.5 pricing (USD per token) — update if WEB_DISCOVERY_MODEL changes.
# Anthropic also bills web_search per-use separately from tokens; that fee
# is NOT tracked here (not reliably known at write time), so treat the
# monthly cap as a token-cost floor, not a hard ceiling on total spend —
# keep max_uses low and watch the Anthropic console too.
PRICE_PER_INPUT_TOKEN = 1.00 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 5.00 / 1_000_000

DEFAULT_QUERIES = [
    "site:tgpsc.gov.in OR site:appsc.gov.in OR site:ssc.gov.in new recruitment notification 2026 PDF",
    "UPSC recruitment notification 2026 PDF site:upsc.gov.in",
    "site:indiapost.gov.in OR site:indiapostgdsonline.gov.in Gramin Dak Sevak GDS recruitment notification 2026 PDF",
    "site:ibps.in OR site:pfrda.org.in OR site:iob.in bank officer recruitment notification 2026 PDF",
    "PSU public sector undertaking recruitment notification 2026 PDF site:.gov.in OR site:.nic.in engineer OR manager",
    "MPESB OR CERT-In OR MeitY OR IndiaAI recruitment notification 2026 PDF site:.gov.in OR site:.nic.in",
]

SYSTEM_PROMPT = (
    "You find OFFICIAL Indian government recruitment notifications by searching the web. "
    "Only include a result if its URL is on an official government domain (.gov.in, .nic.in, "
    "or a state Public Service Commission's own domain) and plausibly links to an actual "
    "recruitment notification (a PDF, or a notice page that links to one). "
    "NEVER include private job-aggregator or blog sites (e.g. sarkariresult, freejobalert, "
    "employment-news blogs) — they are not authoritative and often stale or wrong. "
    "NEVER invent a URL — only URLs that came back from your search. "
    "After searching, respond with ONLY a JSON array (no prose, no markdown fence). Each item: "
    '{"url": "...", "title": "...", "source_domain": "...", "notification_number": "... or null", '
    '"year": 2026 or null, "department": "... or null"}. Empty array if you found nothing '
    "that meets the bar above."
)


def _parse_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _search_one(client, query: str) -> list[dict]:
    # max_uses caps how many times Claude can search before answering —
    # each round stuffs full result pages into context, which is what
    # actually drives cost. Keep this low; a broad query rarely needs
    # more than 1-2 rounds to find an official notification URL.
    response = client.messages.create(
        model=settings.web_discovery_model,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        # allowed_callers is required on Haiku models for this tool, else
        # a 400 ("does not support programmatic tool calling") — harmless
        # to set explicitly on Sonnet/Opus too.
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 2, "allowed_callers": ["direct"]}],
        messages=[{"role": "user", "content": query}],
    )
    cost = response.usage.input_tokens * PRICE_PER_INPUT_TOKEN + response.usage.output_tokens * PRICE_PER_OUTPUT_TOKEN
    spend.record_spend(cost, component="web_discovery")
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    items = _parse_json_array(text)
    eventlog.emit("web_search_query", f"Query done: {len(items)} candidate(s) (${cost:.4f})",
                  source="web_discovery", query=query, count=len(items), cost_usd=cost)
    return items


def run(queries: list[str] | None = None) -> list[DiscoveredDocument]:
    """Runs each query in turn, stops early (fails closed) if the next
    query would exceed the monthly web-discovery budget. Returns
    deduplicated DiscoveredDocument candidates ready for
    crawler.crawl_documents()."""
    if not settings.web_discovery_enabled:
        eventlog.emit("web_discovery_skipped", "WEB_DISCOVERY_ENABLED is false — skipping.", level="warning", source="web_discovery")
        return []
    if not settings.llm_api_key:
        eventlog.emit("web_discovery_skipped", "No LLM_API_KEY configured — skipping.", level="warning", source="web_discovery")
        return []

    queries = queries or DEFAULT_QUERIES
    import anthropic
    client = anthropic.Anthropic(api_key=settings.llm_api_key)

    # Pre-flight estimate per query. A live run at Sonnet 5 / max_uses=5
    # measured $0.13-$0.19/query; Haiku 4.5 is half the per-token price
    # and max_uses is now 2, so real cost should come in well under this —
    # kept deliberately conservative so budget-cap checks fail closed
    # before the actual spend, not after.
    estimated_usd_per_query = 0.08

    found: dict[str, dict] = {}
    for query in queries:
        if spend.would_exceed_budget(estimated_usd_per_query, component="web_discovery"):
            eventlog.emit("web_discovery_budget_stop",
                          f"Monthly web-discovery budget (${settings.web_discovery_monthly_usd_cap:.2f}) reached — "
                          f"stopped after {len(found)} candidate(s) from earlier queries.",
                          level="warning", source="web_discovery")
            break
        try:
            for item in _search_one(client, query):
                url = (item.get("url") or "").strip()
                if url and url not in found:
                    found[url] = item
        except Exception as e:
            eventlog.emit("web_search_query_failed", f"Query failed: {e}", level="error", source="web_discovery", query=query)

    docs = []
    for url, item in found.items():
        num = item.get("notification_number")
        docs.append(DiscoveredDocument(
            source_key="web_discovery",
            title=item.get("title") or url,
            official_url=url,
            notification_number=num,
            department=item.get("department"),
            year=item.get("year"),
            document_type="notification",
            recruitment_key=num or f"url:{url}",
        ))
    return docs
