"""Critic agent: NLI entailment verification + deterministic numeric checks.

No LLM calls — verification is a local NLI cross-encoder plus exact numeric
matching against computed metrics, so the critic cannot hallucinate.
"""

from __future__ import annotations

import re
from typing import Any

from src.guardrails.grounding import best_entailment
from src.logging_setup import get_logger
from src.state import AgentState, Claim, CriticVerdict, MetricValue, RetrievedEvidence

log = get_logger(__name__)

_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w%]*\d)")
_SCALES = (1.0, 1e3, 1e6, 1e9, 1e12, 1e-2)
_REL_TOLERANCE = 0.005  # 0.5% — covers rounding to fewer decimals


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

    # 3) Qualitative claims need NLI entailment from at least one cited chunk.
    if cited_texts:
        from src.config import settings

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
