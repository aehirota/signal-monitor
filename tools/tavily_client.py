"""
Tavily search wrapper.

Used by `producer_relevant_jd` as the primary source for JD signals.
Tavily wins at keyword precision against ATS-hosted careers pages
(Greenhouse, Lever, Ashby) and LinkedIn job postings.

Day-2: implements basic search + spend tracking.
Day-3: extend with optional include_domains override per signal type.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from tavily import TavilyClient

from tools.spend_tracker import SpendTracker

_client: TavilyClient | None = None

# Tavily pricing: ~$0.005 per advanced search call (paid plan).
# Basic search is cheaper but lower precision; use advanced for JD signal extraction.
_PRICE_PER_SEARCH = 0.005


@dataclass
class TavilySearchResult:
    title: str
    url: str
    content: str
    score: float
    published_date: str | None = None


def client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _client


# Domains where modern SaaS companies publish ATS-hosted JDs.
# linkedin.com handled separately — too noisy for include_domains.
_JD_ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "smartrecruiters.com",
    "jobs.lever.co",
    "boards.greenhouse.io",
    "jobs.ashbyhq.com",
]


def search_jd(
    company_name: str,
    company_domain: str,
    role_keywords: list[str],
    days: int = 45,
    max_results: int = 8,
    *,
    tracker: SpendTracker,
) -> list[TavilySearchResult]:
    """Search ATS-hosted JDs for the given company within the last `days`.

    Builds a single query combining the company name + a curated list of
    role keywords (e.g. 'GTM Engineer', 'RevOps Architect'). include_domains
    restricts to ATS hosts so we surface the actual JD URL, not press
    coverage about hiring.
    """
    roles_clause = " OR ".join(f'"{role}"' for role in role_keywords)
    # Quote the company name to anchor the query; include domain stem as
    # a fallback for companies whose name is generic.
    domain_stem = company_domain.split(".")[0]
    query = f'"{company_name}" OR "{domain_stem}" ({roles_clause})'

    with tracker.charge(company_domain, "tavily_search_jd", est_usd=_PRICE_PER_SEARCH):
        raw = client().search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            include_domains=_JD_ATS_DOMAINS,
            days=days,
        )

    results: list[TavilySearchResult] = []
    for r in raw.get("results", []):
        results.append(
            TavilySearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                score=float(r.get("score", 0.0)),
                published_date=r.get("published_date"),
            )
        )
    return results
