"""DDReport — the structured due diligence report contract.

Every prose section is a list of `Claim`s (each citing evidence chunks or
computed metric IDs); rendering joins them. Free text is not allowed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.state import (
    AgentState,
    Claim,
    InsiderActivity,
    InsiderTransaction,
    InvestmentScorecard,
    MetricValue,
    PeerMultiple,
)

Severity = Literal["low", "medium", "high"]
Likelihood = Literal["low", "medium", "high"]


class CompanyMeta(BaseModel):
    name: str
    ticker: str
    cik: str
    exchange: str = ""
    sector: str = ""
    industry: str = ""


class MetricsTable(BaseModel):
    """Deterministically computed metrics shown in the financial health section."""

    metrics: list[MetricValue] = Field(default_factory=list)


class FinancialHealthSection(BaseModel):
    table: MetricsTable = Field(default_factory=MetricsTable)
    commentary: list[Claim] = Field(default_factory=list)


class RiskEntry(BaseModel):
    title: str
    severity: Severity
    likelihood: Likelihood
    claims: list[Claim] = Field(default_factory=list)


class ValuationSection(BaseModel):
    """Current valuation multiples, analyst price targets, and peer benchmarks."""
    pe_ttm: float | None = None
    forward_pe: float | None = None
    ev_to_ebitda: float | None = None
    price_to_sales: float | None = None
    price_to_book: float | None = None
    beta: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    recommendation: str = ""
    recommendation_mean: float | None = None
    num_analysts: int = 0
    short_percent_float: float | None = None
    short_ratio: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    peers: list[PeerMultiple] = Field(default_factory=list)
    commentary: list[Claim] = Field(default_factory=list)


class EarningsQualitySection(BaseModel):
    """Accruals ratio and cash conversion — deterministic earnings quality flags."""
    accruals_ratio: float | None = None    # (Net Income - OCF) / Revenue %
    cash_conversion: float | None = None   # OCF / Net Income x
    quality_label: str = ""               # "High", "Medium", "Low"
    flags: list[str] = Field(default_factory=list)
    commentary: list[Claim] = Field(default_factory=list)


class InsiderActivitySection(BaseModel):
    """Recent insider buys/sells from SEC Form 4 data (via yfinance)."""
    available: bool = False
    sentiment: str = ""           # "bullish", "bearish", "neutral"
    net_shares: float = 0.0
    net_value: float = 0.0
    transactions: list[InsiderTransaction] = Field(default_factory=list)
    commentary: list[Claim] = Field(default_factory=list)


class InvestmentScorecardSection(BaseModel):
    """Deterministically computed multi-dimensional investment scorecard (1-5 scale)."""
    available: bool = False
    dimensions: list[dict] = Field(default_factory=list)   # ScorecardDimension dicts
    composite_score: float = 0.0
    composite_label: str = ""   # "Strong Buy", "Buy", "Hold", "Reduce", "Sell"


class ReportMetadata(BaseModel):
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0
    model_versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DDReport(BaseModel):
    """The full due diligence report. The writer agent emits this schema."""

    company: CompanyMeta
    executive_summary: list[Claim] = Field(default_factory=list)
    business_overview: list[Claim] = Field(default_factory=list)
    financial_health: FinancialHealthSection = Field(default_factory=FinancialHealthSection)
    valuation: ValuationSection = Field(default_factory=ValuationSection)
    earnings_quality: EarningsQualitySection = Field(default_factory=EarningsQualitySection)
    insider_activity: InsiderActivitySection = Field(default_factory=InsiderActivitySection)
    scorecard: InvestmentScorecardSection = Field(default_factory=InvestmentScorecardSection)
    risk_matrix: list[RiskEntry] = Field(default_factory=list)
    recent_developments: list[Claim] = Field(default_factory=list)
    red_flags: list[Claim] = Field(default_factory=list)
    management_questions: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    metadata: ReportMetadata

    def all_claims(self) -> list[Claim]:
        """Every claim in the report, used by the critic and the renderer."""
        claims: list[Claim] = []
        claims.extend(self.executive_summary)
        claims.extend(self.business_overview)
        claims.extend(self.financial_health.commentary)
        claims.extend(self.valuation.commentary)
        claims.extend(self.earnings_quality.commentary)
        claims.extend(self.insider_activity.commentary)
        for risk in self.risk_matrix:
            claims.extend(risk.claims)
        claims.extend(self.recent_developments)
        claims.extend(self.red_flags)
        return claims

    def drop_claim(self, claim_id: str) -> bool:
        """Remove a claim everywhere it appears. Returns True if found."""
        found = False
        for section in (
            self.executive_summary,
            self.business_overview,
            self.financial_health.commentary,
            self.valuation.commentary,
            self.earnings_quality.commentary,
            self.insider_activity.commentary,
            self.recent_developments,
            self.red_flags,
            *[r.claims for r in self.risk_matrix],
        ):
            for claim in list(section):
                if claim.claim_id == claim_id:
                    section.remove(claim)
                    found = True
        return found


# Finalize the forward reference AgentState.report -> DDReport. The name is
# also injected into src.state's namespace so typing.get_type_hints() (used by
# LangGraph when introspecting the state schema) can resolve it.
import app.state as _state_module  # noqa: E402

setattr(_state_module, "DDReport", DDReport)  # noqa: B010
AgentState.model_rebuild(_types_namespace={"DDReport": DDReport})
