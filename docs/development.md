# Development, Configuration & Deployment

## 1. Prerequisites

Typical local requirements:

- Docker / Docker Compose
- Python 3.11+
- Node.js 20+
- PostgreSQL through Docker Compose

## 2. Start the database

From the repository root:

```bash
docker compose up -d
```

Verify:

```bash
docker compose ps
```

## 3. Start the backend

Run these commands from the repository root, then keep the backend terminal open.

```bash
cd backend
python -m venv .venv
```

Windows (PowerShell):

```powershell
.venv\\Scripts\\python -m pip install -r requirements.txt
copy .env.example .env
.venv\\Scripts\\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

macOS / Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend serves Swagger at:

```text
http://127.0.0.1:8000/docs
```

## 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Typical local frontend:

```text
http://127.0.0.1:5173
```

## 5. Configuration

Important backend settings include:

- database URL
- JWT secret
- JWT algorithm
- token lifetime
- email provider (`EMAIL_PROVIDER=smtp` or `resend`) and SMTP/Resend credentials
- CORS allowlist (`CORS_ORIGINS`)
- shared auth rate limiting (`AUTH_RATE_LIMIT_REDIS_URL`)
- optional Google OAuth client ID
- embedding model
- routing embedding weight
- LoRA adapter path
- local LLM enable/disable flag
- generation length
- agentic mode
- maximum agentic steps
- optional Tesseract executable path

Secrets should be supplied through environment variables rather than committed to Git. The checked-in `.env.example` enables mock email for local development so registration can be exercised without SMTP; it is not for real users. Never enable mock email or mock Google authentication outside isolated tests/development. Production and staging require a non-placeholder JWT secret of at least 32 characters, real email delivery, shared Redis rate limiting, an explicit HTTPS-only `CORS_ORIGINS` allowlist, and mock Google auth disabled. Startup validation rejects incomplete staging/production configuration.

### Deploying a free staging environment

See [Free-tier staging](free-staging.md) for the Render Blueprint, Neon/PostgreSQL and Upstash/Redis setup, Resend configuration, cost limitations, and isolated regression checklist. The staging profile intentionally disables local Qwen and semantic embeddings to fit free-instance memory limits; it is not feature-equivalent to the full local AI configuration.

## 6. Local LLM

The local model is optional.

When enabled, the backend loads the Qwen2.5-1.5B base model plus the local LoRA adapter.

When disabled or unavailable, deterministic specialist behavior remains available.

Because CPU inference can be slow, the first request can take significantly longer than later requests.

## 7. External dependencies

### Yahoo Finance

Investment analysis depends on market-data availability through the project's market-data service.

External failures should be treated as expected failure modes rather than assumed permanent application failures.

### Tesseract

Scanned invoice OCR requires Tesseract.

Text-based PDFs do not require OCR.

## 8. Database behavior

The current application initializes SQLAlchemy metadata at startup.

For updating existing development databases with new auth fields:

```bash
cd backend
python scripts/migrate_auth.py
```

## 9. Useful validation commands

Backend tests:

```bash
cd backend
python -m unittest discover -s tests -p "test_*.py"
```

Frontend build:

```bash
cd frontend
npm run build
```

RAG evaluation:

```bash
cd backend
python scripts/evaluate_rag.py
```

AI evaluation:

```bash
cd backend
python scripts/evaluate_ai.py --token <JWT_TOKEN>
```

## 10. Troubleshooting

### Database authentication errors

Check:

1. PostgreSQL container status
2. host port mapping
3. `DATABASE_URL`
4. stale development volumes

### Slow first request

The embedding model is lazy-loaded. Local Qwen inference can also be expensive on CPU.

### OCR failure

Verify Tesseract installation and `TESSERACT_CMD` when automatic discovery does not work.

### Yahoo Finance failure

Check network availability and reinstall/update the backend dependencies if the market-data client is not functioning.

## 11. Development principle

When changing a module:

1. update its module documentation
2. update the architecture document if the data flow changed
3. update evaluation documentation if behavior/metrics changed
4. update README only for user-facing setup/features

This keeps documentation modular instead of recreating another monolithic reference file.
