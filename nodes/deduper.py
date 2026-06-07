"""
Deduper: drops signals whose identity_key is already active, writes surviving
signals to SQLite so they don't re-surface in future runs inside their
recency window.

Day-4: wires SQLite persistence.
"""

from __future__ import annotations

import sys

from state import GraphState
from storage import sqlite_store


def deduper_node(state: GraphState) -> dict:
    critique = state.get("critique")
    if critique is None:
        return {"signals_after_dedup": []}

    active = state.get("dedup_keys_active", set())
    config = state.get("config", {})
    db_path = config.get("storage", {}).get("sqlite_path", ".tmp/signals.sqlite")

    survivors = []
    dropped_dup = 0
    inserted = 0

    conn = sqlite_store.connect(db_path)
    try:
        for signal in critique.signals_passed:
            key = signal.identity_key()
            if key in active:
                dropped_dup += 1
                continue
            # First-time-seen this run; persist + surface.
            if sqlite_store.insert_signal(conn, signal):
                inserted += 1
                survivors.append(signal)
            else:
                # PK collision — another row arrived between loader's read
                # and now (rare; only matters if two producers in the same
                # super-step extracted the same identity). Treat as dup.
                dropped_dup += 1
    finally:
        conn.close()

    print(
        f"[deduper] candidates_passed={len(critique.signals_passed)} "
        f"dropped_dup={dropped_dup} inserted={inserted}",
        file=sys.stderr,
    )

    return {"signals_after_dedup": survivors}
