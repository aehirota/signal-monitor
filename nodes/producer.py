"""
Producer factory for all 6 signal types.

Per type, a producer:
  1. Picks the source (primary by default; fallback if orchestrator hinted)
  2. Queries the source with type-tuned phrasing
  3. Hands raw results to Sonnet with type-specific extraction prompt
  4. Returns typed signals

Orchestrator re-fire mechanics:
  When iteration > 0 AND state["retry_companies_<type>"] is set, the producer
  iterates ONLY those companies AND uses the Anthropic web_search fallback
  instead of the primary source. The retry payload is delivered via
  LangGraph Send() from the orchestrator.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from pydantic import BaseModel, Field

from schemas.signal_types import (
    AnySignal,
    ExecMoveSignal,
    FundingRoundSignal,
    NewsStrategicSignal,
    ProductLaunchSignal,
    RelevantJDSignal,
    SignalType,
    TechSignalSignal,
)
from state import GraphState, SpendLedger
from tools import anthropic_client
from tools import exa_client
from tools import perplexity_client
from tools import tavily_client
from tools import web_search_fallback
from tools.spend_tracker import BudgetExceeded, SpendTracker


# ─── Extraction wrappers (one per signal type) ───────────────────


class FundingRoundExtraction(BaseModel):
    signals: list[FundingRoundSignal] = Field(default_factory=list)
    reasoning: str = ""


class ExecMoveExtraction(BaseModel):
    signals: list[ExecMoveSignal] = Field(default_factory=list)
    reasoning: str = ""


class RelevantJDExtraction(BaseModel):
    signals: list[RelevantJDSignal] = Field(default_factory=list)
    reasoning: str = ""


class ProductLaunchExtraction(BaseModel):
    signals: list[ProductLaunchSignal] = Field(default_factory=list)
    reasoning: str = ""


class TechSignalExtraction(BaseModel):
    signals: list[TechSignalSignal] = Field(default_factory=list)
    reasoning: str = ""


class NewsStrategicExtraction(BaseModel):
    signals: list[NewsStrategicSignal] = Field(default_factory=list)
    reasoning: str = ""


_EXTRACTION_SCHEMA = {
    SignalType.funding_round: FundingRoundExtraction,
    SignalType.exec_move: ExecMoveExtraction,
    SignalType.relevant_jd: RelevantJDExtraction,
    SignalType.product_launch: ProductLaunchExtraction,
    SignalType.tech_signal: TechSignalExtraction,
    SignalType.news_strategic: NewsStrategicExtraction,
}

_PROMPT_NAME = {
    SignalType.funding_round: "producer_funding_round",
    SignalType.exec_move: "producer_exec_move",
    SignalType.relevant_jd: "producer_relevant_jd",
    SignalType.product_launch: "producer_product_launch",
    SignalType.tech_signal: "producer_tech_signal",
    SignalType.news_strategic: "producer_news_strategic",
}


# ─── Per-type primary query construction ─────────────────────────


_RELEVANT_JD_KEYWORDS = [
    "GTM Engineer",
    "RevOps",
    "Revenue Operations",
    "Sales Operations",
    "Marketing Operations",
    "AI SDR",
    "Sales Systems",
    "Salesforce Architect",
    "HubSpot Architect",
]


def _domain_to_company_name(domain: str) -> str:
    stem = domain.split(".")[0]
    return stem[:1].upper() + stem[1:]


def _query_primary(
    signal_type: SignalType,
    company_domain: str,
    days: int,
    tracker: SpendTracker,
) -> tuple[list, str]:
    """Returns (results, source_label). source_label is what we put in raw_source on each typed signal."""
    name = _domain_to_company_name(company_domain)
    stem = company_domain.split(".")[0]

    if signal_type is SignalType.funding_round:
        query = f'"{name}" OR "{stem}" Series A OR Series B OR Series C OR Series D OR funding OR raised OR valuation'
        return exa_client.search(query, company_domain, days, tracker=tracker, label="exa_funding_round"), "exa"

    if signal_type is SignalType.exec_move:
        query = (
            f'"{name}" OR "{stem}" (CRO OR COO OR CMO OR "VP Sales" OR "Head of RevOps" '
            f'OR "Head of GTM" OR "Head of Marketing") (hired OR joins OR appointed OR promoted)'
        )
        return exa_client.search(query, company_domain, days, tracker=tracker, label="exa_exec_move"), "exa"

    if signal_type is SignalType.relevant_jd:
        return (
            tavily_client.search_jd(
                company_name=name,
                company_domain=company_domain,
                role_keywords=_RELEVANT_JD_KEYWORDS,
                days=days,
                tracker=tracker,
            ),
            "tavily",
        )

    if signal_type is SignalType.product_launch:
        query = (
            f'What did {name} ({company_domain}) launch, ship, or release as GA / public beta '
            f'in the last {days} days? Include source URLs.'
        )
        return perplexity_client.search(query, company_domain, tracker=tracker, label="perplexity_product_launch"), "perplexity"

    if signal_type is SignalType.tech_signal:
        query = (
            f'"{name}" OR "{stem}" (Salesforce OR HubSpot OR Apollo OR Clay OR Instantly OR Outreach OR n8n) '
            f'(evaluating OR migrating OR adopted OR replaced OR sunset)'
        )
        return exa_client.search(query, company_domain, days, tracker=tracker, label="exa_tech_signal"), "exa"

    if signal_type is SignalType.news_strategic:
        query = (
            f'Did {name} ({company_domain}) announce any M&A, IPO filing, layoffs, '
            f'major reorganization, or leadership shakeup in the last {days} days? Include source URLs.'
        )
        return perplexity_client.search(query, company_domain, tracker=tracker, label="perplexity_news_strategic"), "perplexity"

    raise ValueError(f"Unknown signal_type: {signal_type}")


def _query_fallback(
    signal_type: SignalType,
    company_domain: str,
    days: int,
    tracker: SpendTracker,
    fallback_model: str,
) -> tuple[list, str]:
    """Anthropic native web_search fallback. Same result shape as primary sources."""
    name = _domain_to_company_name(company_domain)
    results = web_search_fallback.search(
        signal_type=signal_type,
        company_name=name,
        company_domain=company_domain,
        days=days,
        tracker=tracker,
        model=fallback_model,
    )
    return results, "anthropic_web_search"


def _results_to_blob(results: list) -> str:
    """Format heterogeneous source results into a single string for the extractor."""
    parts: list[str] = []
    for i, r in enumerate(results):
        url = getattr(r, "url", "")
        title = getattr(r, "title", "")
        published = getattr(r, "published_date", None) or "unknown"
        content = getattr(r, "content", "")
        parts.append(
            f"[{i}] title: {title}\nurl: {url}\npublished: {published}\ncontent: {content}"
        )
    return "\n\n".join(parts)


def _extract(
    signal_type: SignalType,
    company_domain: str,
    results: list,
    source_label: str,
    days: int,
    today: str,
    model: str,
    tracker: SpendTracker,
) -> list[AnySignal]:
    """Hand source results to Sonnet for typed extraction."""
    if not results:
        return []
    schema_cls = _EXTRACTION_SCHEMA[signal_type]
    system_prompt = anthropic_client.load_prompt(_PROMPT_NAME[signal_type])
    user = (
        f"Watchlist company: {company_domain}\n"
        f"Today's date: {today}\n"
        f"Recency window: last {days} days\n"
        f"Raw source for these results: {source_label} (use as `raw_source` per signal)\n\n"
        f"Search results (top {len(results)}):\n\n"
        f"{_results_to_blob(results)}\n\n"
        "Extract typed signals per the schema. Reject anything outside scope or anything you can't quote-anchor."
    )
    extraction = anthropic_client.call_structured(
        model=model,
        system=system_prompt,
        user=user,
        schema=schema_cls,
        tracker=tracker,
        company_domain=company_domain,
        label=f"producer_{signal_type.value}_extract",
    )
    return list(extraction.signals)


# ─── Producer node body ──────────────────────────────────────────


def _produce_for_type(
    signal_type: SignalType,
    state: GraphState,
    tracker: SpendTracker,
) -> list[AnySignal]:
    # Defensive: in parallel super-steps with intermittent peer-node errors,
    # LangGraph occasionally hands us state where reducer-untouched fields
    # (watchlist, config) appear empty. Re-load from disk as a fallback —
    # config is static per-run, so this is safe and idempotent.
    config = state.get("config")
    if not config or not config.get("signal_types"):
        import yaml
        from pathlib import Path
        try:
            config = yaml.safe_load(Path("config.yaml").read_text())
            print(f"[producer_{signal_type.value}] config re-loaded from disk")
        except Exception as e:
            print(f"[producer_{signal_type.value}] config unrecoverable: {e}")
            return []

    days = config["signal_types"][signal_type.value]["recency_days"]
    today_iso = date.today().isoformat()
    producer_model = config["models"]["producer"]
    fallback_model = config["models"]["producer"]  # web_search fallback uses same tier

    # Did the orchestrator target this producer for retry?
    retry_key = f"retry_companies_{signal_type.value}"
    retry_only: list[str] | None = state.get(retry_key)
    use_fallback = retry_only is not None

    watchlist_full = state.get("watchlist") or []
    if not watchlist_full:
        # Same fallback as config — re-load from disk on transient empty.
        import yaml
        from pathlib import Path
        try:
            wlpath = config.get("watchlist", {}).get("path", "watchlist.yaml")
            wldoc = yaml.safe_load(Path(wlpath).read_text())
            watchlist_full = wldoc.get("companies", [])
            only = config.get("only_domain")
            if only:
                watchlist_full = [c for c in watchlist_full if c.get("domain") == only]
        except Exception as e:
            print(f"[producer_{signal_type.value}] watchlist unrecoverable: {e}")
            return []

    if retry_only is not None:
        watchlist = [c for c in watchlist_full if c.get("domain") in set(retry_only)]
    else:
        watchlist = watchlist_full

    all_signals: list[AnySignal] = []

    for company in watchlist:
        domain = company.get("domain")
        if not domain:
            continue
        if signal_type.value in (company.get("mute_types") or []):
            continue
        if tracker.is_over_budget(domain):
            continue

        try:
            if use_fallback:
                results, source_label = _query_fallback(
                    signal_type, domain, days, tracker, fallback_model
                )
            else:
                results, source_label = _query_primary(signal_type, domain, days, tracker)
        except BudgetExceeded as e:
            print(f"[producer_{signal_type.value}] budget cap on {domain}: {e}")
            break
        except Exception as e:
            # Source-specific failure shouldn't crash the run. Log and skip.
            print(f"[producer_{signal_type.value}] source error on {domain}: {type(e).__name__}: {e}")
            continue

        if not results:
            continue

        try:
            signals = _extract(
                signal_type=signal_type,
                company_domain=domain,
                results=results,
                source_label=source_label,
                days=days,
                today=today_iso,
                model=producer_model,
                tracker=tracker,
            )
        except BudgetExceeded as e:
            print(f"[producer_{signal_type.value}] budget cap on extract for {domain}: {e}")
            break
        except Exception as e:
            print(f"[producer_{signal_type.value}] extraction error on {domain}: {type(e).__name__}: {e}")
            continue

        all_signals.extend(signals)

    return all_signals


def make_producer_node(signal_type: SignalType) -> Callable[[GraphState], dict]:
    def producer(state: GraphState) -> dict:
        # Defensive: in some LangGraph parallel-super-step paths the state
        # dict may transiently miss reducer-managed fields when a peer node
        # fails. Fall back to safe defaults; loader's values are still the
        # source of truth via the merge_spend reducer.
        spend_in = state.get("spend") or SpendLedger(cap_usd=4.0)
        config = state.get("config") or {}
        budget_cfg = config.get("budget", {}) if isinstance(config, dict) else {}

        prior_total = spend_in.total_usd
        prior_per_company = dict(spend_in.per_company_usd)

        tracker = SpendTracker(
            cap_usd=spend_in.cap_usd,
            per_company_soft_cap_usd=budget_cfg.get("per_company_soft_cap_usd", 0.25),
            total_usd=prior_total,
            per_company_usd=prior_per_company,
        )

        signals = _produce_for_type(signal_type, state, tracker)

        # Emit delta only — merge_spend reducer will combine across producers.
        delta_total = tracker.total_usd - prior_total
        delta_per_company: dict[str, float] = {}
        for d, total in tracker.per_company_usd.items():
            prior = prior_per_company.get(d, 0.0)
            x = total - prior
            if x > 0:
                delta_per_company[d] = x

        return {
            "signal_candidates": signals,
            "spend": SpendLedger(
                total_usd=delta_total,
                per_company_usd=delta_per_company,
                cap_usd=spend_in.cap_usd,
            ),
        }

    producer.__name__ = f"producer_{signal_type.value}"
    return producer
