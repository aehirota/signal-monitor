"""
Digest compiler: renders the weekly digest with why_this_matters per signal,
then writes the per-run audit log.

Day-2: stdout-printing digest.
Day-4: also persists `.tmp/runs/<run_id>/run.json` audit snapshot.
Day-5: wires Resend.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from schemas.signal_types import AnySignal
from state import GraphState
from storage import audit_log, sqlite_store
from tools import anthropic_client, resend_client
from tools.spend_tracker import BudgetExceeded, SpendTracker


def _why_this_matters(signal: AnySignal, model: str, tracker: SpendTracker) -> str:
    system = anthropic_client.load_prompt("why_this_matters")
    user = (
        f"Signal:\n{signal.model_dump_json(indent=2)}\n\n"
        f"Write one sentence (max 25 words) explaining why this matters."
    )
    try:
        return anthropic_client.call_text(
            model=model,
            system=system,
            user=user,
            max_tokens=120,
            tracker=tracker,
            company_domain=signal.company_domain,
            label="digest_why_this_matters",
        ).strip()
    except BudgetExceeded:
        return "(why-this-matters skipped — budget cap reached)"


def _company_priority(state: GraphState, domain: str) -> int:
    for entry in state.get("watchlist", []):
        if entry.get("domain") == domain:
            return {"high": 0, "normal": 1, "low": 2}.get(entry.get("priority", "normal"), 1)
    return 1


def _block_for(signal: AnySignal, why: str) -> str:
    domain_upper = signal.company_domain.upper()
    type_tag = signal.type.value
    type_specific = {
        "relevant_jd": lambda s: f"Open req: {getattr(s, 'role_title', '?')} ({getattr(s, 'location', None) or 'location not stated'})",
        "exec_move": lambda s: f"{getattr(s, 'move_kind', '?')}: {getattr(s, 'person_name', '?')} as {getattr(s, 'role', '?')}",
        "funding_round": lambda s: f"{getattr(s, 'round_type', '?')}: ${getattr(s, 'amount_usd', 0) or 0:,} from {getattr(s, 'lead_investor', None) or 'undisclosed lead'}",
        "product_launch": lambda s: f"Launched: {getattr(s, 'product_name', '?')} ({getattr(s, 'launch_quarter', '?')})",
        "tech_signal": lambda s: f"{getattr(s, 'signal_action', '?')}: {getattr(s, 'tech_mentioned', '?')}",
        "news_strategic": lambda s: f"Event: {getattr(s, 'event_type', '?')}",
    }.get(type_tag, lambda s: "(no headline mapper)")(signal)

    return (
        f"{domain_upper}  [{type_tag}]\n"
        f"{type_specific} (signal_date: {signal.signal_date.isoformat()}, "
        f"confidence: {signal.confidence:.2f})\n"
        f"URL: {signal.source_url}\n"
        f"Why this matters: {why}"
    )


def _maybe_send_email(state: GraphState, subject: str, text: str) -> str | None:
    """Send via Resend unless we're in dry-run mode. Failures don't crash the run."""
    dry_run = state.get("config", {}).get("dry_run", False)
    if dry_run:
        return None
    try:
        msg_id = resend_client.send_digest(subject=subject, text=text)
        print(f"[digest_compiler] sent via Resend id={msg_id}", file=sys.stderr)
        return msg_id
    except Exception as e:
        # Resend down or misconfigured shouldn't lose the digest — it's already
        # in the audit log + stdout. Log + continue.
        print(
            f"[digest_compiler] Resend send FAILED ({type(e).__name__}: {e}); "
            "digest preserved in audit log + stdout",
            file=sys.stderr,
        )
        return None


def _persist_audit(state: GraphState, digest_text: str, total_spend: float, spend_in=None) -> None:
    config = state.get("config", {})
    runs_dir = Path(config.get("logging", {}).get("runs_dir", ".tmp/runs"))
    db_path = config.get("storage", {}).get("sqlite_path", ".tmp/signals.sqlite")

    # Count of persistent signals after this run for footer / audit.
    try:
        conn = sqlite_store.connect(db_path)
        try:
            stored_count = sqlite_store.count_signals(conn, status="active")
        finally:
            conn.close()
    except Exception:
        stored_count = -1

    from state import SpendLedger as _SpendLedger
    sl = spend_in if spend_in is not None else (state.get("spend") or _SpendLedger(cap_usd=4.0))
    critique = state.get("critique")
    audit_payload = {
        "run_id": state.get("run_id"),
        "watchlist_size": len(state.get("watchlist", [])),
        "candidate_count": len(critique.signals_passed) + len(critique.signals_failed) if critique else 0,
        "passed_count": len(critique.signals_passed) if critique else 0,
        "failed_count": len(critique.signals_failed) if critique else 0,
        "surfaced_count": len(state.get("signals_after_dedup", [])),
        "stored_active_total": stored_count,
        "iterations": state.get("iteration", 0),
        "spend": {
            "total_usd": total_spend,
            "cap_usd": sl.cap_usd,
            "per_company_usd": dict(sl.per_company_usd),
        },
        "digest_text": digest_text,
        "failed_signals": critique.signals_failed if critique else [],
        "config_snapshot": {
            k: v for k, v in config.items() if k != "models"  # keep snapshot light
        },
    }
    try:
        out = audit_log.write_audit(runs_dir, state.get("run_id", "unknown"), audit_payload)
        print(f"[digest_compiler] audit written to {out}", file=sys.stderr)
    except Exception as e:
        print(f"[digest_compiler] audit write failed: {type(e).__name__}: {e}", file=sys.stderr)


def digest_compiler_node(state: GraphState) -> dict:
    config = state["config"]
    signals: list[AnySignal] = state.get("signals_after_dedup", []) or []

    from state import SpendLedger as _SpendLedger
    spend_in = state.get("spend") or _SpendLedger(cap_usd=4.0)
    tracker = SpendTracker(
        cap_usd=spend_in.cap_usd,
        per_company_soft_cap_usd=config["budget"]["per_company_soft_cap_usd"],
        total_usd=spend_in.total_usd,
        per_company_usd=dict(spend_in.per_company_usd),
    )

    watchlist_n = len(state.get("watchlist", []))

    if not signals:
        subject_no = f"Signal Monitor — no new signals ({date.today().isoformat()})"
        body_no = (
            f"No new signals across {watchlist_n} companies — system healthy.\n\n"
            f"—\n"
            f"Watchlist: {watchlist_n} companies | "
            f"Run cost: ${spend_in.total_usd:.2f} / "
            f"${spend_in.cap_usd:.2f} cap"
        )
        text = f"Subject: {subject_no}\n\n{body_no}"
        _persist_audit(state, text, total_spend=spend_in.total_usd, spend_in=spend_in)
        _maybe_send_email(state, subject=subject_no, text=body_no)
        return {"digest_text": text, "digest_html": text}

    signals_sorted = sorted(
        signals,
        key=lambda s: (
            _company_priority(state, s.company_domain),
            -s.signal_date.toordinal(),
        ),
    )

    blocks: list[str] = []
    model = config["models"]["digest_compiler"]
    for s in signals_sorted:
        why = _why_this_matters(s, model, tracker)
        blocks.append(_block_for(s, why))

    distinct_companies = len({s.company_domain for s in signals_sorted})
    subject_line = (
        f"Signal Monitor — {len(signals_sorted)} new signals "
        f"across {distinct_companies} companies ({date.today().isoformat()})"
    )
    body = "\n\n".join(
        [
            *blocks,
            "—",
            (
                f"Watchlist: {watchlist_n} companies | "
                f"Signals this week: {len(signals_sorted)} | "
                f"Run cost: ${tracker.total_usd:.2f} / ${spend_in.cap_usd:.2f} cap"
            ),
            "Pause: touch .tmp/PAUSE",
        ]
    )
    text = f"Subject: {subject_line}\n\n{body}"

    _persist_audit(state, text, total_spend=tracker.total_usd, spend_in=spend_in)
    _maybe_send_email(state, subject=subject_line, text=body)
    return {"digest_text": text, "digest_html": text}
