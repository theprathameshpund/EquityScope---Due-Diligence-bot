# EquityScope — Multi-Agent Financial Research & Due Diligence Copilot

**One-line description:** A production-grade multi-agent system that autonomously researches a public company — pulling SEC filings, earnings calls, news, and market data — and produces a cited, analyst-quality due diligence report with self-verification and hallucination guards.

---

## 1. Business Problem (Real-World Relevance)

Equity analysts, VCs, corporate development teams, and credit/risk officers spend **6–20 hours per company** manually reading 10-Ks, 10-Qs, 8-Ks, earnings transcripts, and news to answer questions like *"What are this company's key risks, revenue drivers, and red flags?"*

Pain points this solves:
- **Manual synthesis across heterogeneous sources** (structured XBRL financials + unstructured filings + real-time news).
- **Trust:** finance is zero-tolerance for hallucination — every claim must be cited and verifiable.
- **Staleness:** static RAG over a fixed corpus fails; analysts need fresh data pulled on demand.

This is exactly the class of problem (multi-source agentic research with verification) that enterprises like JPMorgan, Bloomberg, Moody's, and McKinsey are building internally — which is why it interviews so well.

---

## 2. Multi-Agent Architecture (Text Diagram)

```
                            ┌──────────────────────────┐
                            │        USER (UI/API)      │
                            │  "Run due diligence on    │
                            │   NVIDIA, focus on risk"  │
                            └────────────┬──────────────┘
                                         │
                            ┌────────────▼──────────────┐
                            │   ORCHESTRATOR AGENT       │
                            │   (LangGraph Supervisor)   │
                            │ • Decomposes query → plan  │
                            │ • Routes tasks to agents   │
                            │ • Tracks state & budget    │
                            │ • Handles retries/failures │
                            └──┬─────┬─────┬─────┬───────┘
                               │     │     │     │
        ┌──────────────────────┘     │     │     └──────────────────────┐
        ▼                            ▼     ▼                            ▼
┌───────────────┐   ┌────────────────┐  ┌────────────────┐   ┌────────────────┐
│ FILINGS AGENT │   │ MARKET DATA    │  │ NEWS & SENTIMENT│  │ FINANCIAL      │
│ (Agentic RAG) │   │ AGENT          │  │ AGENT           │  │ ANALYST AGENT  │
│               │   │                │  │                 │  │                │
│ Tools:        │   │ Tools:         │  │ Tools:          │  │ Tools:         │
│ • EDGAR fetch │   │ • yfinance API │  │ • NewsAPI/RSS   │  │ • Python REPL  │
│ • Vector      │   │ • FRED macro   │  │ • Web search    │  │   (sandboxed)  │
│   search      │   │   data API     │  │ • Sentiment     │  │ • XBRL parser  │
│ • Re-ranker   │   │                │  │   classifier    │  │ • Ratio calc   │
│ • Self-query  │   │                │  │                 │  │   functions    │
│   rewriter    │   │                │  │                 │  │                │
└───────┬───────┘   └────────┬───────┘  └────────┬────────┘  └────────┬───────┘
        │                    │                   │                    │
        └────────────────────┴─────────┬─────────┴────────────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │  CRITIC / VERIFIER AGENT     │
                        │ • Claim → citation check     │
                        │   (NLI-based groundedness)   │
                        │ • Numeric consistency check  │
                        │ • Sends rejected sections    │
                        │   back to source agent       │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │  REPORT WRITER AGENT         │
                        │ • Structured output (Pydantic)│
                        │ • Inline citations [doc§page]│
                        │ • Exec summary + risk matrix │
                        └──────────────┬───────────────┘
                                       ▼
                          Final Report (MD/PDF + JSON)
```

### Agent Roster

