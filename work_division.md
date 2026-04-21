# FinMate Project Work Division (4 Members)

## Overview
The work is divided to ensure:
- Equal weightage for each member
- Balanced mix of theory, diagrams, and implementation
- Each member can defend their section in viva

---

# Person 1 — Introduction + Core Design Concepts

## Sections

### 1. Intro + Problem (3–4 pages)

#### Content:
- Background of personal finance systems
- Problems faced by users:
  - fragmented tools
  - lack of intelligent insights
- Problem statement:
  - no unified system for budgeting, investing, invoicing
- Proposed solution:
  - FinMate as a multi-agent AI assistant
- Objectives:
  - intelligent decision-making
  - structured outputs
  - personalization

---

### 2. Design Details (Part 1)

#### Topics:

**Novelty**
- Multi-agent system instead of single chatbot
- Hybrid routing (rules + embeddings)
- Structured responses (JSON + text)

**Innovativeness**
- Combination of backend + ML + finance tools
- Use of LoRA fine-tuning

**Interoperability**
- Communication between:
  - frontend
  - backend
  - database
  - agents
- API-based architecture

**Performance**
- Fast rule-based execution
- Controlled LLM usage
- token limits and caching

---

## Expected Understanding
- Overall system idea
- Why this system is different
- High-level system behavior

---

# Person 2 — Data + ML + System Quality

## Sections

### 1. Data (3–4 pages)

#### 4.1 Overview
- Types of data:
  - transactions
  - investment data
  - synthetic invoice data
- Purpose:
  - training
  - evaluation

---

#### 4.2 Dataset
- Sources:
  - CSV datasets
  - synthetic data generation
- Final dataset:
  - ~4500 samples
  - balanced across agents

---

#### 4.3 Data Preprocessing
- Cleaning:
  - remove duplicates
  - fix encoding issues
- Transformation:
  - CSV → JSONL
- Formatting:
  - system / user / assistant structure

---

### 2. Design Details (Part 2)

#### Topics:

**Security**
- JWT authentication
- password hashing
- protected endpoints

**Reliability**
- fallback system:
  - LLM → rule-based
- ensures stable output

**Maintainability**
- modular architecture
- separate agents and services

**Portability**
- works on local and cloud systems
- Docker support for database

---

## Expected Understanding
- Data pipeline
- Model training basics
- System stability and safety

---

# Person 3 — Architecture + System Design

## Sections

### 1. Architecture (2–3 pages)

#### Content:
- System layers:
  - Frontend (React)
  - Backend (FastAPI)
  - Database (PostgreSQL)
- Request flow:
  - User → API → Orchestrator → Agent → Response
- Include architecture diagram

---

### 2. Design Description (Part 1)

#### 6.1 Master Class Diagram
Include:
- User
- Transaction
- MemoryChunk
- Agents
- Orchestrator

Show:
- relationships
- interactions

---

#### 6.2 ER Diagram / Swimlane / State Diagram

**ER Diagram**
- Tables:
  - User
  - Transactions
  - MemoryChunk
- relationships and keys

**Swimlane / State Diagram**
- chat flow:
  - user input
  - routing
  - response generation

---

## Expected Understanding
- Backend structure
- Data relationships
- System flow

---

# Person 4 — Implementation + UI + Outputs

## Sections

### 1. Tech + Implementation (6–7 pages)

#### Backend:
- FastAPI framework
- JWT authentication
- API endpoints

#### Agents:
- Budget Planner
- Investment Analyser
- Invoice Generator

#### Features:
- CSV import
- PDF invoice generation
- RAG memory system

---

### 2. Design Description (Part 2)

#### 6.3 UI Diagrams
- login screen
- dashboard
- chat interface

---

#### 6.4 Report Layouts
- invoice format
- structured chat output

---

#### 6.5 External Interfaces
- yfinance API
- LLM inference
- database communication

---

#### 6.6 Deployment Diagram
- frontend (Vite)
- backend (FastAPI)
- database (Postgres)

---

### 3. Others (1–2 pages)

Include:
- future scope
- limitations of system

---

## Expected Understanding
- Full system execution
- frontend-backend interaction
- real-world usage

---

# Final Summary

| Person | Focus Area |
|--------|------------|
| Person 1 | Introduction + Design Concepts |
| Person 2 | Data + ML + System Quality |
| Person 3 | Architecture + Diagrams |
| Person 4 | Implementation + UI |

---

# Important Notes

- Maintain consistent writing style across all sections
- Use same terminology throughout the report
- Add cross-references between sections where possible
- Ensure diagrams are clear and labeled
