"""
Entry point. Loaded by launchd Saturday 7am, also runs manually:

    python run.py                     # full watchlist
    python run.py --only retool.com   # single domain, smoke-test mode

Day-2: produces a real digest to stdout for relevant_jd signals only.
Day-3-5 fill in remaining producers, Resend wiring, launchd.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


PAUSE_FILE = Path(".tmp/PAUSE")
JSON_SCHEMA_VERSION = "1.0"


def _build_json_payload(final: dict) -> dict:
    """Build the public JSON contract for downstream consumers (e.g. meeting-prep-agent).

    Stable schema — bump JSON_SCHEMA_VERSION on breaking changes. See docs/json-api.md.
    """
    signals = final.get("signals_after_dedup") or []
    spend = final.get("spend")
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "run_id": final.get("run_id"),
        "watchlist_size": len(final.get("watchlist", [])),
        "signals": [s.model_dump(mode="json") for s in signals],
        "signals_count": len(signals),
        "digest_text": final.get("digest_text", ""),
        "spend_total_usd": float(getattr(spend, "total_usd", 0.0)),
        "spend_cap_usd": float(getattr(spend, "cap_usd", 0.0)),
    }


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(prog="signal-monitor")
    parser.add_argument("--only", help="Restrict to a single watchlist domain")
    parser.add_argument(
        "--print-graph", action="store_true",
        help="Just print graph topology and exit (Day-1 smoke test).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip Resend email send; print digest to stdout only.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print full result as JSON to stdout (diagnostics → stderr). "
        "Implies --dry-run (JSON consumers should not trigger email side effects). "
        "Stable contract for downstream consumers. See docs/json-api.md.",
    )
    args = parser.parse_args()

    # --json implies --dry-run so subprocess consumers can't accidentally
    # trigger an email send during composition.
    if args.json:
        args.dry_run = True

    if PAUSE_FILE.exists():
        print(f"[signal-monitor] paused via {PAUSE_FILE}; exiting without run")
        return 0

    from graph import build_graph

    graph = build_graph()

    if args.print_graph:
        print(f"[signal-monitor] graph compiled OK with {len(graph.nodes)} nodes")
        for n in sorted(graph.nodes):
            print(f"  - {n}")
        return 0

    initial_config: dict = {}
    if args.only:
        initial_config["only_domain"] = args.only
    if args.dry_run:
        initial_config["dry_run"] = True

    initial_state = {
        "run_id": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"),
        "iteration": 0,
        "config": initial_config,
    }

    if args.json:
        # JSON mode: route all diagnostic prints to stderr so stdout stays
        # a clean JSON payload for subprocess consumers.
        with redirect_stdout(sys.stderr):
            final = graph.invoke(initial_state)
        print(json.dumps(_build_json_payload(final), indent=2, default=str))
        return 0

    final = graph.invoke(initial_state)

    print()
    print(final.get("digest_text", "(no digest_text produced)"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