| Agent | Role | Tools | Communication |
|---|---|---|---|
| **Orchestrator (Supervisor)** | Query decomposition, task routing, state management, token/cost budget enforcement, retry logic | LangGraph `StateGraph`, task queue | Routes via shared typed state object (`AgentState`); receives structured results from sub-agents |
| **Filings Agent** | Agentic RAG over SEC filings (10-K/10-Q/8-K, proxy statements) | EDGAR full-text fetch, hybrid vector search (dense + BM25), cross-encoder re-ranker, query rewriting, metadata filtering (filing type, fiscal year, section) | Returns `RetrievedEvidence[]` with chunk IDs, source URLs, page anchors |
| **Market Data Agent** | Quantitative context: price history, valuation multiples, peer comparison | yfinance, FRED API (macro rates), function calling for structured queries | Returns typed `MarketSnapshot` Pydantic model |
| **News & Sentiment Agent** | Recent events, controversies, analyst sentiment | NewsAPI / Google News RSS, web search tool, fine-tuned/few-shot sentiment classifier | Returns `NewsDigest` with per-article sentiment + recency weights |
| **Financial Analyst Agent** | Computes ratios (margins, leverage, FCF, growth), trend analysis, anomaly flags | Sandboxed Python REPL, XBRL parser, deterministic calculation functions (never lets the LLM do arithmetic) | Returns `FinancialAnalysis` with computed metrics + code provenance |
| **Critic / Verifier Agent** | Groundedness gate: every claim must map to a citation; numeric claims re-verified against source | NLI entailment model (e.g., DeBERTa-NLI) or LLM-as-judge with rubric, numeric extraction + comparison | Emits `pass/revise` verdicts; on `revise`, orchestrator re-dispatches with critic feedback (max 2 loops) |
| **Report Writer Agent** | Synthesis into exec-quality report with risk matrix and citations | Structured output (Pydantic schema), Jinja2 → MD/PDF | Final node; writes report + machine-readable JSON |

**Key architectural decisions to be ready to defend:**
- **Supervisor pattern over peer-to-peer:** deterministic routing, easier debugging/tracing, bounded cost.
- **Critic loop with a cap (max 2 revisions):** prevents infinite self-correction loops; this is the #1 production failure mode of naive agent systems.
- **LLM never does math:** all arithmetic via tools — interviewers love this.
- **Typed state (Pydantic) between agents, not free-form text:** turns "agents chatting" into a real distributed system with contracts.

---

## 3. Tech Stack

| Layer | Choice | Why (interview-ready justification) |
|---|---|---|
| **Orchestration** | **LangGraph** | Graph-based state machine → deterministic, resumable, checkpointable; industry has converged here over CrewA/AutoGen for production |
| **LLMs** | Claude (Sonnet) for reasoning/writing, GPT-4o-mini or Gemini Flash for cheap sub-tasks (classification, rewriting) | Demonstrates **model routing by task complexity** — a real cost-engineering skill |
| **Embeddings** | OpenAI `text-embedding-3-large` or open-source `bge-large` | Show you benchmarked both (include the comparison in repo) |
| **Vector DB** | **Qdrant** (self-hosted in Docker) | Hybrid search (dense+sparse), payload filtering, production-grade, free |
| **Re-ranker** | Cohere Rerank or `cross-encoder/ms-marco-MiniLM` | Measurable retrieval lift — quantify it in your evals |
| **Backend** | **FastAPI** + async workers, SSE streaming for agent progress | Standard enterprise stack |
| **Memory** | Redis (short-term session state) + Postgres (long-term: past reports, user prefs, agent checkpoints via LangGraph persistence) | Two-tier memory is a strong talking point |
| **Frontend** | Streamlit (fast) or Next.js (stronger signal) with live agent-activity timeline | Show *which agent is doing what* in real time — huge demo wow-factor |
| **Observability** | **LangSmith** or **Langfuse** (open-source) — traces, token costs, latency per agent | Non-negotiable for "production-grade" claims |
| **Evals** | RAGAS + custom LLM-as-judge harness + pytest regression suite | See §6 |
| **Infra** | Docker Compose locally → deploy on AWS ECS / GCP Cloud Run / Railway; GitHub Actions CI | |

---

## 4. Key Features (Gen AI Highlights)

1. **Agentic RAG with self-correcting retrieval** — query rewriting, hybrid search, re-ranking, and a relevance-grading step that triggers re-retrieval when grading fails (CRAG-style). Quantify the lift: e.g., "re-ranking improved context precision from 0.61 → 0.84."
2. **Critic-verifier loop for hallucination control** — NLI-based groundedness check on every generated claim; ungrounded sections bounce back with feedback. Report a measured hallucination rate before/after.
3. **Cost- and latency-aware model routing** — cheap models for classification/rewriting, frontier models for synthesis; per-request cost tracked and surfaced in the UI ("This report cost $0.11").
4. **Deterministic tool layer for numbers** — XBRL parsing + sandboxed Python for all calculations, with code provenance attached to every metric in the report.
5. **Streaming agent telemetry UI** — live timeline of agent handoffs, tool calls, and token spend (SSE), so the demo itself teaches your architecture.
6. **Checkpointed, resumable runs** — LangGraph persistence in Postgres; a crashed run resumes from the last node. Genuine production engineering.
7. **Structured, citable output** — every claim carries `[10-K 2025, Item 1A, p.23]`-style anchors that link back to the exact chunk; report exported as PDF + JSON.
8. **Guardrails** — prompt-injection screening on fetched web/news content (treat retrieved text as data, not instructions), PII scrubbing, and a refusal path for non-public-company queries.

