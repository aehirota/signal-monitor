"""
LangGraph state machine for Signal Monitor.

Topology:

    ENTRY
      |
   [loader]
      |
   [producer_funding_round] [producer_exec_move] [producer_relevant_jd]
   [producer_product_launch] [producer_tech_signal] [producer_news_strategic]
      |  (parallel super-step; merge_signals reducer)
      v
   [critic]
      |
      v
   [orchestrator]  -- Command goto --
      |                              \\
      | goto=[Send(producer_X, ...)]   goto="deduper"
      v                                  v
  (specific producers re-fire)        [deduper]
   with retry_companies_<type>           |
      v                                  v
   [critic] (iteration += 1)         [digest_compiler]
      v                                  |
   [orchestrator]                        v
      v                                 EXIT
   [deduper] when no more retries
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from state import GraphState
from schemas.signal_types import SignalType


def build_graph():
    from nodes.loader import loader_node
    from nodes.producer import make_producer_node
    from nodes.critic import critic_node
    from nodes.orchestrator import orchestrator_node
    from nodes.deduper import deduper_node
    from nodes.digest_compiler import digest_compiler_node

    g = StateGraph(GraphState)

    g.add_node("loader", loader_node)

    for st in SignalType:
        g.add_node(f"producer_{st.value}", make_producer_node(st))

    g.add_node("critic", critic_node)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("deduper", deduper_node)
    g.add_node("digest_compiler", digest_compiler_node)

    # ─ Loader → 6 producers (cold start, parallel super-step) ─
    g.add_edge(START, "loader")
    for st in SignalType:
        g.add_edge("loader", f"producer_{st.value}")
        g.add_edge(f"producer_{st.value}", "critic")

    # ─ Critic always edges to orchestrator. ─
    # The orchestrator owns the retry-vs-done decision and returns a
    # Command whose goto is either a list of Sends (re-fire specific
    # producers with retry_companies_<type>) or "deduper".
    g.add_edge("critic", "orchestrator")

    g.add_edge("deduper", "digest_compiler")
    g.add_edge("digest_compiler", END)

    return g.compile()
