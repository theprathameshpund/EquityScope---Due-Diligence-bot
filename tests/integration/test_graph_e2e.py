"""End-to-end graph run with mocked LLM, retriever, and data sources.

Verifies the full supervisor topology: ingest → parallel research → analyst
→ writer → critic → finalize, including citation enforcement and graceful
degradation when market/news sources fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.llm.router import LLMResponse, LLMRouter, drop_tracker
from src.report.schema import DDReport
from src.state import AgentState, MarketSnapshot, NewsItem, RetrievedEvidence, RunBudget
from src.tools.edgar import CompanyIdentity
from src.tools.xbrl import parse_companyfacts

FIXTURE = Path(__file__).parent / "fixtures" / "companyfacts_sample.json"

EVIDENCE = [
    RetrievedEvidence(
        chunk_id="c1",
        text="The Company faces intense competition in all markets in which it operates.",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/test.htm",
        form_type="10-K",
        fiscal_period="2024-09-28",
        section="Risk Factors",
        score=0.9,
    ),
    RetrievedEvidence(
        chunk_id="c2",
        text="Substantially all of the Company's manufacturing is performed by outsourcing partners located primarily in Asia.",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/test.htm",
        form_type="10-K",
        fiscal_period="2024-09-28",
        section="Risk Factors",
        score=0.8,
    ),
]


def _fake_complete_json(
    self: LLMRouter, tier: str, system: str, user: str, schema: type[Any], **kwargs: Any
) -> Any:
    name = schema.__name__
    if name == "_Questions":
        return schema(questions=["What competition does the company face?",
                                 "What supply chain risks exist?",
                                 "How is liquidity managed?",
                                 "What are recent material events?"])
    if name == "_Rewrite":
        return schema(query="competition risk factors")
    if name == "_Grades":
        return schema(grades=["yes", "yes"])
    if name == "_Peers":
        return schema(tickers=["MSFT", "GOOGL", "DELL"])
    if name == "_Sentiments":
        return schema(labels=["neutral"])
    if name == "_Commentary":
        return schema(claims=[{"text": "Revenue CAGR was -0.42% over the period.",
                               "metric_ids": ["revenue_cagr_3y"]}])
    if name == "_WriterOutput":
        return schema(
            executive_summary=[
                {"text": "The company faces intense competition in all its markets.",
                 "citation_chunk_ids": ["c1"], "metric_ids": []},
            ],
            business_overview=[
                {"text": "Manufacturing is outsourced to partners primarily in Asia.",
                 "citation_chunk_ids": ["c2"], "metric_ids": []},
            ],
            financial_commentary=[
                {"text": "Gross margin reached 46.21% in FY2024.",
                 "citation_chunk_ids": [], "metric_ids": ["gross_margin_fy2024"]},
            ],
            risk_matrix=[
                {"title": "Competition", "severity": "high", "likelihood": "high",
                 "claims": [{"text": "Intense competition pressures pricing across markets.",
                             "citation_chunk_ids": ["c1"], "metric_ids": []}]},
            ],
            recent_developments=[],
            red_flags=[
                {"text": "This claim has no citation and must be dropped.",
                 "citation_chunk_ids": [], "metric_ids": []},
            ],
        )
    if name == "_Revision":
        return schema(action="drop")
    raise AssertionError(f"Unexpected schema requested: {name}")


def _fake_complete(
    self: LLMRouter, tier: str, system: str, user: str, **kwargs: Any
) -> LLMResponse:
    self.tracker.add(100, 20, 0.0001)
    return LLMResponse(text="The stock traded steadily over the year.",
                       input_tokens=100, output_tokens=20, cost_usd=0.0001,
                       provider="mock", model="mock")


@pytest.fixture
def patched_graph(monkeypatch: pytest.MonkeyPatch) -> Any:
    import src.agents.analyst_agent as analyst_mod
    import src.agents.critic_agent as critic_mod
    import src.agents.filings_agent as filings_mod
    import src.agents.market_agent as market_mod
    import src.agents.news_agent as news_mod
    import src.agents.orchestrator as orch_mod

    facts = parse_companyfacts(json.loads(FIXTURE.read_text(encoding="utf-8")))

    monkeypatch.setattr(
        orch_mod, "ingest_company",
        lambda query, force=False: (CompanyIdentity(cik="320193", ticker="AAPL",
                                                    name="Apple Inc."), 42),
    )
    monkeypatch.setattr(filings_mod, "retrieve",
                        lambda query, *, ticker, form_type=None, top_n=None: EVIDENCE)
    monkeypatch.setattr(analyst_mod, "fetch_financial_facts", lambda cik: facts)
    monkeypatch.setattr(market_mod, "get_snapshot",
                        lambda ticker: MarketSnapshot(ticker=ticker, price=200.0))
    monkeypatch.setattr(market_mod, "get_peer_multiples", lambda tickers: [])
    monkeypatch.setattr(market_mod, "get_macro_notes", lambda: [])
    monkeypatch.setattr(
        news_mod, "fetch_news",
        lambda company, ticker: [NewsItem(title="Company ships new product",
                                          url="https://example.com")],
    )
    monkeypatch.setattr(critic_mod, "best_entailment", lambda claim, premises: 0.95)
    monkeypatch.setattr(LLMRouter, "complete_json", _fake_complete_json)
    monkeypatch.setattr(LLMRouter, "complete", _fake_complete)
    return orch_mod


def _initial_state(run_id: str) -> AgentState:
    return AgentState(
        run_id=run_id,
        company_input="AAPL",
        focus="competition risk",
        budget=RunBudget(token_limit=150_000),
    )


def test_graph_end_to_end(patched_graph: Any) -> None:
    graph = patched_graph.build_graph(None)
    final = graph.invoke(_initial_state("run_test_e2e"))
    drop_tracker("run_test_e2e")

    report = final["report"]
    assert isinstance(report, DDReport)
    assert report.company.ticker == "AAPL"
    assert final["status"] == "done"

    claims = report.all_claims()
    assert claims, "report must contain claims"
    # Every surviving claim is verified and carries provenance.
    for claim in claims:
        assert claim.verification_status == "supported"
        assert claim.citation_chunk_ids or claim.metric_ids
    # The uncited red-flag claim was dropped before shipping.
    assert all("no citation" not in c.text for c in claims)
    assert not report.red_flags
    # Metrics table is populated from deterministic XBRL computation.
    metric_ids = {m.metric_id for m in report.financial_health.table.metrics}
    assert "gross_margin_fy2024" in metric_ids
    assert report.metadata.run_id == "run_test_e2e"


def test_graph_degrades_when_sources_fail(
    patched_graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.agents.market_agent as market_mod
    import src.agents.news_agent as news_mod

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("source down")

    monkeypatch.setattr(market_mod, "get_snapshot", _boom)
    monkeypatch.setattr(news_mod, "fetch_news", _boom)

    graph = patched_graph.build_graph(None)
    final = graph.invoke(_initial_state("run_test_degraded"))
    drop_tracker("run_test_degraded")

    report = final["report"]
    assert isinstance(report, DDReport)
    assert final["status"] == "done"
    market = final["market"]
    news = final["news"]
    assert market is not None and not market.available
    assert news is not None and not news.available
    gaps = " ".join(report.data_gaps)
    assert "Market data unavailable" in gaps
    assert "News unavailable" in gaps