---

## 5. Data Sources (All Free)

| Source | Use |
|---|---|
| **SEC EDGAR** (full-text search API + XBRL Frames API) | 10-K, 10-Q, 8-K, DEF 14A — the RAG corpus |
| **yfinance** | Prices, multiples, peer tickers |
| **FRED API** | Macro context (rates, CPI) |
| **NewsAPI / Google News RSS** | Recent events & sentiment |
| **Earnings call transcripts** | Motley Fool / Seeking Alpha public transcripts, or EDGAR 8-K exhibits |
| **Eval gold set** | Hand-build 50–80 Q&A pairs per 3–5 companies from filings (you become the labeler — this is normal and expected) |

Ingestion pipeline: scheduled fetch → section-aware chunking (split 10-Ks by Item, not blind 512-token windows — mention this, it matters) → embed → upsert with rich metadata (`ticker, form_type, fiscal_year, item_section, page`).

---

## 6. Evaluation Strategy

Treat evals as a first-class component — this is the single biggest differentiator vs. typical portfolio projects.

**A. Retrieval evals (RAGAS + custom):**
- Context precision / recall on your gold Q&A set
- Hit-rate@k and MRR, with and without re-ranker (ablation table in README)

**B. Generation evals:**
- **Faithfulness/groundedness:** RAGAS faithfulness + your NLI verifier as an automated judge; report hallucination rate
- **Answer correctness vs. gold answers:** LLM-as-judge with a written rubric (include the rubric in the repo)
- **Citation accuracy:** % of citations whose chunk actually entails the claim (sample-audited manually)

**C. Agent/system-level evals:**
- Task success rate on 20 end-to-end scenarios ("full DD report on AAPL focusing on supply-chain risk")
- Cost per report, P50/P95 latency, tool-call error rate, critic-loop trigger rate
- **Regression suite in CI:** every PR runs a small eval set; merge blocked if faithfulness drops > threshold

**D. Tracing:** every run fully traced in Langfuse/LangSmith; include screenshots in the README.

---

## 7. Deployment Plan

1. **Local:** `docker-compose up` → services: `api` (FastAPI), `worker`, `qdrant`, `redis`, `postgres`, `frontend`, `langfuse`.
2. **CI/CD:** GitHub Actions — lint (ruff), type-check (mypy), unit tests, eval regression suite, build & push images.
3. **Cloud:** AWS ECS Fargate or GCP Cloud Run (API + worker), managed Postgres (RDS/Cloud SQL), Qdrant Cloud free tier; secrets via SSM/Secret Manager.
4. **Production touches:** rate limiting, per-user API keys, request-level cost caps, health checks, structured JSON logging, graceful degradation (if news API is down, report ships with a "news unavailable" section rather than failing).
5. **Demo:** hosted URL + 2-minute Loom video in the README. Recruiters click links; they don't clone repos.

---

## 8. Resume Bullet Points

Use numbers from *your* eval runs — these are templates:

- *Architected and deployed a 6-agent LangGraph system (supervisor + specialized agents) automating financial due diligence, reducing research time from ~8 hours to under 5 minutes per company with fully cited outputs.*
- *Built agentic RAG pipeline over 10K+ SEC filing sections (Qdrant hybrid search + cross-encoder re-ranking + corrective re-retrieval), improving context precision from 0.61 to 0.84 on a hand-labeled eval set.*
- *Designed an NLI-based critic-verifier loop that cut hallucinated claims by 72% (measured via RAGAS faithfulness + manual audit), enforcing claim-level citation guarantees.*
- *Implemented cost-aware model routing across Claude/GPT/Gemini and deterministic tool execution for all numeric computation, reducing per-report LLM cost by ~60% at equal quality.*
- *Shipped production infrastructure: checkpointed/resumable agent runs (LangGraph + Postgres), Langfuse tracing, CI-gated eval regression suite, Dockerized deploy on Cloud Run with SSE streaming UI.*

---

## 9. Interview Talking Points This Project Enables

