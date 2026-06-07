"""
SQLite identity-key store. Single table; the schema IS the dedup contract.

A signal lives forever in this table (audit). Its `status` reflects whether
it should still influence dedup decisions:
  - `active`   — within its type's recency window; appears in dedup_keys_active
  - `expired`  — past its recency window; does not block re-surfacing
  - `superseded` — manually marked (future v2: e.g. an updated funding amount)

`active`/`expired` is COMPUTED at query time from `signal_date` vs.
type-specific recency. We don't run a sweeper — recency is a window over
data we already store, not a state to maintain.

Queries:
  - load_active_keys(recency_by_type) → set[str]  # used by loader
  - insert_signal(typed_signal) → bool            # used by deduper
  - mark_expired() (no-op for now; status is computed at read)

Cost discipline: this is a single sqlite3 connection opened per run, used
read-mostly. No pooling, no migrations framework. v1 simplicity.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from schemas.signal_types import AnySignal


_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    identity_key   TEXT PRIMARY KEY,
    company_domain TEXT NOT NULL,
    signal_type    TEXT NOT NULL,
    signal_date    TEXT NOT NULL,  -- ISO YYYY-MM-DD
    first_seen_at  TEXT NOT NULL,  -- ISO date of first time we wrote this key
    latest_url     TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_signals_company_type
    ON signals (company_domain, signal_type);

CREATE INDEX IF NOT EXISTS idx_signals_status
    ON signals (status);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def load_active_keys(
    conn: sqlite3.Connection,
    recency_by_type: dict[str, int],
) -> set[str]:
    """Return identity_keys still inside their type's recency window.

    `recency_by_type` is a dict like `{"funding_round": 120, "exec_move": 90, ...}`.
    For each type we ONLY consider signals whose `signal_date` is within
    `today - recency_days` for that type.
    """
    today = date.today()
    active: set[str] = set()
    if not recency_by_type:
        return active

    cur = conn.cursor()
    for signal_type, days in recency_by_type.items():
        cutoff = (today - timedelta(days=days)).isoformat()
        cur.execute(
            "SELECT identity_key FROM signals "
            "WHERE status = 'active' "
            "  AND signal_type = ? "
            "  AND signal_date >= ?",
            (signal_type, cutoff),
        )
        active.update(row["identity_key"] for row in cur.fetchall())
    return active


def insert_signal(conn: sqlite3.Connection, signal: AnySignal) -> bool:
    """Insert a new signal. Returns True if inserted, False if it already existed.

    Race-safe via PRIMARY KEY constraint — if `identity_key` exists, we
    treat that as "already seen" and skip silently.
    """
    today_iso = date.today().isoformat()
    payload = signal.model_dump(mode="json")
    try:
        conn.execute(
            "INSERT INTO signals "
            "(identity_key, company_domain, signal_type, signal_date, "
            " first_seen_at, latest_url, payload_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            (
                signal.identity_key(),
                signal.company_domain,
                signal.type.value,
                signal.signal_date.isoformat(),
                today_iso,
                str(signal.source_url),
                json.dumps(payload, default=str),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def count_signals(conn: sqlite3.Connection, status: str = "active") -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM signals WHERE status = ?", (status,))
    return int(cur.fetchone()["n"])
