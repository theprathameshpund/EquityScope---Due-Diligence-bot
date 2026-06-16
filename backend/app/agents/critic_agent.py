"""Critic agent: NLI entailment verification + deterministic numeric checks.

No LLM calls — verification is a local NLI cross-encoder plus exact numeric
matching against computed metrics, so the critic cannot hallucinate.
"""

from __future__ import annotations

import re
from typing import Any

from app.guardrails.grounding import best_entailment
from app.logging_setup import get_logger
from app.state import AgentState, Claim, CriticVerdict, MetricValue, RetrievedEvidence

log = get_logger(__name__)

# Synthetic chunk IDs injected by the writer from market/news/insider data.
# The critic trusts these as primary sources (they are never LLM-generated).
_MARKET_CHUNK_PREFIX = "mkt_"

_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w%]*\d)")
_SCALES = (1.0, 1e3, 1e6, 1e9, 1e12, 1e-2)
_REL_TOLERANCE = 0.005  # 0.5% — covers rounding to fewer decimals


def _synthetic_market_evidence(state: AgentState) -> list[RetrievedEvidence]:
    """Mirror writer synthetic chunks so critic can verify those citations."""
    chunks: list[RetrievedEvidence] = []
    if state.market and state.market.available:
        m = state.market
        lines = [f"Market data for {m.ticker} (source: Yahoo Finance):"]
        if m.price:
            lines.append(f"  Price: ${m.price:,.2f}")
        if m.change_1y_pct is not None:
            lines.append(f"  1-year price change: {m.change_1y_pct:+.2f}%")
        if m.market_cap:
            lines.append(f"  Market cap: ${m.market_cap/1e9:,.1f}B")
        if m.pe_ttm:
            lines.append(f"  Trailing P/E: {m.pe_ttm:.1f}x")
        if m.forward_pe:
            lines.append(f"  Forward P/E: {m.forward_pe:.1f}x")
        if m.ev_to_ebitda:
            lines.append(f"  EV/EBITDA: {m.ev_to_ebitda:.1f}x")
        if m.beta:
            lines.append(f"  Beta: {m.beta:.2f}")
        if m.sector:
            lines.append(f"  Sector: {m.sector}")
        if m.industry:
            lines.append(f"  Industry: {m.industry}")
        if m.summary:
            lines.append(f"  Market summary: {m.summary}")
        chunks.append(
            RetrievedEvidence(
                chunk_id=f"{_MARKET_CHUNK_PREFIX}snapshot",
                text="\n".join(lines),
                source_url=f"https://finance.yahoo.com/quote/{m.ticker}",
                form_type="market",
                fiscal_period="current",
                section="Market Snapshot",
                score=0.9,
            )
        )

    if state.news and state.news.available and state.news.items:
        news_lines = ["Recent news headlines (source: Google News RSS):"]
        for item in state.news.items[:12]:
            news_lines.append(f"  [{item.sentiment.upper()}] {item.title}")
        chunks.append(
            RetrievedEvidence(
                chunk_id=f"{_MARKET_CHUNK_PREFIX}news",
                text="\n".join(news_lines),
                source_url="https://news.google.com",
                form_type="news",
                fiscal_period="current",
                section="Recent News",
                score=0.8,
            )
        )

    if state.insider_activity and state.insider_activity.available:
        ia = state.insider_activity
        insider_lines = [
            f"Insider activity for {state.ticker}:",
            f"  Net sentiment: {ia.sentiment.upper()}",
            f"  Net shares: {ia.net_shares:,.0f}",
        ]
        chunks.append(
            RetrievedEvidence(
                chunk_id=f"{_MARKET_CHUNK_PREFIX}insiders",
                text="\n".join(insider_lines),
                source_url=f"https://finance.yahoo.com/quote/{state.ticker}/insider-transactions",
                form_type="insider",
                fiscal_period="trailing12m",
                section="Insider Activity",
                score=0.85,
            )
        )
    return chunks


