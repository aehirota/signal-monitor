"""
Code-enforced evidence-traceability clamp.

Fetches the source URL, renders to text, and checks whether the quoted
snippet actually appears in the rendered page. Catches the single most
common failure mode of search-based agents: hallucinated quotes attributed
to real URLs.

Returns 10 if the quote is present, 0 if not. No LLM involved.

Third instance of the code-over-prompt rule pattern:
  - account-research-agent: disqualifier clamp
  - blog-autopilot:        length compliance
  - signal-monitor:        evidence_traceable
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup


@dataclass
class EvidenceCheckResult:
    score: int                # 0 or 10
    reason: str               # human-readable; logged to audit
    rendered_length: int = 0  # how much text we extracted from the page


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_TRIM = str.maketrans("", "", "“”‘’\"'`…")


def normalize(s: str) -> str:
    """Lowercase, strip smart quotes/ellipses, collapse whitespace.

    Loose enough to handle minor quote-extraction artifacts (smart quotes
    around the snippet, ellipses inside it) without becoming so loose that
    random short substrings match.
    """
    s = s.lower().translate(_PUNCT_TRIM)
    s = _WHITESPACE_RE.sub(" ", s)
    return s.strip()


# Minimum quote length we'll accept for matching. Below this, false-positive
# substring matches become likely (a 4-word quote can appear in any page).
_MIN_QUOTE_LEN = 30


def check_evidence(
    source_url: str,
    evidence_quote: str,
    timeout: float = 8.0,
) -> EvidenceCheckResult:
    """Fetch URL, render to text, substring-match the evidence quote.

    Failure modes that score 0:
      - HTTP non-2xx
      - Rendered text shorter than the quote (likely SPA shell — JS required)
      - Quote not present after normalization
      - Quote shorter than _MIN_QUOTE_LEN (too short to be safely matched)
    """
    norm_quote = normalize(evidence_quote)
    if len(norm_quote) < _MIN_QUOTE_LEN:
        return EvidenceCheckResult(
            score=0,
            reason=f"evidence_quote too short ({len(norm_quote)} chars); minimum {_MIN_QUOTE_LEN}",
        )

    try:
        resp = httpx.get(
            source_url,
            headers=_BROWSER_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        return EvidenceCheckResult(score=0, reason=f"fetch failed: {type(e).__name__}: {e}")

    if resp.status_code >= 400:
        return EvidenceCheckResult(
            score=0,
            reason=f"source_url returned HTTP {resp.status_code}",
        )

    soup = BeautifulSoup(resp.text, "html.parser")
    # Drop script/style noise before extracting text.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    rendered = soup.get_text(" ", strip=True)
    norm_rendered = normalize(rendered)

    if len(norm_rendered) < len(norm_quote):
        return EvidenceCheckResult(
            score=0,
            reason="rendered text shorter than quote; page likely requires JS",
            rendered_length=len(norm_rendered),
        )

    if norm_quote in norm_rendered:
        return EvidenceCheckResult(
            score=10,
            reason="quote found in rendered page",
            rendered_length=len(norm_rendered),
        )

    return EvidenceCheckResult(
        score=0,
        reason="quote not found in rendered page",
        rendered_length=len(norm_rendered),
    )
