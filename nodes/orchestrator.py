"""
Orchestrator: routes either to selective producer re-fires (Send + retry hint)
or onward to the deduper if nothing needs retrying.

Pattern: orchestrator_node returns a LangGraph Command whose `goto` is either
a list of Send objects (parallel re-fire) or the string `"deduper"` (done).
This replaces the need for a separate `should_retry` conditional-edge function;
the routing decision lives in one place.

Per-type unique state keys (`retry_companies_<type>`) avoid LastValue
clobbering when multiple Sends merge into the global state.
"""

from __future__ import annotations

from langgraph.types import Command, Send

from schemas.signal_types import SignalType
from state import GraphState


def orchestrator_node(state: GraphState) -> Command:
    critique = state.get("critique")
    if critique is None or not critique.gaps:
        return Command(goto="deduper")

    config = state.get("config", {})
    max_retries = config.get("critic", {}).get("max_retries_per_type", 1)
    if state.get("iteration", 0) > max_retries:
        return Command(goto="deduper")

    by_type: dict[SignalType, list[str]] = {}
    for gap in critique.gaps:
        bucket = by_type.setdefault(gap.failed_signal_type, [])
        if gap.company_domain not in bucket:
            bucket.append(gap.company_domain)

    sends: list[Send] = []
    for st, companies in by_type.items():
        if not companies:
            continue
        sends.append(
            Send(
                f"producer_{st.value}",
                {f"retry_companies_{st.value}": companies},
            )
        )

    if not sends:
        return Command(goto="deduper")
    return Command(goto=sends)
