# EquityScope backend developer commands.
# Run from the backend/ directory, or use: make -C backend <target>

PYTHON ?= python
VENV   ?= ../.venv

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
	@test -f ../.env || cp ../.env.example ../.env
	@echo "Setup complete. Fill in GROQ_API_KEY and EDGAR_USER_AGENT in .env"

dev:
	docker compose -f ../docker-compose.yml up -d qdrant postgres redis
	$(VENV_PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

up:
	docker compose -f ../docker-compose.yml up -d --build

down:
	docker compose -f ../docker-compose.yml down

lint:
	$(VENV_PY) -m ruff check src tests evals
	$(VENV_PY) -m mypy

test:
	$(VENV_PY) -m pytest tests -q

eval:
	$(VENV_PY) evals/run_numeric_check.py
	$(VENV_PY) evals/run_faithfulness.py
	$(VENV_PY) evals/run_retrieval.py
