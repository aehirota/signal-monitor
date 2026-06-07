"""
Exa search wrapper.

Primary source for `funding_round`, `exec_move`, and `tech_signal` signals.
Exa wins at semantic web search — funding announcements + exec moves +
stack-mention nuance where keyword precision (Tavily) isn't enough.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta

from exa_py import Exa

from tools.spend_tracker import SpendTracker


_client: Exa | None = None

# Exa pricing (advanced search w/ contents): ~$0.005 per call. Adjust if tier changes.
_PRICE_PER_SEARCH = 0.005


@dataclass
class ExaSearchResult:
    title: str
    url: str
    content: str
    score: float
    published_date: str | None = None


def client() -> Exa:
    global _client
    if _client is None:
        _client = Exa(api_key=os.environ["EXA_API_KEY"])
    return _client


def _start_date_for(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# Cap per-result content length to keep total context well under Claude's
# 200k limit. With num_results=8 × 2000 chars + prompt overhead, we stay
# comfortably below 50k tokens for the extractor call.
_MAX_CHARS_PER_RESULT = 2000


def search(
    query: str,
    company_domain: str,
    days: int,
    num_results: int = 8,
    *,
    tracker: SpendTracker,
    label: str = "exa_search",
) -> list[ExaSearchResult]:
    """Semantic Exa search restricted to the recency window.

    Returns up to num_results items with content snippets, truncated per
    item so the extractor call stays inside Claude's input window.
    """
    with tracker.charge(company_domain, label, est_usd=_PRICE_PER_SEARCH):
        raw = client().search_and_contents(
            query=query,
            num_results=num_results,
            start_published_date=_start_date_for(days),
            text={"max_characters": _MAX_CHARS_PER_RESULT},
        )

    out: list[ExaSearchResult] = []
    for r in raw.results:
        content = getattr(r, "text", "") or ""
        if len(content) > _MAX_CHARS_PER_RESULT:
            # Defensive: Exa may return slightly more than requested.
            content = content[:_MAX_CHARS_PER_RESULT]
        out.append(
            ExaSearchResult(
                title=getattr(r, "title", "") or "",
                url=getattr(r, "url", "") or "",
                content=content,
                score=float(getattr(r, "score", 0.0) or 0.0),
                published_date=getattr(r, "published_date", None),
            )
        )
    return out
