"""Shared typed state for the EquityScope agent graph.

Every agent communicates exclusively through these Pydantic models inside
the LangGraph ``AgentState``. There are no free-form text handoffs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.report.schema import DDReport

SentimentLabel = Literal["positive", "negative", "neutral"]
VerificationStatus = Literal["unverified", "supported", "unsupported", "dropped"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Short unique identifier with a readable prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RetrievedEvidence(BaseModel):
    """One retrieved + reranked chunk of a filing, citable by claims."""

    chunk_id: str
    text: str
    source_url: str
    form_type: str
    fiscal_period: str
    section: str
    score: float = 0.0


class PeerMultiple(BaseModel):
    ticker: str
    pe_ttm: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    price_to_book: float | None = None
    sector: str = ""


class MarketSnapshot(BaseModel):
    available: bool = True
    error: str | None = None
    ticker: str = ""
    as_of: datetime = Field(default_factory=_utcnow)
    price: float | None = None
    change_1y_pct: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap: float | None = None
    pe_ttm: float | None = None
    forward_pe: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    price_to_book: float | None = None
    beta: float | None = None
    sector: str = ""
    industry: str = ""
    officers: list[dict[str, str | int | float | None]] = Field(default_factory=list)
    # Analyst consensus & price targets (source: Yahoo Finance)
    recommendation: str = ""          # e.g. "buy", "hold", "sell", "strong_buy"
    recommendation_mean: float | None = None  # 1=Strong Buy, 5=Strong Sell
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    num_analysts: int = 0
    # Short interest
    short_percent_float: float | None = None  # % of float sold short
    short_ratio: float | None = None           # days to cover
    # Dividends
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    peers: list[PeerMultiple] = Field(default_factory=list)
    macro_notes: list[str] = Field(default_factory=list)
    summary: str = ""


class InsiderTransaction(BaseModel):
    """One SEC Form 4 insider buy or sell transaction."""
    name: str
    title: str = ""
    transaction_type: str   # "Purchase" or "Sale"
    shares: float
    value: float | None = None
    date: str


class InsiderActivity(BaseModel):
    """Aggregated insider activity over the lookback window."""
    available: bool = False
    error: str | None = None
    transactions: list[InsiderTransaction] = Field(default_factory=list)
    net_shares: float = 0.0     # positive = net purchases, negative = net sales
    net_value: float = 0.0
    sentiment: str = ""         # "bullish", "bearish", "neutral"


class ScorecardDimension(BaseModel):
    """One dimension in the investment scorecard."""
    name: str
    score: int          # 1 (worst) to 5 (best)
    rationale: str
    metric_ids: list[str] = Field(default_factory=list)


class InvestmentScorecard(BaseModel):
    """Deterministically computed multi-dimensional investment scorecard."""
    available: bool = False
    dimensions: list[ScorecardDimension] = Field(default_factory=list)
    composite_score: float = 0.0
    composite_label: str = ""   # "Strong Buy", "Buy", "Hold", "Reduce", "Sell"


class NewsItem(BaseModel):
    title: str
    source: str = ""
    published_at: datetime | None = None
    url: str = ""
    snippet: str = ""
    sentiment: SentimentLabel = "neutral"


class NewsDigest(BaseModel):
    available: bool = True
    error: str | None = None
    items: list[NewsItem] = Field(default_factory=list)


class FactValue(BaseModel):
    """One XBRL fact taken verbatim from EDGAR companyfacts."""

    concept: str
    label: str
    unit: str
    value: float
    end_date: str
    start_date: str | None = None
    fiscal_year: int
    fiscal_period: str
    form: str
    accession: str


class FinancialFacts(BaseModel):
    cik: str
    entity_name: str
    facts: dict[str, list[FactValue]] = Field(default_factory=dict)
    """Mapping XBRL concept tag -> chronological annual/quarterly values."""


class MetricValue(BaseModel):
    """A deterministically computed metric, carrying its raw XBRL inputs."""

    metric_id: str
    name: str
    value: float
    unit: str
    period: str
    inputs: dict[str, float] = Field(default_factory=dict)
    formula: str = ""


class Claim(BaseModel):
    """One qualitative statement in the report, with mandatory provenance."""

    claim_id: str = Field(default_factory=lambda: new_id("clm"))
    text: str
    citation_chunk_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus = "unverified"
    section: str = ""


class FinancialAnalysis(BaseModel):
    metrics: list[MetricValue] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    commentary: list[Claim] = Field(default_factory=list)

    def metric_by_id(self, metric_id: str) -> MetricValue | None:
        return next((m for m in self.metrics if m.metric_id == metric_id), None)


class CriticVerdict(BaseModel):
    claim_id: str
    entailment_score: float
    verdict: Literal["supported", "unsupported", "no_citation", "numeric_mismatch"]
    feedback: str = ""


class RunBudget(BaseModel):
    token_limit: int
    tokens_used: int = 0
    cost_usd: float = 0.0

    @property
    def exceeded(self) -> bool:
        return self.tokens_used > self.token_limit


class ProgressEvent(BaseModel):
    """Published to Redis pub/sub at every node start/end, consumed by SSE."""

    run_id: str
    node: str
    status: Literal["start", "end", "error", "warning", "done"]
    message: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    ts: datetime = Field(default_factory=_utcnow)


def _append_unique(left: list[str], right: list[str]) -> list[str]:
    """LangGraph reducer for data_gaps: parallel nodes append, no duplicates."""
    return left + [item for item in right if item not in left]


def _merge_evidence(
    left: list[RetrievedEvidence], right: list[RetrievedEvidence]
) -> list[RetrievedEvidence]:
    """LangGraph reducer: append new evidence, dedup by chunk_id."""
    seen = {e.chunk_id for e in left}
    return left + [e for e in right if e.chunk_id not in seen]


class AgentState(BaseModel):
    """Top-level LangGraph state carried between agent nodes."""

    run_id: str
    company_input: str
    focus: str = ""
    started_at: datetime = Field(default_factory=_utcnow)

    ticker: str = ""
    cik: str = ""
    company_name: str = ""

    research_questions: list[str] = Field(default_factory=list)
    evidence: Annotated[list[RetrievedEvidence], _merge_evidence] = Field(default_factory=list)
    market: MarketSnapshot | None = None
    news: NewsDigest | None = None
    facts: FinancialFacts | None = None
    analysis: FinancialAnalysis | None = None
    insider_activity: InsiderActivity | None = None
    scorecard: InvestmentScorecard | None = None
    management_questions: list[str] = Field(default_factory=list)

    report: DDReport | None = None
    critic_verdicts: list[CriticVerdict] = Field(default_factory=list)
    revision_count: int = 0

    budget: RunBudget
    data_gaps: Annotated[list[str], _append_unique] = Field(default_factory=list)
    status: str = "pending"
    error: str | None = None


# NOTE: AgentState has a forward reference to DDReport (src/report/schema.py),
# which itself imports Claim from this module. schema.py finalizes the model
# via AgentState.model_rebuild() after DDReport is defined — import
# src.report.schema before instantiating AgentState.
