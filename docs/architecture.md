# FinMate Architecture

## 1. Overview

FinMate is a multi-agent personal finance assistant built as a layered full-stack application.

The main runtime components are:

- React + Vite frontend
- FastAPI backend
- PostgreSQL database
- specialist finance agents
- RAG/memory layer
- optional local Qwen2.5-1.5B LoRA inference
- external/tool-backed services such as Yahoo Finance and ReportLab/Tesseract

## 2. Runtime architecture

```text
React / Vite
    │
    │ authenticated REST API
    ▼
FastAPI
    │
    ├── Authentication / user context
    ├── Chat / conversation routes
    ├── Transaction APIs
    ├── Portfolio APIs
    └── Invoice APIs
    │
    ▼
Context + Orchestration
    │
    ├── RAG retrieval
    ├── recent conversation context
    ├── onboarding context
    ├── normal specialist routing
    └── bounded agentic planning
    │
    ├──────────────┬───────────────┬───────────────┐
    ▼              ▼               ▼
 Budget        Investment       Invoice
 Agent         Agent            Agent
    │              │               │
    ▼              ▼               ▼
PostgreSQL     Yahoo Finance    Invoice services
transactions   + holdings      + OCR + PDF/CSV
    │
    └──────────────┬─────────────────────┐
                   ▼                     ▼
             RAG / Memory          Optional Qwen
             MiniLM + cosine       LoRA adapter
                   │
                   ▼
               PostgreSQL
```

## 3. Core design principle

FinMate separates deterministic financial work from natural-language generation.

### Deterministic layer

The application should rely on code and stored data for operations such as:

- transaction aggregation
- portfolio valuation
- invoice totals and structured artifacts
- routing safeguards
- persistence
- export generation

### AI layer

The local model is used for:

- natural-language responses
- structured response generation
- optional agentic synthesis

If generation fails or is unavailable, specialist agents can return deterministic fallback responses.

This design prevents a model failure from making the application unusable.

## 4. End-to-end chat flow

1. The frontend sends `POST /api/chat/message`.
2. The JWT is authenticated.
3. Relevant memory is retrieved.
4. Recent conversation turns are loaded.
5. The latest onboarding context is loaded.
6. The system checks whether the request requires multiple specialists.
7. If it is multi-domain, the bounded agentic workflow may execute.
8. Otherwise, explicit routing/follow-up safeguards/hybrid routing select a specialist.
9. The specialist performs database or external-tool work.
10. Optional Qwen generation is performed.
11. The response contract is enforced.
12. Confidence metadata is calculated.
13. Useful conversation data is persisted.
14. The frontend renders the cleaned response and metadata.

## 5. Response contract

Every assistant response is normalized into:

```text
[AGENT: BUDGET|INVESTMENT|INVOICE]

Natural-language response...

{"intent":"...","steps":[...],"tools_needed":[...],"notes":"..."}
```

The tag and JSON are machine-readable. The frontend removes those implementation details before displaying the natural-language response.

## 6. Architectural boundaries

### Frontend

Responsible for:

- authentication screens
- chat UI
- conversation selection
- onboarding/settings
- invoice import/editing
- transaction import/export
- displaying metadata and artifacts

### Backend

Responsible for:

- authentication
- API validation
- persistence
- orchestration
- specialist execution
- context retrieval
- invoice/portfolio services

### Database

Responsible for durable application state:

- users
- transactions
- budgets
- holdings
- memory
- conversations
- messages

### AI/RAG

Responsible for:

- semantic context retrieval
- specialist natural-language generation
- bounded cross-agent synthesis

## 7. Current scope boundaries

The current implementation is a bounded multi-agent system.

It is **not** an unbounded recursive AutoGPT loop. The current planner executes at most `agentic_max_steps` specialists, defaulting to 3.

A future recursive design can be added later without changing the specialist responsibilities.
