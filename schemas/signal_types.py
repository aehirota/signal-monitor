"""
Pydantic schemas for the six v1 signal types.

The taxonomy IS the contract. Anything that doesn't fit a type gets dropped
upstream of the critic. Each type defines its own identity_key() — used by
the dedup layer to collapse "same Series B reported across 5 outlets" into
one signal identity.

Adding a new type means:
  1. New class subclassing BaseSignal
  2. SignalType enum entry
  3. identity_key() implementation
  4. recency window in config.yaml
  5. primary source in config.yaml
  6. producer prompt in prompts/
  7. >= 5 replay snapshots under evals/replay/snapshots/
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ─── Enum ───────────────────────────────────────────────────


class SignalType(str, Enum):
    funding_round = "funding_round"
    exec_move = "exec_move"
    relevant_jd = "relevant_jd"
    product_launch = "product_launch"
    tech_signal = "tech_signal"
    news_strategic = "news_strategic"


# ─── Base ───────────────────────────────────────────────────


class BaseSignal(BaseModel):
    """Common fields every signal carries.

    Concrete subclasses add the typed fields that make the signal
    identifiable + actionable, and implement identity_key() for dedup.
    """

    company_domain: str = Field(description="e.g. vercel.com")
    type: SignalType
    signal_date: date = Field(description="When the signal event occurred per its source")
    source_url: HttpUrl
    evidence_quote: str = Field(
        description="Verbatim snippet from source_url proving the signal. "
        "Code-enforced check: this must appear in the rendered page text."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    raw_source: Literal["exa", "tavily", "perplexity", "anthropic_web_search"]

    @field_validator("signal_date", mode="before")
    @classmethod
    def _heal_date(cls, v):
        """Coerce LLM-emitted malformed dates.

        Observed in live runs:
          - '2026-06-00' (day=0): LLM hallucinates day for partially-dated
            sources like 'June 2026'. Coerce to day=1.
          - '2026-13-15' (month=13): coerce to month=12.

        Anything still invalid after these passes errors normally —
        emitting a bad date is the LLM's failure, not ours to bury further.
        """
        if isinstance(v, str):
            m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v.strip())
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if d == 0:
                    d = 1
                if mo == 0:
                    mo = 1
                if mo > 12:
                    mo = 12
                v = f"{y:04d}-{mo:02d}-{d:02d}"
        return v

    def identity_key(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError(
            f"{self.__class__.__name__}.identity_key() must be implemented "
            "so the dedup layer can collapse cross-outlet duplicates."
        )


# ─── Concrete signal types ──────────────────────────────────


class FundingRoundSignal(BaseSignal):
    type: Literal[SignalType.funding_round] = SignalType.funding_round
    # Late_stage catches Series F+; some watchlist companies (Anthropic Series G/H,
    # Vercel Series F) raise rounds beyond the standard alphabet. Better to bucket
    # post-E than to lose the signal to Literal-validation errors.
    round_type: Literal[
        "seed",
        "series_a", "series_b", "series_c", "series_d", "series_e",
        "late_stage",
        "bridge", "debt", "ipo",
    ]
    amount_usd: Optional[int] = None
    lead_investor: Optional[str] = None

    def identity_key(self) -> str:
        ym = self.signal_date.strftime("%Y-%m")
        return f"{self.company_domain}::funding_round::{self.round_type}::{ym}"


class ExecMoveSignal(BaseSignal):
    type: Literal[SignalType.exec_move] = SignalType.exec_move
    role: str = Field(description="Normalized role (e.g. 'COO', 'VP Sales', 'Head of RevOps')")
    person_name: str
    move_kind: Literal["hire", "promotion", "departure", "interim"]

    def identity_key(self) -> str:
        # role + person uniquely identify a single move; normalize role
        role_norm = self.role.strip().lower().replace(" ", "_")
        person_norm = self.person_name.strip().lower().replace(" ", "_")
        return f"{self.company_domain}::exec_move::{role_norm}::{person_norm}"


class RelevantJDSignal(BaseSignal):
    type: Literal[SignalType.relevant_jd] = SignalType.relevant_jd
    role_title: str
    role_title_normalized: str = Field(
        description="Lowercased, whitespace-collapsed, no seniority qualifiers stripped"
    )
    location: Optional[str] = None
    job_id: Optional[str] = None

    def identity_key(self) -> str:
        return f"{self.company_domain}::relevant_jd::{self.role_title_normalized}"


class ProductLaunchSignal(BaseSignal):
    type: Literal[SignalType.product_launch] = SignalType.product_launch
    product_name: str
    launch_quarter: str = Field(description="e.g. '2026-Q2'")

    def identity_key(self) -> str:
        product_norm = self.product_name.strip().lower().replace(" ", "_")
        return f"{self.company_domain}::product_launch::{product_norm}::{self.launch_quarter}"


class TechSignalSignal(BaseSignal):
    type: Literal[SignalType.tech_signal] = SignalType.tech_signal
    tech_mentioned: str = Field(description="e.g. 'salesforce', 'clay', 'hubspot'")
    signal_action: Literal["evaluating", "migrating_to", "migrating_off", "adopted", "deprecated"]

    def identity_key(self) -> str:
        tech_norm = self.tech_mentioned.strip().lower().replace(" ", "_")
        return f"{self.company_domain}::tech_signal::{tech_norm}::{self.signal_action}"


class NewsStrategicSignal(BaseSignal):
    type: Literal[SignalType.news_strategic] = SignalType.news_strategic
    event_type: Literal["m_and_a", "ipo_file", "layoffs", "reorg", "leadership_shakeup"]

    def identity_key(self) -> str:
        ym = self.signal_date.strftime("%Y-%m")
        return f"{self.company_domain}::news_strategic::{self.event_type}::{ym}"


# ─── Union for state ────────────────────────────────────────


AnySignal = (
    FundingRoundSignal
    | ExecMoveSignal
    | RelevantJDSignal
    | ProductLaunchSignal
    | TechSignalSignal
    | NewsStrategicSignal
)


def signal_class_for(t: SignalType) -> type[BaseSignal]:
    """Map enum to concrete class. Producers use this for structured-output binding."""
    return {
        SignalType.funding_round: FundingRoundSignal,
        SignalType.exec_move: ExecMoveSignal,
        SignalType.relevant_jd: RelevantJDSignal,
        SignalType.product_launch: ProductLaunchSignal,
        SignalType.tech_signal: TechSignalSignal,
        SignalType.news_strategic: NewsStrategicSignal,
    }[t]
