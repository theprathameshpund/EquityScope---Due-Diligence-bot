# 🎨 EquityScope Frontend

> **Modern Angular frontend for EquityScope — an AI-powered financial due diligence platform that enables analysts to interact with multi-agent AI workflows, analyze companies, and generate citation-backed investment reports.**

![Angular](https://img.shields.io/badge/Angular-20-red)
![TypeScript](https://img.shields.io/badge/TypeScript-5-blue)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED)
![Status](https://img.shields.io/badge/Status-Active-success)

---

# 📖 Overview

The EquityScope frontend provides a clean and responsive interface for interacting with the AI-powered due diligence platform.

Users can:

- 🔍 Search and analyze public companies
- 📄 Submit financial due diligence requests
- 🤖 Interact with AI-generated investment insights
- 📑 View citation-backed research reports
- 📊 Explore financial summaries and supporting evidence

The frontend communicates with the FastAPI backend through REST APIs and provides a seamless experience for enterprise financial research.

---

# ✨ Features

- 📊 Modern Dashboard UI
- 🔍 Company Search
- 🤖 AI-Powered Due Diligence Interface
- 📄 Citation-Based Report Viewer
- ⚡ Fast & Responsive Design
- 📱 Responsive Layout
- 🔗 REST API Integration
- 🐳 Docker Support

---

# 🏗 Frontend Architecture

```
User
   │
   ▼
Angular UI
   │
   ▼
Services
   │
   ▼
REST API
   │
   ▼
FastAPI Backend
```

---

# 🛠 Tech Stack

- Angular 20
- TypeScript
- RxJS
- Angular Router
- HTTP Client
- Docker
- Nginx (Production)

---

# 📂 Project Structure

```
src/
├── app/
│   ├── components/
│   ├── pages/
│   ├── services/
│   ├── models/
│   ├── guards/
│   ├── interceptors/
│   └── shared/
│
├── assets/
├── environments/
└── styles/
```

---

# 🚀 Getting Started

## Install Dependencies

```bash
npm install
```

---

## Start Development Server

```bash
npm start
```

or

```bash
ng serve
```

Application will be available at:

```
http://localhost:4200
```

---

# 🔗 Backend Configuration

The frontend communicates with the FastAPI backend.

Default API endpoint:

```
http://localhost:8000
```

Proxy configuration is provided through:

```
proxy.conf.json
```

---

# 📦 Production Build

```bash
ng build
```

The optimized production build will be generated inside:

```
dist/
```

---

# 🐳 Docker

Build the frontend container:

```bash
docker build -t equityscope-ui .
```

Run:

```bash
docker run -p 4200:80 equityscope-ui
```

---

# 📸 Screenshots

> Add application screenshots here.

Example:

- Dashboard
- Company Search
- AI Report
- Financial Summary
- Research Workflow

---

# 🔮 Future Improvements

- Dark Mode
- Interactive Financial Charts
- Real-Time Report Generation
- Company Comparison Dashboard
- Report Export (PDF)
- User Authentication
- Portfolio Tracking
- AI Chat Interface

---

# 📄 Related Documentation

- 📦 Backend Documentation → `../backend/README.md`
- 🚀 Root Project README → `../README.md`

---

# 👨‍💻 Author

**Prathamesh Pund**

AI Engineer focused on Enterprise AI, Multi-Agent Systems, Generative AI, and Intelligent Automation.