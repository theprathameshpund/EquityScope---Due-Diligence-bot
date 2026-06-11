# EquityScope 🔬

**A production-grade multi-agent financial due diligence copilot.**

Give it a public company name or ticker (plus an optional focus like
*"supply-chain risk"*) and it produces a fully cited, claim-verified due
diligence report from SEC filings, EDGAR XBRL financials, market data, and
recent news — streamed live to a web UI.

## Architecture

```
                        ┌────────────────────────────────────────────┐
                        │            LangGraph Supervisor            │
                        │   (Postgres checkpoints — resumable runs)  │
                        └────────────────────────────────────────────┘
 user request                                 │
 POST /api/reports ──► ingest_check ──┬─► filings agent ──┐   (agentic RAG:
      │                (EDGAR → parse │   market agent ───┤    rewrite → retrieve
      │                 → chunk →     │   news agent ─────┤    → rerank → grade →
      │                 embed→Qdrant) │   (parallel)      │    corrective retry)
      ▼                               │                   ▼
 SSE /events ◄── Redis pub/sub ◄──────┴───────────► analyst agent (XBRL → pure-
      │                                             Python metrics, NO LLM math)
      ▼                                                   │
 Streamlit UI                                             ▼
 (live timeline,                                    writer agent (structured
  citations,                                        DDReport, every claim cites
  cost meter)                                       chunk_ids / metric_ids)
                                                          │
                                                          ▼
                                  critic agent (NLI entailment + numeric regex
                                  verification; revise loop ≤2, then drop+log)
                                                          │
                                                          ▼
                                                  finalize → report JSON + MD
```

Key guarantees:

- **The LLM never does arithmetic.** All numbers come from EDGAR XBRL
  `companyfacts` via deterministic functions in `src/tools/metrics.py`; each
  `MetricValue` carries the raw inputs used, so every figure is re-verifiable.
- **Every qualitative claim carries citations** (chunk IDs of retrieved filing
  text). The critic verifies entailment with a local NLI cross-encoder;
  unverifiable claims are revised (max `CRITIC_MAX_REVISIONS`) or dropped and
  recorded in `data_gaps` — never silently shipped.
- **Graceful degradation**: market/news failures produce an "unavailable"
  section, never a failed run.
- **Resumable**: LangGraph checkpoints in Postgres; rerun with
  `--resume <run_id>` after a crash.
- **SEC-polite**: contact User-Agent, ≤10 req/s rate limiter, disk cache in
  `data/filings/`.
- **Cost-tracked**: per-call token counts priced from the `.env` cost table;
  totals in report metadata and the UI.

## Setup

Requires Python 3.11+ (3.12 tested). Infra: Qdrant, PostgreSQL, Redis — via
WSL (no Docker needed) or Docker Compose, your choice.

```powershell
# 1. Virtualenv + dependencies
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2. Configuration
copy .env.example .env
#    → fill in GROQ_API_KEY and EDGAR_USER_AGENT (see table below)

# 3. Infrastructure — WSL path (used on this machine, no Docker):
wsl -d Ubuntu -u root -- bash "/mnt/c/<path-to-repo>/scripts/infra_wsl.sh"
#    Starts: Redis :6379 · PostgreSQL :5433 · Qdrant :6333
#    Note: WSL Postgres runs on 5433 because a native Windows postgres may own
#    5432 — keep POSTGRES_DSN pointing at 5433 in that case.
#    If your WSL Redis has `requirepass` set, use redis://:<password>@localhost:6379/0

#    …or the Docker path:
docker compose up -d qdrant postgres redis
```

### Corporate proxy / TLS interception

If your network re-signs TLS (self-signed cert errors from Groq, Hugging Face,
or yfinance):

- `src/config.py` already calls `truststore.inject_into_ssl()` so Python uses
  the OS certificate store.
- yfinance (curl_cffi) needs a PEM bundle. Generate it once:

```powershell
@'
import ssl, certifi
from pathlib import Path
parts = [Path(certifi.where()).read_text(encoding="utf-8")]
ctx = ssl.create_default_context(); ctx.load_default_certs()
parts += [ssl.DER_cert_to_PEM_cert(d) for d in ctx.get_ca_certs(binary_form=True)]
Path("data/ca_bundle.pem").write_text("\n".join(parts), encoding="utf-8")
'@ | .\.venv\Scripts\python.exe -
```

`config.py` auto-sets `CURL_CA_BUNDLE` to `data/ca_bundle.pem` when present.

### Run the full stack

```powershell
# API (FastAPI)
.\.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000

# Frontend (Streamlit) — second terminal
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.port 8501
```

Open http://localhost:8501, enter a ticker, watch the agent timeline, expand
citations in the finished report. Health check: http://localhost:8000/healthz

### CLI usage (no UI)

