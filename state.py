"""
LangGraph state container + custom reducers for safe parallel writes.

The producer_per_type super-step fans out 6 nodes that all write to
state.signal_candidates. LangGraph's default LastValue channel would
clobber 5 of 6 writes. The merge_signals reducer concatenates instead.

Same architectural lesson as account-research-agent's merge_raw reducer:
when parallel nodes write to the same state key, you must own the merge
contract.
"""

from __future__ import annotations

from typing import Annotated, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel, Field

from schemas.signal_types import AnySignal, SignalType


# ─── Critic-driven gap routing (mirrors account-research-agent) ──


class Gap(BaseModel):
    """A specific deficiency the critic wants the orchestrator to fix.

    target_signal_type tells the orchestrator which producer to re-fire;
    hint carries a short instruction (e.g. 'use anthropic_web_search fallback,
    primary returned empty').
    """

    failed_signal_type: SignalType
    company_domain: str
    hint: str
    severity: float = Field(ge=0.0, le=1.0)


class Critique(BaseModel):
    """Critic's verdict on the candidate signals from this iteration."""

    iteration: int = 0
    signals_passed: list[AnySignal] = Field(default_factory=list)
    signals_failed: list[dict] = Field(
        default_factory=list,
        description="Raw failure records for audit log (not state-routed)",
    )
    gaps: list[Gap] = Field(default_factory=list)


# ─── Spend tracking ────────────────────────────────────────


class SpendLedger(BaseModel):
    """Cumulative spend across all API calls in this run.

    Concrete tracker lives in tools/spend_tracker.py; this is the snapshot
    that gets serialized into state for digest_compiler to display.
    """

    total_usd: float = 0.0
    per_company_usd: dict[str, float] = Field(default_factory=dict)
    cap_usd: float = 4.0
    halted: bool = False
    halted_reason: Optional[str] = None


# ─── Reducers ──────────────────────────────────────────────


def merge_signals(
    a: Optional[list[AnySignal]], b: Optional[list[AnySignal]]
) -> list[AnySignal]:
    """Reducer for parallel writes to state['signal_candidates'].

    Each producer node returns its slice of candidates; the reducer
    concatenates them. Initial reduce against undefined left side returns b.
    """
    if a is None:
        return list(b or [])
    if b is None:
        return list(a)
    return [*a, *b]


def merge_spend(
    a: Optional[SpendLedger], b: Optional[SpendLedger]
) -> SpendLedger:
    """Reducer for spend updates from parallel nodes.

    Each leaf node returns a SpendLedger with just its own contribution
    in per_company_usd + total_usd. The reducer sums them.
    """
    if a is None:
        return b or SpendLedger()
    if b is None:
        return a
    merged_per_company = dict(a.per_company_usd)
    for company, spend in b.per_company_usd.items():
        merged_per_company[company] = merged_per_company.get(company, 0.0) + spend
    halted = a.halted or b.halted
    halted_reason = a.halted_reason or b.halted_reason
    return SpendLedger(
        total_usd=a.total_usd + b.total_usd,
        per_company_usd=merged_per_company,
        cap_usd=max(a.cap_usd, b.cap_usd),
        halted=halted,
        halted_reason=halted_reason,
    )


# ─── Main state ─────────────────────────────────────────────


class GraphState(TypedDict, total=False):
    """The LangGraph state object threaded through every node.

    All reducer-managed fields use Annotated[<type>, <reducer>] so parallel
    writes don't clobber. Single-writer fields are plain types.
    """

    # ─ Configuration / inputs (set by loader, read-only after) ─
    config: dict
    watchlist: list[dict]
    dedup_keys_active: set[str]  # identity_keys still inside recency window

    # ─ Parallel-write fields (reducer required) ─
    signal_candidates: Annotated[list[AnySignal], merge_signals]
    spend: Annotated[SpendLedger, merge_spend]

    # ─ Single-writer fields ─
    critique: Critique
    signals_after_dedup: list[AnySignal]
    digest_html: str
    digest_text: str

    # ─ Audit ─
    run_id: str               # ISO timestamp; also used as .tmp/runs/<run_id>/ dir
    iteration: int            # critic loop counter; capped at config.critic.max_retries_per_type

    # ─ Orchestrator → producer retry signaling (set via Send on the retry pass) ─
    # Per-type unique keys avoid LastValue clobbering when multiple Sends merge.
    # Producer reads its own key; if present, iterate only those companies + use fallback.
    retry_companies_funding_round: list[str]
    retry_companies_exec_move: list[str]
    retry_companies_relevant_jd: list[str]
    retry_companies_product_launch: list[str]
    retry_companies_tech_signal: list[str]
    retry_companies_news_strategic: list[str]
