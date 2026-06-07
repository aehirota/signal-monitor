"""
Thin wrapper around the Anthropic SDK direct. No LangChain wrappers.

Adapted from account-research-agent/tools/anthropic_client.py with the
SpendTracker integration: every call charges optimistically against the
per-run cap before hitting the API. If usage metadata is returned, the
charge is adjusted to actual.

The _heal_input() defensive coercion lives here so producers don't need to
re-implement it per node — same lesson as account-research-agent's Haiku
structured-output quirk.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional, Type, TypeVar

from anthropic import Anthropic
from pydantic import BaseModel

from tools.spend_tracker import SpendTracker

_client: Optional[Anthropic] = None
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)


# ─── Concurrency clamp (code-enforced rate-limit-aware orchestration) ─
#
# Caps the number of in-flight Anthropic API calls across the entire process.
# LangGraph runs 6 producers in parallel; without this clamp, all 6 can fire
# messages.create() at the same instant and burst past Anthropic's per-minute
# token ceiling (Tier 1: 30k Sonnet / 50k Haiku).
#
# Configurable via env var ANTHROPIC_MAX_CONCURRENT (default 2). When you
# graduate to Tier 2+, bump it to 4 or 6 to reclaim parallelism.
#
# This is the fourth instance of the code-enforced-rule pattern in the
# portfolio:
#   1. account-research-agent: disqualifier clamp
#   2. blog-autopilot:        length compliance
#   3. signal-monitor evidence_check: evidence_traceable clamp
#   4. signal-monitor concurrency:    rate-limit clamp


def _max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("ANTHROPIC_MAX_CONCURRENT", "2")))
    except (TypeError, ValueError):
        return 2


_CONCURRENCY_GATE = threading.BoundedSemaphore(_max_concurrent())


def client() -> Anthropic:
    global _client
    if _client is None:
        # max_retries enables built-in exponential backoff on 429 (rate limit)
        # and 5xx errors. Tier 1 Anthropic accounts hit 30k Sonnet / 50k Haiku
        # tokens/min, which 6 parallel producers can burst past — retries
        # smooth this out without architectural surgery. Bump if needed.
        _client = Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            max_retries=5,
        )
    return _client


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


# Anthropic pricing as of 2026 — used for spend tracking.
# Per million tokens. Update when Anthropic publishes new pricing.
_PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
}


def _cost_estimate_for(model: str, max_tokens: int) -> float:
    """Pessimistic pre-call estimate: assume ~2k input + full max_tokens output.

    Used to charge the SpendTracker BEFORE the API call. The handle is
    adjusted to actual cost after the call returns with usage data.
    """
    price = _PRICING.get(model, {"input": 3.00, "output": 15.00})  # conservative default
    est_input_tokens = 2_000
    return (
        est_input_tokens * price["input"] / 1_000_000
        + max_tokens * price["output"] / 1_000_000
    )


def _actual_cost(model: str, usage) -> float:
    price = _PRICING.get(model, {"input": 3.00, "output": 15.00})
    return (
        usage.input_tokens * price["input"] / 1_000_000
        + usage.output_tokens * price["output"] / 1_000_000
    )


def _heal_input(v):
    """Recursively coerce JSON-stringified lists/dicts back to actual types.

    Haiku sometimes emits structured tool-call inputs with list/dict fields
    serialized as JSON strings. This walks the input and tries to parse
    anything that looks like JSON.
    """
    if isinstance(v, dict):
        return {k: _heal_input(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_heal_input(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                return json.loads(s)
            except Exception:
                pass
    return v


def call_structured(
    model: str,
    system: str,
    user: str,
    schema: Type[T],
    max_tokens: int = 4096,
    cache_system: bool = True,
    *,
    tracker: SpendTracker,
    company_domain: str,
    label: str,
) -> T:
    """Force a tool call whose input matches `schema`. Returns validated model.

    Charges the tracker for the call. Raises BudgetExceeded if the cap
    would be hit.
    """
    tool_name = "emit_" + schema.__name__.lower()
    system_block = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system
    )

    est = _cost_estimate_for(model, max_tokens)
    with tracker.charge(company_domain, label, est_usd=est) as handle:
        with _CONCURRENCY_GATE:
            resp = client().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_block,
                tools=[
                    {
                        "name": tool_name,
                        "description": f"Emit a {schema.__name__} object.",
                        "input_schema": schema.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user}],
            )
        handle.adjust(_actual_cost(model, resp.usage))

    for block in resp.content:
        if block.type == "tool_use" and block.name == tool_name:
            return schema.model_validate(_heal_input(block.input))
    raise RuntimeError(
        f"Model did not emit the {tool_name} tool. Response: {resp.content}"
    )


def call_with_tools(
    model: str,
    system: str,
    user: str,
    tools: list[dict],
    max_tokens: int = 4096,
    cache_system: bool = True,
    *,
    tracker: SpendTracker,
    company_domain: str,
    label: str,
) -> str:
    """Agentic loop with arbitrary tools (e.g. Anthropic native web_search).

    Returns the concatenated final text content.
    """
    system_block = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system
    )
    est = _cost_estimate_for(model, max_tokens)
    with tracker.charge(company_domain, label, est_usd=est) as handle:
        with _CONCURRENCY_GATE:
            resp = client().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_block,
                tools=tools,
                messages=[{"role": "user", "content": user}],
            )
        handle.adjust(_actual_cost(model, resp.usage))
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n\n".join(parts).strip()


def call_text(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1024,
    cache_system: bool = True,
    *,
    tracker: SpendTracker,
    company_domain: str,
    label: str,
) -> str:
    """Plain text completion for short generative tasks (e.g. why_this_matters)."""
    system_block = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system
    )
    est = _cost_estimate_for(model, max_tokens)
    with tracker.charge(company_domain, label, est_usd=est) as handle:
        with _CONCURRENCY_GATE:
            resp = client().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_block,
                messages=[{"role": "user", "content": user}],
            )
        handle.adjust(_actual_cost(model, resp.usage))
    return resp.content[0].text
