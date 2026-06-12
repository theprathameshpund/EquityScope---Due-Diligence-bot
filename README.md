# EquityScope — Multi-Agent Financial Due Diligence Copilot

Multi-agent research over SEC filings, market data, and news — every claim cited and verified.

## Project Structure

```
.
├── backend/        # Python FastAPI backend (LangGraph agents, RAG, EDGAR tools)
│   ├── src/        # Application source code
│   ├── tests/      # Unit and integration tests
│   ├── evals/      # Evaluation scripts
│   ├── data/       # Filings cache and generated reports
│   ├── scripts/    # Infrastructure helper scripts
│   ├── Dockerfile
│   ├── Makefile
│   └── pyproject.toml
├── frontend/       # Angular SPA (served by nginx in production)
│   ├── src/        # Angular source code
│   ├── Dockerfile
│   ├── proxy.conf.json
│   └── package.json
└── docker-compose.yml
```

## Quick Start

### With Docker Compose (recommended)

```bash
cp .env.example .env
# Fill in GROQ_API_KEY and EDGAR_USER_AGENT in .env
docker compose up -d --build
```

- API: http://localhost:8000
- Frontend: http://localhost:8501

### Local Development

**Backend:**
```bash
cd backend
make setup   # creates venv and installs deps
make dev     # starts infra + API on port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm start    # Angular dev server on http://localhost:4200 (proxies /api to :8000)
```

## See Also

- [backend/README.md](backend/README.md) — full backend documentation
- [frontend/README.md](frontend/README.md) — Angular frontend documentation
