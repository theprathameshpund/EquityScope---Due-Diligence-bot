"""Filings research agent: agentic RAG over the indexed SEC filings.

Generates focused research questions, then per question: query rewrite →
payload-filtered dense retrieval → cross-encoder rerank → LLM relevance
grading → one corrective rewrite-and-retry when too few chunks survive.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.llm.router import BudgetExceededError, LLMRouter, load_prompt
from app.logging_setup import get_logger
from app.rag.retriever import retrieve
from app.state import AgentState, RetrievedEvidence

log = get_logger(__name__)

MIN_RELEVANT_CHUNKS = 2
MAX_RESEARCH_QUESTIONS = 5


class _Questions(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=12)


class _Rewrite(BaseModel):
    query: str


class _Grades(BaseModel):
    grades: list[str]


def _fallback_questions(state: AgentState) -> list[str]:
    company = state.company_name or state.company_input or state.ticker
    focus = (state.focus or "").strip()
    questions = [
        f"What does {company} disclose about its business model and major revenue drivers in recent SEC filings?",
        f"What key risk factors does {company} disclose in its latest annual or quarterly filings?",
        f"What does {company} disclose about liquidity, debt, cash flows, and capital resources?",
        f"What competitive, regulatory, litigation, or cost-pressure risks does {company} disclose?",
        f"What recent material events or management updates appear in {company}'s SEC filings?",
    ]
    if focus:
        questions.insert(0, f"What does {company} disclose about {focus} in its SEC filings?")
        questions.insert(1, f"What risks or uncertainties related to {focus} does {company} disclose?")
    return questions[:MAX_RESEARCH_QUESTIONS]


def _generate_questions(router: LLMRouter, state: AgentState) -> list[str]:
    system = load_prompt("filings_questions").format(
        company=state.company_name, ticker=state.ticker, focus=state.focus or "general"
    )
    result = router.complete_json(
        "fast", system, "Generate the research questions.", _Questions, max_tokens=600
    )
    questions = [q.strip() for q in result.questions if q.strip()]
    return questions[:MAX_RESEARCH_QUESTIONS]


def _rewrite_query(router: LLMRouter, question: str, hint: str = "") -> str:
    user = question if not hint else f"{question}\n\nNote: {hint}"
    try:
        result = router.complete_json(
            "fast", load_prompt("query_rewrite"), user, _Rewrite, max_tokens=120
        )
    except Exception:
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
    user = (
        f"Question: {question}\n"
        "Primary period policy: prefer the latest fiscal year/current filing period; "
        "reject stale metrics unless the question explicitly asks for historical trend context.\n\n"
        f"Passages:\n{numbered}"
    )
    try:
        result = router.complete_json(
            "fast", load_prompt("relevance_grade"), user, _Grades, max_tokens=150
        )
    except Exception:
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
    candidates = retrieve(question, ticker=ticker)
    relevant = _grade_relevance(router, question, candidates)
    if len(relevant) < MIN_RELEVANT_CHUNKS:
        # Corrective RAG: one rewrite with feedback, then retry through the same quality gate.
        retry_query = _rewrite_query(
            router,
            question,
            hint=(
                "the query returned too few current, field-correct passages; "
                "include latest fiscal year/current-period filing vocabulary and exact metric type"
            ),
        )
        retry_candidates = retrieve(retry_query, ticker=ticker)
        relevant.extend(_grade_relevance(router, retry_query, retry_candidates))
    deduped: dict[str, RetrievedEvidence] = {}
    for item in relevant:
        deduped.setdefault(item.chunk_id, item)
    return list(deduped.values())


def filings_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: research the filings index for the user's focus."""
    router = LLMRouter(state.run_id)
    log.info("filings_questions_start", ticker=state.ticker)
    try:
        questions = _generate_questions(router, state)
    except BudgetExceededError as exc:
        log.warning("filings_question_generation_budget_exhausted", error=str(exc))
        return {
            "research_questions": [],
            "data_gaps": ["Filings research unavailable because the run token budget was exhausted."],
        }
    except Exception as exc:
        log.warning("filings_question_generation_failed_using_fallback", error=str(exc)[:300])
        questions = _fallback_questions(state)

    log.info("filings_questions_end", ticker=state.ticker, questions=len(questions))

    evidence: dict[str, RetrievedEvidence] = {}
    for index, question in enumerate(questions, 1):
        try:
            log.info("filings_question_start", index=index, total=len(questions), question=question[:160])
            before = len(evidence)
            for item in _research_question(router, question, state.ticker):
                evidence.setdefault(item.chunk_id, item)
            log.info(
                "filings_question_end",
                index=index,
                total=len(questions),
                new_chunks=len(evidence) - before,
                total_chunks=len(evidence),
            )
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
        updates["data_gaps"] = [
            f"No relevant filing evidence retrieved from the indexed SEC filings for "
            f"{state.company_name} ({state.ticker}). "
            "This may mean: (a) the company is being indexed for the first time and had no "
            "indexable filings (20-F/10-K/6-K/10-Q), or (b) the filing content did not match "
            "the research questions. "
            "Try re-running; if the problem persists this company may have limited EDGAR filings."
        ]
    return updates
