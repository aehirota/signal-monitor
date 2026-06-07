"""
Critic node: evidence_traceable (code) + Sonnet judgment on 3 dims.

Day-3 updates:
- Tracks already-judged identity_keys across iterations so re-fires don't
  re-judge signals the critic already verdicted.
- Carries cumulative signals_passed across iterations.
- Emits Gap objects with failed_signal_type + company_domain for the
  orchestrator's selective re-dispatch.
"""

from __future__ import annotations

import sys
from typing import Optional

from pydantic import BaseModel, Field

from schemas.signal_types import AnySignal
from state import Critique, Gap, GraphState, SpendLedger
from tools import anthropic_client
from tools.evidence_check import check_evidence
from tools.spend_tracker import BudgetExceeded, SpendTracker


class _SignalCritique(BaseModel):
    recency_genuine: int = Field(ge=0, le=10)
    type_match: int = Field(ge=0, le=10)
    actionability: int = Field(ge=0, le=10)
    pass_decision: bool
    failure_reason: Optional[str] = None


def _critique_one_signal(
    signal: AnySignal,
    config: dict,
    tracker: SpendTracker,
) -> tuple[bool, dict]:
    min_dim = config["critic"]["min_per_dimension"]

    ev = check_evidence(str(signal.source_url), signal.evidence_quote)
    if ev.score == 0:
        return False, {
            "identity_key": signal.identity_key(),
            "signal": signal.model_dump(mode="json"),
            "verdict": {
                "evidence_traceable": 0,
                "pass_decision": False,
                "failure_reason": f"evidence_traceable=0: {ev.reason}",
            },
        }

    system_prompt = anthropic_client.load_prompt("critic")
    user = (
        f"Candidate signal (already passed code-enforced evidence_traceable check):\n"
        f"{signal.model_dump_json(indent=2)}\n\n"
        f"Score recency_genuine, type_match, actionability. "
        f"Pass requires ALL >= {min_dim}."
    )

    try:
        verdict = anthropic_client.call_structured(
            model=config["models"]["critic"],
            system=system_prompt,
            user=user,
            schema=_SignalCritique,
            tracker=tracker,
            company_domain=signal.company_domain,
            label="critic_judge",
        )
    except BudgetExceeded as e:
        print(f"[critic] budget cap on {signal.company_domain}: {e}", file=sys.stderr)
        return False, {
            "identity_key": signal.identity_key(),
            "signal": signal.model_dump(mode="json"),
            "verdict": {
                "evidence_traceable": 10,
                "pass_decision": False,
                "failure_reason": "budget_cap_hit_during_critic",
            },
        }

    passed = (
        verdict.pass_decision
        and verdict.recency_genuine >= min_dim
        and verdict.type_match >= min_dim
        and verdict.actionability >= min_dim
    )
    return passed, {
        "identity_key": signal.identity_key(),
        "signal": signal.model_dump(mode="json"),
        "verdict": {
            "evidence_traceable": 10,
            **verdict.model_dump(),
        },
    }


def critic_node(state: GraphState) -> dict:
    candidates: list[AnySignal] = state.get("signal_candidates", []) or []
    config = state["config"]
    iteration = state.get("iteration", 0)

    # Cumulative state from prior critic passes (if this is a retry).
    prior = state.get("critique")
    already_judged_keys: set[str] = set()
    cumulative_passed: list[AnySignal] = []
    cumulative_failed: list[dict] = []
    if prior is not None:
        cumulative_passed = list(prior.signals_passed)
        cumulative_failed = list(prior.signals_failed)
        already_judged_keys.update(s.identity_key() for s in prior.signals_passed)
        already_judged_keys.update(
            rec.get("identity_key", "") for rec in prior.signals_failed
        )

    spend_in = state.get("spend") or SpendLedger(cap_usd=4.0)
    tracker = SpendTracker(
        cap_usd=spend_in.cap_usd,
        per_company_soft_cap_usd=config["budget"]["per_company_soft_cap_usd"],
        total_usd=spend_in.total_usd,
        per_company_usd=dict(spend_in.per_company_usd),
    )

    # In-this-iteration dedup + skip-already-judged.
    seen_this_pass: set[str] = set()
    to_judge: list[AnySignal] = []
    for s in candidates:
        k = s.identity_key()
        if k in already_judged_keys or k in seen_this_pass:
            continue
        seen_this_pass.add(k)
        to_judge.append(s)

    new_passed: list[AnySignal] = []
    new_failed: list[dict] = []
    new_gaps: list[Gap] = []

    for signal in to_judge:
        passed, audit = _critique_one_signal(signal, config, tracker)
        if passed:
            new_passed.append(signal)
        else:
            new_failed.append(audit)
            new_gaps.append(
                Gap(
                    failed_signal_type=signal.type,
                    company_domain=signal.company_domain,
                    hint=audit["verdict"].get("failure_reason") or "unspecified",
                    severity=0.6,
                )
            )

    critique = Critique(
        iteration=iteration + 1,
        signals_passed=cumulative_passed + new_passed,
        signals_failed=cumulative_failed + new_failed,
        gaps=new_gaps,
    )

    prior_total = spend_in.total_usd
    prior_per_company = dict(spend_in.per_company_usd)
    delta_total = tracker.total_usd - prior_total
    delta_per_company: dict[str, float] = {}
    for d, total in tracker.per_company_usd.items():
        prior_v = prior_per_company.get(d, 0.0)
        x = total - prior_v
        if x > 0:
            delta_per_company[d] = x

    return {
        "critique": critique,
        "spend": SpendLedger(
            total_usd=delta_total,
            per_company_usd=delta_per_company,
            cap_usd=spend_in.cap_usd,
        ),
        "iteration": iteration + 1,
    }
