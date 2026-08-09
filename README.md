# 🚀 EquityScope — AI-Powered Financial Due Diligence Platform

> **An enterprise-grade multi-agent AI system that automates financial due diligence by analyzing SEC filings, market data, and financial news to generate comprehensive, citation-backed investment reports.**

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![Angular](https://img.shields.io/badge/Angular-Frontend-red)
![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-orange)
![LLMs](https://img.shields.io/badge/LLMs-Groq-purple)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

---

# 📖 Overview

Traditional financial due diligence requires analysts to manually review hundreds of pages of SEC filings, cross-reference financial statements, validate market information, and track recent news before making informed investment decisions.

**EquityScope** automates this process using a **Multi-Agent AI architecture**, enabling intelligent financial research with verifiable, citation-backed insights.

The platform combines **Large Language Models (LLMs)**, **Retrieval-Augmented Generation (RAG)**, and **specialized AI agents** to analyze structured and unstructured financial information, delivering investment-grade due diligence reports within minutes.

---

# ✨ Key Features

- 🤖 Multi-Agent AI Architecture
- 📑 SEC Filing Analysis
- 📈 Financial Statement Understanding
- 📰 Real-Time News Analysis
- 🔍 Citation-Based Responses
- 📊 Automated Due Diligence Reports
- ⚡ Retrieval-Augmented Generation (RAG)
- 🌐 Modern Angular Dashboard
- 🚀 FastAPI Backend
- 🐳 Dockerized Deployment

---

# 🏗️ System Architecture

```
                        User
                          │
                          ▼
                Angular Frontend
                          │
                          ▼
                   FastAPI Backend
                          │
                          ▼
             LangGraph Supervisor Agent
                          │
 ┌──────────────┬──────────────┬──────────────┐
 │              │              │              │
 ▼              ▼              ▼              ▼
SEC Agent   Market Agent   News Agent   Report Agent
 │              │              │              │
 └──────────────┴──────────────┴──────────────┘
                          │
                          ▼
                     RAG Pipeline
                          │
                          ▼
                 Citation-backed Report
```

---

# 🛠 Tech Stack

### AI & Machine Learning

- Large Language Models (Groq)
- LangGraph
- Retrieval-Augmented Generation (RAG)
- Prompt Engineering
- Multi-Agent Systems

### Backend

- Python
- FastAPI
- Pydantic
- Docker

### Frontend

- Angular
- TypeScript

### Data Sources

- SEC EDGAR Filings
- Financial Market Data
- News Sources

---

# 📂 Project Structure

```
.
├── backend/
│   ├── src/
│   ├── tests/
│   ├── evals/
│   ├── data/
│   ├── scripts/
│   ├── Dockerfile
│   ├── Makefile
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   ├── Dockerfile
│   ├── proxy.conf.json
│   └── package.json
│
└── docker-compose.yml
```

---

# 🚀 Quick Start

## Docker (Recommended)

```bash
cp .env.example .env
```

Configure:

```text
GROQ_API_KEY=your_key
EDGAR_USER_AGENT=your_email
```

Run:

```bash
docker compose up -d --build
```

### Services

| Service | URL |
|----------|-----|
| Frontend | http://localhost:8501 |
| Backend API | http://localhost:8000 |

---

# 💻 Local Development

## Backend

```bash
cd backend

make setup

make dev
```

Backend runs on:

```
http://localhost:8000
```

---

## Frontend

```bash
cd frontend

npm install

npm start
```

Frontend runs on:

```
http://localhost:4200
```

---

# 📚 Documentation

Detailed documentation is available for each component.

- 📦 **Backend:** [backend/README.md](backend/README.md)
- 🎨 **Frontend:** [frontend/README.md](frontend/README.md)

---

# 🎯 Future Roadmap

- AI Risk Scoring
- Valuation Analysis
- Peer Company Comparison
- Portfolio Analysis
- Earnings Call Summarization
- Interactive Investment Dashboard
- Export to PDF & Excel
- Multi-Company Comparative Reports

---

# 👨‍💻 Author

**Prathamesh Pund**

AI Engineer focused on Enterprise AI, Multi-Agent Systems, Generative AI, and Intelligent Automation.

If you found this project interesting, consider giving it a ⭐.