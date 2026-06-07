"""
Code-enforced spend cap. Wraps every API call.

Runtime sibling of account-research-agent's disqualifier clamp: rules that
matter are enforced in code, not in prompts. Once total_spent reaches cap,
further calls raise BudgetExceeded.

Charging is OPTIMISTIC — we charge before the call. That makes the cap a
true ceiling (the next call can't push us over) rather than an after-the-fact
accounting tool. If the actual call ends up cheaper than the estimate,
adjust() back-fills the delta.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from state import SpendLedger


class BudgetExceeded(Exception):
    """Raised when an API call would push total_usd over the per-run cap."""


@dataclass
class SpendTracker:
    cap_usd: float
    per_company_soft_cap_usd: float = 0.25
    total_usd: float = 0.0
    per_company_usd: dict[str, float] = field(default_factory=dict)
    over_budget_companies: set[str] = field(default_factory=set)
    call_log: list[dict] = field(default_factory=list)

    @contextmanager
    def charge(
        self,
        company_domain: str,
        label: str,
        est_usd: float,
    ) -> Iterator["_ChargeHandle"]:
        """Context manager that charges est_usd before yielding.

        - If charging would exceed cap: raise BudgetExceeded BEFORE the
          wrapped call runs.
        - If charging exceeds per-company soft cap: mark over_budget;
          producer is responsible for honoring this between calls.
        - The yielded handle exposes .adjust(actual_usd) so the caller can
          true-up the estimate against the actual cost from the API response.
        """
        projected = self.total_usd + est_usd
        if projected > self.cap_usd:
            raise BudgetExceeded(
                f"Run cap ${self.cap_usd:.2f} would be exceeded by {label} "
                f"(current ${self.total_usd:.4f} + est ${est_usd:.4f})"
            )

        self.total_usd += est_usd
        current_company = self.per_company_usd.get(company_domain, 0.0) + est_usd
        self.per_company_usd[company_domain] = current_company
        if current_company > self.per_company_soft_cap_usd:
            self.over_budget_companies.add(company_domain)

        handle = _ChargeHandle(self, company_domain, label, est_usd)
        try:
            yield handle
        finally:
            self.call_log.append(
                {
                    "company": company_domain,
                    "label": label,
                    "est_usd": est_usd,
                    "actual_usd": handle.actual_usd if handle.actual_usd is not None else est_usd,
                }
            )

    def is_over_budget(self, company_domain: str) -> bool:
        return company_domain in self.over_budget_companies

    def to_ledger(self) -> SpendLedger:
        return SpendLedger(
            total_usd=round(self.total_usd, 6),
            per_company_usd={k: round(v, 6) for k, v in self.per_company_usd.items()},
            cap_usd=self.cap_usd,
            halted=False,
            halted_reason=None,
        )


@dataclass
class _ChargeHandle:
    """Returned inside the `charge` context. Caller can `adjust()` to swap
    the optimistic estimate for the actual cost once the API returns."""

    tracker: SpendTracker
    company_domain: str
    label: str
    estimate: float
    actual_usd: float | None = None

    def adjust(self, actual_usd: float) -> None:
        """Replace the estimate with the actual cost.

        Used after an API call returns usage metadata. Updates the tracker's
        totals so the per-run ledger reflects truth, not just our guess.
        """
        delta = actual_usd - self.estimate
        self.tracker.total_usd += delta
        self.tracker.per_company_usd[self.company_domain] = (
            self.tracker.per_company_usd.get(self.company_domain, 0.0) + delta
        )
        if (
            self.tracker.per_company_usd[self.company_domain]
            > self.tracker.per_company_soft_cap_usd
        ):
            self.tracker.over_budget_companies.add(self.company_domain)
        self.actual_usd = actual_usd
