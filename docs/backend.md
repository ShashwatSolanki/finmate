# Backend

## 1. Stack

The backend uses:

- FastAPI
- SQLAlchemy
- PostgreSQL
- Pydantic / pydantic-settings
- bcrypt/Passlib
- JWT
- Sentence Transformers
- yfinance
- ReportLab
- optional PEFT/Transformers inference

## 2. Structure

```text
backend/
├── app/
│   ├── api/
│   │   └── routes/
│   ├── agents/
│   ├── db/
│   ├── invoice/
│   ├── ml/
│   ├── rag/
│   ├── security/
│   └── services/
├── scripts/
├── tests/
└── requirements.txt
```

## 3. Database models

The core models include:

### User

Account identity and password hash.

### Transaction

Financial transaction data:

- user
- amount
- currency
- category
- description
- occurred date

### Budget

Category limits and budget periods. The model exists, but a complete budget CRUD API is not currently exposed.

### InvestmentHolding

Portfolio positions:

- symbol
- quantity
- average cost
- currency

Used for grounded portfolio valuation.

### MemoryChunk

Retrievable text for onboarding and conversation memory.

### ChatSession / ChatMessage

Persisted conversations and individual turns.

## 4. Authentication

Relevant files:

```text
backend/app/api/routes/auth.py
backend/app/api/deps.py
backend/app/security/passwords.py
backend/app/security/jwt_tokens.py
```

Registration:

1. validate input
2. check existing email
3. hash password
4. create user and verification token
5. send verification email
6. issue JWT

Login:

1. find user
2. verify password
3. check verification status
4. issue JWT

The implementation provides dual-token authentication (short-lived access tokens + long-lived rotating refresh tokens), Google OAuth integration, email verification & password reset, account linking/unlinking, and brute-force rate limiting.

## 5. Main API groups

### Auth

```text
POST /api/auth/register
POST /api/auth/verify-email
POST /api/auth/resend-verification
POST /api/auth/forgot-password
POST /api/auth/reset-password
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
POST /api/auth/google
POST /api/auth/link/google
POST /api/auth/unlink/google
```

### Users

```text
GET    /api/users/me
PATCH  /api/users/me
POST   /api/users/change-password
DELETE /api/users/me
POST   /api/users/onboarding
GET    /api/users/onboarding/latest
GET    /api/users/onboarding/profile
```

### Transactions

```text
POST /api/transactions
GET  /api/transactions
GET  /api/transactions/summary/monthly
POST /api/transactions/import/csv
GET  /api/transactions/export/csv
```

### Portfolio

```text
GET    /api/portfolio/summary
POST   /api/portfolio/holdings
DELETE /api/portfolio/holdings/{symbol}
```

### Invoices

```text
POST /api/invoices/parse
POST /api/invoices/parse/csv
POST /api/invoices/pdf
POST /api/invoices/pdf/structured
```

### Agents

```text
GET /api/agents
```

### Chat

```text
POST /api/chat/message
```

### Conversations

```text
GET    /api/conversations
POST   /api/conversations
PATCH  /api/conversations/{session_id}
DELETE /api/conversations/{session_id}
GET    /api/conversations/{session_id}/messages
```

## 6. Important backend responsibilities

### Chat route

The chat route:

- authenticates requests
- retrieves memory
- loads onboarding context
- loads recent conversation context
- invokes orchestration
- enforces response format
- calculates/attaches metadata
- persists conversation state

### Transaction route

Handles:

- transaction creation
- listing
- monthly summary
- CSV import
- CSV export

### Portfolio route

Handles stored holdings and current portfolio summaries.

### Invoice route

Handles parsing and PDF generation endpoints.

## 7. Services

Important service responsibilities include:

- market-data access through Yahoo Finance
- deterministic spending insights
- invoice parsing/extraction
- PDF generation

## 8. Database initialization

The current application initializes ORM tables through SQLAlchemy metadata.

A full production migration framework is not currently the primary database setup mechanism.

## 9. Backend design rule

The backend should keep business-critical calculations close to the data source and outside the language model.

For example:

```text
Transaction rows → Python/SQL aggregation → financial result
                                     ↓
                               LLM explanation
```

rather than:

```text
Transaction rows → LLM guesses total
```
