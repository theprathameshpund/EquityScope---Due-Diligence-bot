"""Filings research agent: agentic RAG over the indexed SEC filings.

Generates focused research questions, then per question: query rewrite →
payload-filtered dense retrieval → cross-encoder rerank → LLM relevance
grading → one corrective rewrite-and-retry when too few chunks survive.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.llm.router import BudgetExceededError, LLMRouter, load_prompt
from src.logging_setup import get_logger
from src.rag.retriever import retrieve
from src.state import AgentState, RetrievedEvidence

log = get_logger(__name__)

MIN_RELEVANT_CHUNKS = 2


class _Questions(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=12)


class _Rewrite(BaseModel):
    query: str


class _Grades(BaseModel):
    grades: list[str]


def _generate_questions(router: LLMRouter, state: AgentState) -> list[str]:
    system = load_prompt("filings_questions").format(
        company=state.company_name, ticker=state.ticker, focus=state.focus or "general"
    )
    result = router.complete_json(
        "fast", system, "Generate the research questions.", _Questions, max_tokens=600
    )
    questions = [q.strip() for q in result.questions if q.strip()]
    return questions[:8] if len(questions) > 8 else questions


def _rewrite_query(router: LLMRouter, question: str, hint: str = "") -> str:
    user = question if not hint else f"{question}\n\nNote: {hint}"
    try:
        result = router.complete_json(
            "fast", load_prompt("query_rewrite"), user, _Rewrite, max_tokens=120
        )
    except ValueError:
        return question
    return result.query.strip() or question


def _grade_relevance(
    router: LLMRouter, question: str, candidates: list[RetrievedEvidence]
) -> list[RetrievedEvidence]:
    if not candidates:
        return []
    numbered = "\n\n".join(
        f"[{i + 1}] {c.text[:600]}" for i, c in enumerate(candidates)
    )
    user = f"Question: {question}\n\nPassages:\n{numbered}"
    try:
        result = router.complete_json(
            "fast", load_prompt("relevance_grade"), user, _Grades, max_tokens=150
        )
    except ValueError:
        log.warning("relevance_grading_failed_keeping_all", question=question)
        return candidates
    kept: list[RetrievedEvidence] = []
    for i, candidate in enumerate(candidates):
        label = result.grades[i].strip().lower() if i < len(result.grades) else "yes"
        if label.startswith("y"):
            kept.append(candidate)
    return kept


def _research_question(
    router: LLMRouter, question: str, ticker: str
) -> list[RetrievedEvidence]:
    query = _rewrite_query(router, question)
    candidates = retrieve(query, ticker=ticker)
    relevant = _grade_relevance(router, question, candidates)
    if len(relevant) < MIN_RELEVANT_CHUNKS:
        # Corrective RAG: one rewrite with feedback, then retry.
        retry_query = _rewrite_query(
            router,
            question,
            hint=f"the query '{query}' returned mostly irrelevant passages; "
            "use different filing vocabulary",
        )
        retry_candidates = retrieve(retry_query, ticker=ticker)
        relevant.extend(_grade_relevance(router, question, retry_candidates))
    return relevant


def filings_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: research the filings index for the user's focus."""
    router = LLMRouter(state.run_id)
    try:
        questions = _generate_questions(router, state)
    except (BudgetExceededError, ValueError) as exc:
        log.warning("filings_question_generation_failed", error=str(exc))
        return {
            "research_questions": [],
            "data_gaps": [f"Filings research unavailable: {exc}"],
        }

    evidence: dict[str, RetrievedEvidence] = {}
    for question in questions:
        try:
            for item in _research_question(router, question, state.ticker):
                evidence.setdefault(item.chunk_id, item)
        except BudgetExceededError:
            log.warning("filings_budget_exhausted", answered_before_stop=question)
            break
        except Exception as exc:
            log.warning("filings_question_failed", question=question, error=str(exc))

    log.info("filings_research_done", questions=len(questions), chunks=len(evidence))
    updates: dict[str, Any] = {
        "research_questions": questions,
        "evidence": list(evidence.values()),
    }
    if not evidence:
        updates["data_gaps"] = ["No relevant filing evidence retrieved."]
    return updates
