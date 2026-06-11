# EquityScope Agents — one file per agent

Each agent is a single, independently configurable module exposing one
LangGraph node function. They communicate **only** through the typed
`AgentState` (src/state.py) — never free-form text.

| File | Node | What it does | LLM tier | Tuning knobs |
|---|---|---|---|---|
| `orchestrator.py` | all wiring | Graph topology, supervisor routing, budget enforcement, Redis progress events, finalize | — | `RUN_TOKEN_BUDGET`, `CRITIC_MAX_REVISIONS` (.env); `EVENTS_*`/`RUN_KEY` constants |
| `filings_agent.py` | `filings` | Agentic RAG over the indexed filings: question generation → query rewrite → retrieve → rerank → relevance grade → one corrective retry | fast | `RETRIEVAL_TOP_K`, `RERANK_TOP_N` (.env); `MIN_RELEVANT_CHUNKS` (module) |
| `market_agent.py` | `market` | yfinance snapshot + LLM-suggested peer multiples + optional FRED macro; degrades to `available=false` | fast (summary only) | `FRED_API_KEY` (.env) |
| `news_agent.py` | `news` | Google News RSS → prompt-injection filter → batched sentiment | fast | `NEWS_RSS_ENABLED` (.env); lookback/max in `src/tools/news.py` |
| `analyst_agent.py` | `analyst` | XBRL → deterministic metrics (`src/tools/metrics.py`) + one commentary claim per metric group. **The LLM never computes numbers.** | smart | anomaly thresholds in `src/tools/metrics.py` |
| `writer_agent.py` | `writer` | Structured `DDReport` via JSON-schema output; per-claim revision passes; metrics-only partial report if the LLM is unavailable | writer (`LLM_WRITER_PROVIDER`) | `_MAX_EVIDENCE_CHUNKS`, `_MAX_EVIDENCE_CHARS`, `_WRITER_MAX_TOKENS` (module) |
| `critic_agent.py` | `critic` | No LLM: NLI entailment per cited chunk (windowed) + regex numeric verification against computed metrics | — (local NLI model) | `CRITIC_ENTAILMENT_THRESHOLD`, `CRITIC_NLI_MODEL` (.env) |

## Flow

```
ingest_check ─→ filings ─┐
             ─→ market  ─┼─→ analyst ─→ writer ─→ critic ─→ finalize
             ─→ news    ─┘                ↑           │
                                          └── revise ─┘  (≤ CRITIC_MAX_REVISIONS)
```

- Every node start/end/error publishes a `ProgressEvent` to Redis pub/sub
  (`equityscope:events:<run_id>`), streamed to the UI via SSE.
- All LLM calls go through `src/llm/router.py`: tier routing, retries,
  token/cost accounting, rate pacing (`GROQ_MAX_RPM`, `GROQ_TPM_*`), and
  automatic smart→fast fallback when a daily quota is exhausted.
- State is checkpointed to Postgres after every node — resume a crashed run
  with `python -m src.agents.orchestrator --resume <run_id>`.

## Adding an agent

1. Create `src/agents/<name>_agent.py` exposing `def <name>_node(state: AgentState) -> dict[str, Any]`.
2. Return only the state keys you own; lists written by parallel nodes need a
   reducer on the `AgentState` field (see `data_gaps`).
3. Wire it in `orchestrator.build_graph()` wrapped in `_with_events("<name>", ...)`.
4. Prompts live in `src/llm/prompts/<name>.md`, loaded via `load_prompt`.