1. **"Why supervisor pattern instead of autonomous peer agents?"** → determinism, debuggability, bounded cost; you tried free-form handoffs first and they looped.
2. **"How do you stop hallucination?"** → layered: grounded retrieval → structured outputs → critic NLI gate → numbers only via tools → measured, not vibes.
3. **Agent failure modes you actually hit:** infinite critic loops (fixed with loop cap), context-window blowout from accumulating state (fixed with state summarization), tool errors cascading (fixed with retries + circuit breaker).
4. **Chunking strategy:** why section-aware chunking of 10-Ks beats fixed-size windows; show the eval delta.
5. **Cost engineering:** model routing, prompt caching, token budgets per node — talk in $/report.
6. **Eval philosophy:** offline gold set + online tracing + CI regression gates; how you'd extend to A/B testing in prod.
7. **Security:** prompt injection via retrieved web content and how you sandbox it; sandboxed code execution for the analyst agent.
8. **Trade-off discussion:** LangGraph vs CrewAI vs AutoGen — you can compare from experience, not blog posts.
9. **Scaling story:** how you'd take it to 1,000 concurrent users (queue workers, embedding cache, pre-computed company indexes).
10. **Memory design:** session memory vs. durable checkpoints vs. user-preference memory, and why they're separate stores.

---

## 10. Build Timeline (~6–8 Weeks Part-Time)

| Week | Component | Deliverable |
|---|---|---|
| 1 | Data ingestion + RAG core | EDGAR fetcher, section-aware chunker, Qdrant index, basic retrieval API |
| 2 | Filings Agent + retrieval evals | Hybrid search + re-ranker; gold Q&A set v1; RAGAS baseline numbers |
| 3 | Orchestrator + Market/News agents | LangGraph supervisor graph, typed state, tool calling, checkpointing |
| 4 | Analyst Agent + Critic loop | Sandboxed Python tool, XBRL metrics, NLI verifier, revision loop |
| 5 | Report Writer + API + streaming | Structured output schema, FastAPI SSE, PDF export |
| 6 | Frontend + observability | Agent timeline UI, Langfuse tracing, cost dashboard |
| 7 | Eval harness + CI/CD + Docker | Full eval suite, regression gates, docker-compose, GitHub Actions |
| 8 | Deploy + polish | Cloud deploy, README with architecture diagram + eval tables, demo video |

Cut-scope path if time-boxed to 4 weeks: drop News Agent and Next.js (use Streamlit), keep the critic loop and evals — those are the differentiators.

---

## 11. GitHub Repo Structure

```
equityscope/
├── README.md                  # Architecture diagram, eval results table, demo GIF, hosted link
├── docker-compose.yml
├── .github/workflows/
│   ├── ci.yml                 # lint, type-check, tests
│   └── eval-regression.yml    # blocks merge on faithfulness drop
├── src/
│   ├── agents/
│   │   ├── orchestrator.py    # LangGraph supervisor graph definition
│   │   ├── filings_agent.py
│   │   ├── market_agent.py
│   │   ├── news_agent.py
│   │   ├── analyst_agent.py
│   │   ├── critic_agent.py
│   │   └── writer_agent.py
│   ├── state.py               # Typed AgentState (Pydantic) — the contract
│   ├── tools/
│   │   ├── edgar.py
│   │   ├── market_data.py
│   │   ├── news.py
│   │   ├── python_sandbox.py
│   │   └── xbrl_parser.py
│   ├── rag/
│   │   ├── ingestion.py       # fetch → chunk → embed → upsert
│   │   ├── chunking.py        # section-aware 10-K splitter
│   │   ├── retriever.py       # hybrid + rerank + corrective loop
│   │   └── embeddings.py
│   ├── llm/
│   │   ├── router.py          # task→model routing logic
│   │   └── prompts/           # versioned prompt files, not inline strings
│   ├── memory/
│   │   ├── session.py         # Redis
│   │   └── checkpoints.py     # LangGraph Postgres persistence
│   ├── guardrails/
│   │   ├── injection_filter.py
│   │   └── grounding.py       # NLI claim verification
│   ├── api/
│   │   ├── main.py            # FastAPI app, SSE endpoints
│   │   └── schemas.py
│   └── observability/
│       └── tracing.py         # Langfuse setup
├── evals/
│   ├── golden/                # hand-labeled Q&A sets per company
│   ├── run_ragas.py
│   ├── llm_judge.py           # rubric-based judge + the rubric itself
│   ├── e2e_scenarios.yaml
│   └── results/               # committed eval result tables (your proof)
├── frontend/                  # Streamlit or Next.js
├── tests/                     # unit + integration
├── infra/                     # Terraform / Cloud Run configs (optional, strong signal)
└── docs/
    ├── architecture.md        # decisions + trade-offs (ADR style)
    └── failure_modes.md       # what broke and how you fixed it ← interview gold
```

**Two repo details that disproportionately impress reviewers:** committed eval results with ablation tables in the README, and a `docs/failure_modes.md` documenting real problems you hit. Both signal "this person has operated agents, not just wired up a tutorial."
