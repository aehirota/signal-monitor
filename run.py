"""
Entry point. Loaded by launchd Saturday 7am, also runs manually:

    python run.py                     # full watchlist
    python run.py --only retool.com   # single domain, smoke-test mode

Day-2: produces a real digest to stdout for relevant_jd signals only.
Day-3-5 fill in remaining producers, Resend wiring, launchd.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


PAUSE_FILE = Path(".tmp/PAUSE")


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
    args = parser.parse_args()

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

    final = graph.invoke(initial_state)

    print()
    print(final.get("digest_text", "(no digest_text produced)"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