```powershell
# Index a company's filings (idempotent; --force re-ingests)
.\.venv\Scripts\python.exe -m src.rag.ingestion AAPL

# Full due diligence run → data/reports/<run_id>.{json,md}
.\.venv\Scripts\python.exe -m src.agents.orchestrator AAPL "competition risk"

# Resume a crashed run from its last completed node
.\.venv\Scripts\python.exe -m src.agents.orchestrator --resume <run_id>
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | **yes** | — | Sub-agent LLM calls (console.groq.com/keys) |
| `EDGAR_USER_AGENT` | **yes** | — | SEC-mandated contact string, e.g. `"Jane Doe jane@x.com"` |
| `ANTHROPIC_API_KEY` | if writer=anthropic | empty | Writer/critic on Claude |
| `LLM_WRITER_PROVIDER` | no | `groq` | `groq` \| `anthropic` routing for writer & critic |
| `GROQ_MODEL_FAST` / `GROQ_MODEL_SMART` | no | llama-3.1-8b / llama-3.3-70b | Model tiers |
| `ANTHROPIC_MODEL` | no | claude-sonnet-4-20250514 | Writer model when anthropic |
| `LLM_MAX_RETRIES` / `LLM_REQUEST_TIMEOUT_S` | no | 3 / 60 | Resilience knobs |
| `GROQ_MAX_RPM` / `GROQ_TPM_FAST` / `GROQ_TPM_SMART` | no | 28 / 6000 / 12000 | Proactive rate pacing (matches Groq free tier; 0 disables) |
| `EMBEDDING_MODEL` / `EMBEDDING_DEVICE` | no | bge-large-en-v1.5 / cpu | Local embeddings (no API) |
| `QDRANT_URL` / `QDRANT_COLLECTION` | no | localhost:6333 / equityscope_filings | Vector store |
| `POSTGRES_DSN` | no | localhost:5432 (5433 under WSL) | LangGraph checkpoints |
| `REDIS_URL` | no | localhost:6379/0 | Progress events + run status |
| `FRED_API_KEY` | no | empty | Macro context (skipped if empty) |
| `NEWS_RSS_ENABLED` | no | true | Google News RSS |
| `CHUNK_MAX_TOKENS` / `CHUNK_OVERLAP_TOKENS` | no | 800 / 100 | Chunker |
| `RETRIEVAL_TOP_K` / `RERANK_TOP_N` | no | 12 / 5 | Retrieval funnel |
| `RERANKER_MODEL` / `CRITIC_NLI_MODEL` | no | ms-marco-MiniLM / nli-deberta-v3-base | Local cross-encoders |
| `CRITIC_MAX_REVISIONS` / `CRITIC_ENTAILMENT_THRESHOLD` | no | 2 / 0.7 | Verification loop |
| `RUN_TOKEN_BUDGET` | no | 150000 | Hard token cap per run |
| `FILINGS_LOOKBACK_8K_MONTHS` / `FILINGS_NUM_10Q` | no | 12 / 4 | Filing selection |
| `API_HOST` / `API_PORT` / `FRONTEND_PORT` | no | 0.0.0.0 / 8000 / 8501 | Bind addresses |
| `LOG_LEVEL` / `ENVIRONMENT` | no | INFO / development | `production` enables JSON logs + rate limiting |
| `LANGFUSE_*` | no | empty | Optional observability |
| `COST_*` | no | see `.env.example` | USD per 1M tokens for cost accounting |

## Quality gates

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests evals   # lint
.\.venv\Scripts\python.exe -m mypy                          # strict typing on src/
.\.venv\Scripts\python.exe -m pytest tests -q               # unit + integration
```

CI (`.github/workflows/ci.yml`) runs all of the above on every PR with mocked
LLMs and recorded EDGAR fixtures — no secrets needed.

## Evals

All three run against a saved report JSON (`data/reports/<run_id>.json`):

```powershell
# Numbers in the report vs freshly recomputed XBRL values (exits ≠0 below 100%)
.\.venv\Scripts\python.exe evals/run_numeric_check.py

# Claim-level NLI groundedness, per-section breakdown → evals/results/
.\.venv\Scripts\python.exe evals/run_faithfulness.py

# Golden Q&A retrieval: hit-rate@5 + MRR, dense vs dense+rerank
.\.venv\Scripts\python.exe evals/run_retrieval.py
```

The golden set `evals/golden/aapl.yaml` ships with 22 questions and **empty
`expected_phrases` that you must fill by hand** from the actual filings (the
file header explains how) — the eval refuses to run on unanswered questions
rather than inventing ground truth.

### Eval results

From verification run `run_12ba9ded23c5` (AAPL, focus "competition risk",
2026-06-11, 31,321 tokens, $0.0123, 315s):

| Eval | Result |
|---|---|
| Numeric exact-match (report vs recomputed XBRL) | **22/22 = 100%** |
| Grounded-claim % (NLI vs cited chunks) | **100%** (mean entailment 0.996–0.998) |
| Retrieval hit-rate@5 / MRR (dense vs rerank) | _pending — fill `evals/golden/aapl.yaml` answers first_ |

3 claims the critic could not verify after 2 revision loops were dropped and
disclosed in the report's `data_gaps` — exactly the intended behavior.

## Screenshots

_Placeholder: add `docs/screenshot_timeline.png` (live agent timeline) and
`docs/screenshot_report.png` (report with expanded citation)._

## Repository layout

```
src/
├── config.py          # pydantic-settings — the only source of configuration
├── state.py           # all shared Pydantic models + LangGraph AgentState
├── agents/            # orchestrator (graph) + 6 specialized agents
├── tools/             # edgar, xbrl, metrics (pure), market_data, news
├── rag/               # ingestion, parsing, chunking, embeddings, retriever
├── llm/               # provider router, cost tracking + prompts/*.md
├── guardrails/        # NLI grounding + prompt-injection filter
├── memory/            # Postgres checkpointer
├── report/            # DDReport schema + Markdown renderer
└── api/               # FastAPI app, routes, SSE
frontend/app.py        # Streamlit UI
evals/                 # numeric / faithfulness / retrieval evals + golden set
tests/                 # unit + integration (mocked LLM, recorded fixtures)
scripts/infra_wsl.sh   # no-Docker infra: Redis + Postgres + Qdrant in WSL
```