def _numbers_in(text: str) -> list[float]:
    values: list[float] = []
    for raw in _NUMBER_RE.findall(text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        # Years and small counts are prose, not financial figures.
        if 1900 <= value <= 2100 and "." not in raw:
            continue
        if value < 10 and "." not in raw:
            continue
        values.append(value)
    return values


def _matches(stated: float, source: float) -> bool:
    for scale in _SCALES:
        target = source / scale
        if target == 0:
            continue
        if abs(stated - target) <= abs(target) * _REL_TOLERANCE + 1e-9:
            return True
        # Also allow the source rounded to the precision the writer used.
        if abs(stated - round(target, 1)) <= 0.051 or abs(stated - round(target)) <= 0.51:
            return True
    return False


def verify_numbers(claim: Claim, metrics: list[MetricValue], chunk_texts: list[str]) -> bool:
    """Every number in the claim must trace to a metric (value or input) or
    appear in a cited chunk."""
    stated_numbers = _numbers_in(claim.text)
    if not stated_numbers:
        return True
    allowed: list[float] = []
    referenced = [m for m in metrics if m.metric_id in claim.metric_ids] or metrics
    for metric in referenced:
        allowed.append(metric.value)
        allowed.extend(metric.inputs.values())
    chunk_numbers = [n for text in chunk_texts for n in _numbers_in(text)]
    allowed.extend(chunk_numbers)
    return all(
        any(_matches(stated, source) for source in allowed) for stated in stated_numbers
    )


def _verify_claim(
    claim: Claim,
    evidence_by_id: dict[str, RetrievedEvidence],
    metrics: list[MetricValue],
) -> CriticVerdict:
    cited_texts = [
        evidence_by_id[cid].text
        for cid in claim.citation_chunk_ids
        if cid in evidence_by_id
    ]

    # Market/news chunks are primary data sources — skip NLI entailment for them,
    # only check numeric consistency.
    market_only = claim.citation_chunk_ids and all(
        cid.startswith(_MARKET_CHUNK_PREFIX) for cid in claim.citation_chunk_ids
    )

    # 1) Provenance: a claim must cite chunks and/or metrics.
    if not cited_texts and not claim.metric_ids:
        return CriticVerdict(
            claim_id=claim.claim_id,
            entailment_score=0.0,
            verdict="no_citation",
            feedback="Claim has no citation; cite a chunk_id or metric_id.",
        )

    # 2) Numbers must match computed metrics / cited source text exactly.
    if not verify_numbers(claim, metrics, cited_texts):
        return CriticVerdict(
            claim_id=claim.claim_id,
            entailment_score=0.0,
            verdict="numeric_mismatch",
            feedback="Numbers in the claim do not match the computed metrics "
            "or the cited source text.",
        )

    # 3) Market/news-only citations: numeric check passed, trust as supported.
    if market_only:
        return CriticVerdict(
            claim_id=claim.claim_id,
            entailment_score=0.85,
            verdict="supported",
            feedback="Supported by market/news data.",
        )

    # 4) Filing-backed qualitative claims need NLI entailment.
    if cited_texts:
        from app.config import settings

        score = best_entailment(claim.text, cited_texts)
        if score < settings.critic_entailment_threshold:
            return CriticVerdict(
                claim_id=claim.claim_id,
                entailment_score=score,
                verdict="unsupported",
                feedback="not supported by cited source",
            )
        return CriticVerdict(
            claim_id=claim.claim_id, entailment_score=score, verdict="supported"
        )

    # Metric-only claim with matching numbers.
    return CriticVerdict(claim_id=claim.claim_id, entailment_score=1.0, verdict="supported")


def critic_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: verify every claim in the draft report."""
    if state.report is None:
        return {"critic_verdicts": [], "status": "critic_skipped"}

    evidence_by_id = {e.chunk_id: e for e in state.evidence}
    for chunk in _synthetic_market_evidence(state):
        evidence_by_id[chunk.chunk_id] = chunk
    metrics = state.analysis.metrics if state.analysis else []

    verdicts: list[CriticVerdict] = []
    for claim in state.report.all_claims():
        verdict = _verify_claim(claim, evidence_by_id, metrics)
        claim.verification_status = (
            "supported" if verdict.verdict == "supported" else "unsupported"
        )
        verdicts.append(verdict)

    n_failed = sum(1 for v in verdicts if v.verdict != "supported")
    log.info("critic_done", total=len(verdicts), failed=n_failed,
             revision_count=state.revision_count)
    return {"critic_verdicts": verdicts, "report": state.report, "status": "criticized"}
