"""
Anthropic native web_search fallback.

Universal fallback for all 6 signal types — used when the orchestrator
re-fires a producer with the "primary returned nothing / low quality" hint.

Mechanism:
  1. Ask Sonnet to use the web_search tool to gather evidence for the
     specific signal type + company.
  2. Sonnet returns a structured summary of cited sources.
  3. We hand that summary downstream to the same per-type extraction
     prompt as the primary path, so the typed-signal contract is the same
     regardless of source.

Zero new API key required — uses ANTHROPIC_API_KEY.
"""

from __future__ import annotations

from dataclasses import dataclass

from schemas.signal_types import SignalType
from tools import anthropic_client
from tools.spend_tracker import SpendTracker


# Search guidance per signal type — keeps the fallback narrow.
_SEARCH_GUIDE = {
    SignalType.funding_round: (
        "Search for any funding round (Seed/Series A/B/C/D/E/Bridge/IPO) "
        "announced by {company} in the last {days} days. Cite source URLs."
    ),
    SignalType.exec_move: (
        "Search for any new executive hire or promotion at {company} in "
        "the last {days} days for roles: CRO, COO, VP Sales, Head of "
        "RevOps, Head of GTM Engineering, Head of Sales Ops. Cite URLs."
    ),
    SignalType.relevant_jd: (
        "Search for currently-open job postings at {company} for roles: "
        "GTM Engineer, RevOps, Revenue Operations, Marketing Operations, "
        "AI SDR, Sales Systems Architect. Posted within {days} days. Cite URLs."
    ),
    SignalType.product_launch: (
        "Search for product launches, GA announcements, or new feature "
        "releases by {company} in the last {days} days. Cite source URLs."
    ),
    SignalType.tech_signal: (
        "Search for public mentions of {company} adopting, evaluating, "
        "migrating to, or migrating away from go-to-market tooling "
        "(Salesforce, HubSpot, Clay, Apollo, Outreach, Instantly, n8n) "
        "within the last {days} days. Cite source URLs."
    ),
    SignalType.news_strategic: (
        "Search for strategic events at {company} in the last {days} days: "
        "mergers, acquisitions, IPO filings, layoffs, major reorganizations, "
        "leadership shakeups. Cite source URLs."
    ),
}


@dataclass
class WebSearchResult:
    """Mirrors the shape of ExaSearchResult / TavilySearchResult / PerplexityResult.

    The extraction prompts treat all sources uniformly — we expose the same
    {title, url, content} contract so producers don't branch on source.
    """

    title: str
    url: str
    content: str
    score: float = 1.0
    published_date: str | None = None


def search(
    signal_type: SignalType,
    company_name: str,
    company_domain: str,
    days: int,
    *,
    tracker: SpendTracker,
    model: str,
) -> list[WebSearchResult]:
    """Run Anthropic native web_search and return a synthesized result list.

    Cost: ~1 LLM call with 2-3 web_search invocations. Estimated ~$0.04-0.06.
    Higher than primary sources but only triggers on the fallback path.
    """
    guide = _SEARCH_GUIDE[signal_type].format(company=company_name, days=days)
    user = (
        f"{guide}\n\n"
        "Output: a markdown bullet list, ONE bullet per cited source. "
        "Each bullet must be:\n"
        "  - URL: <full url>\n"
        "    Title: <article title or page name>\n"
        "    Snippet: <verbatim quote, 1-3 sentences, that supports the signal>\n\n"
        "If nothing relevant exists, output: 'No results.'"
    )
    system = (
        "You are a research assistant. Use the web_search tool to gather "
        "evidence. Return ONLY the markdown bullet list — no preamble, no "
        "epilogue. Each snippet must be a VERBATIM quote from the cited source."
    )

    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    text = anthropic_client.call_with_tools(
        model=model,
        system=system,
        user=user,
        tools=tools,
        max_tokens=2048,
        tracker=tracker,
        company_domain=company_domain,
        label=f"web_search_fallback_{signal_type.value}",
    )

    return _parse_bullets(text)


def _parse_bullets(text: str) -> list[WebSearchResult]:
    """Parse the markdown bullet list emitted by the fallback agent."""
    text = (text or "").strip()
    if text.lower().startswith("no results"):
        return []

    out: list[WebSearchResult] = []
    current: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- URL:") or line.startswith("* URL:"):
            if current:
                out.append(_to_result(current))
            current = {"url": line.split(":", 1)[1].strip()}
        elif line.lower().startswith("title:") or line.lower().startswith("- title:") or line.lower().startswith("* title:"):
            current["title"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("snippet:") or line.lower().startswith("- snippet:") or line.lower().startswith("* snippet:"):
            current["content"] = line.split(":", 1)[1].strip()
    if current:
        out.append(_to_result(current))
    return [r for r in out if r.url and r.content]


def _to_result(d: dict) -> WebSearchResult:
    return WebSearchResult(
        title=d.get("title", d.get("url", ""))[:200],
        url=d.get("url", ""),
        content=d.get("content", ""),
    )
