"""
Loader node: reads watchlist + config, queries SQLite for active dedup keys,
honors PAUSE, initializes state.

Day-4: wires SQLite via storage.sqlite_store.load_active_keys.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from state import GraphState, SpendLedger
from storage import sqlite_store


PAUSE_FILE = Path(".tmp/PAUSE")


def loader_node(state: GraphState) -> dict:
    if PAUSE_FILE.exists():
        print(f"[loader] paused via {PAUSE_FILE}; exiting", file=sys.stderr)
        return {
            "config": {},
            "watchlist": [],
            "dedup_keys_active": set(),
            "spend": SpendLedger(),
        }

    config = yaml.safe_load(Path("config.yaml").read_text())

    incoming = state.get("config") if state else None
    only = (incoming or {}).get("only_domain")
    dry_run = (incoming or {}).get("dry_run", False)
    if only:
        config["only_domain"] = only
    config["dry_run"] = dry_run

    watchlist_path = config.get("watchlist", {}).get("path", "watchlist.yaml")
    watchlist_doc = yaml.safe_load(Path(watchlist_path).read_text())
    companies = watchlist_doc.get("companies", [])
    if only:
        companies = [c for c in companies if c.get("domain") == only]

    # Per-run audit directory.
    run_id = state.get("run_id") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    runs_dir = Path(config.get("logging", {}).get("runs_dir", ".tmp/runs")) / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)

    # SQLite dedup: load identity_keys still inside their type's recency window.
    db_path = config.get("storage", {}).get("sqlite_path", ".tmp/signals.sqlite")
    recency_by_type = {
        st: cfg["recency_days"]
        for st, cfg in config.get("signal_types", {}).items()
    }
    conn = sqlite_store.connect(db_path)
    try:
        dedup_active = sqlite_store.load_active_keys(conn, recency_by_type)
        total_stored = sqlite_store.count_signals(conn, status="active")
    finally:
        conn.close()

    print(
        f"[loader] watchlist={len(companies)} active_keys={len(dedup_active)} "
        f"total_stored_active={total_stored} run_id={run_id}",
        file=sys.stderr,
    )

    return {
        "config": config,
        "watchlist": companies,
        "dedup_keys_active": dedup_active,
        "run_id": run_id,
        "iteration": 0,
        "spend": SpendLedger(
            cap_usd=config.get("budget", {}).get("per_run_cap_usd", 4.0)
        ),
    }
