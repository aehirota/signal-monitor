"""
Per-run audit log writer.

Writes the full run snapshot to `.tmp/runs/<run_id>/run.json`:
  - run_id, started_at, finished_at
  - watchlist + config snapshot
  - candidates_count, passed_count, failed_count
  - final digest_text
  - spend ledger (total + per-company)
  - call log (every API call made, with cost)

Same audit-trail-by-default pattern as the Am-Finn weekly report —
the data the digest summarizes is recoverable from the snapshot, so we
can reproduce or back-fill historical claims.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_audit(
    runs_dir: Path,
    run_id: str,
    payload: dict[str, Any],
) -> Path:
    """Write `run.json` under runs_dir/<run_id>/ and return the written path."""
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "run.json"
    payload = {**payload, "finished_at": datetime.now(timezone.utc).isoformat()}
    out.write_text(json.dumps(payload, indent=2, default=str))
    return out
