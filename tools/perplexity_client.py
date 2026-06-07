"""
Perplexity search wrapper.

Primary source for `product_launch` and `news_strategic` signals — where
synthesis across multiple sources matters more than raw URL retrieval.

Uses the Perplexity Sonar API: a chat-style endpoint that returns synthesized
answers + cited sources. We treat the citations as our search results and
the response text as a "summary" the extractor can mine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from tools.spend_tracker import SpendTracker


_API_URL = "https://api.perplexity.ai/chat/completions"
# Sonar small model: ~$0.005-0.01 per query. Conservative estimate.
_PRICE_PER_QUERY = 0.008


@dataclass
class PerplexityResult:
    title: str
    url: str
    content: str  # the synthesized answer snippet attributed to this source
    score: float = 1.0
    published_date: str | None = None


def search(
    query: str,
    company_domain: str,
    *,
    tracker: SpendTracker,
    label: str = "perplexity_search",
    model: str = "sonar",
) -> list[PerplexityResult]:
    """Ask Perplexity a research question and return cited sources as results.

    The synthesized answer body is split into per-source attributions when
    possible; otherwise the whole answer is attached to each source URL.
    """
    api_key = os.environ["PERPLEXITY_API_KEY"]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": query}],
        "return_related_questions": False,
        "return_citations": True,
    }

    with tracker.charge(company_domain, label, est_usd=_PRICE_PER_QUERY):
        resp = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()

    answer = (body["choices"][0]["message"]["content"] or "").strip()
    citations = body.get("citations", []) or []

    # We don't have per-citation snippets — attaching the full answer to
    # every citation explodes context (10 citations × 20k chars = 200k
    # chars, blows past Claude's 200k input limit). Instead, return the
    # answer ONCE on the first citation as the synthesized summary, and
    # the other citations as URL-only references the extractor can
    # quote-anchor against via web fetch downstream.
    results: list[PerplexityResult] = []
    if not citations:
        return results
    results.append(
        PerplexityResult(
            title="Perplexity synthesis",
            url=citations[0],
            content=answer,
        )
    )
    for url in citations[1:]:
        results.append(
            PerplexityResult(
                title=url,
                url=url,
                content="(referenced in synthesis above)",
            )
        )
    return results
