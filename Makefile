# EquityScope developer commands.
# On Windows without GNU make, run the underlying commands directly (see README).

PYTHON ?= python
VENV   ?= .venv

ifeq ($(OS),Windows_NT)
VENV_PY = $(VENV)/Scripts/python.exe
else
VENV_PY = $(VENV)/bin/python
endif

.PHONY: setup dev up down lint test eval

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "Setup complete. Fill in GROQ_API_KEY and EDGAR_USER_AGENT in .env"

dev:
	docker compose up -d qdrant postgres redis
	$(VENV_PY) -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000 &
	$(VENV_PY) -m streamlit run frontend/app.py --server.port 8501

up:
	docker compose up -d --build

down:
	docker compose down

lint:
	$(VENV_PY) -m ruff check src tests evals
	$(VENV_PY) -m mypy

test:
	$(VENV_PY) -m pytest tests -q

eval:
	$(VENV_PY) evals/run_numeric_check.py
	$(VENV_PY) evals/run_faithfulness.py
	$(VENV_PY) evals/run_retrieval.py
