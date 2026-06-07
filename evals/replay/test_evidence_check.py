"""
Offline replay tests for the evidence_traceable clamp.

Goal: catch code regressions deterministically, every commit, with zero
network and zero LLM cost. The clamp is the single most load-bearing piece
of the architecture — if it stops clamping hallucinated quotes, the digest
loses its trust property.

Strategy:
  - Per-snapshot test loads `<snapshot>.expected.json` for assertion config
  - The HTML fixture is served by mocking httpx.get to return its contents
  - check_evidence then runs against the mock, scored normally
  - Test asserts score + a substring of the reason

Add a new snapshot by:
  1. Drop `<name>.html` (or reuse an existing one) + `<name>.expected.json`
     into `evals/replay/snapshots/`.
  2. Tests pick it up automatically via the `pytest.fixture` discovery below.

Run:
    pytest evals/replay/test_evidence_check.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.evidence_check import check_evidence


_SNAPSHOTS = Path(__file__).parent / "snapshots"


def _expected_specs() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for p in sorted(_SNAPSHOTS.glob("*.expected.json")):
        spec = json.loads(p.read_text())
        out.append((p.name, spec))
    return out


@pytest.mark.parametrize("name,spec", _expected_specs())
def test_evidence_check_snapshot(name: str, spec: dict):
    fixture_path = _SNAPSHOTS / spec["fixture"]
    assert fixture_path.exists(), f"missing HTML fixture: {fixture_path}"
    html = fixture_path.read_text()

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = html

    with patch("tools.evidence_check.httpx.get", return_value=fake_resp):
        result = check_evidence("https://test.local/" + spec["fixture"], spec["evidence_quote"])

    assert result.score == spec["expected"]["score"], (
        f"{name}: expected score={spec['expected']['score']} got {result.score} "
        f"(reason={result.reason!r})"
    )
    assert spec["expected"]["reason_contains"].lower() in result.reason.lower(), (
        f"{name}: expected reason to contain {spec['expected']['reason_contains']!r}, "
        f"got {result.reason!r}"
    )
