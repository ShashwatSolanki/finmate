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

```bash
cd backend
python -m venv .venv
```

Windows:

```bash
.venv\\Scripts\\pip install -r requirements.txt
.venv\\Scripts\\uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
- embedding model
- routing embedding weight
- LoRA adapter path
- local LLM enable/disable flag
- generation length
- agentic mode
- maximum agentic steps
- optional Tesseract executable path

Secrets should be supplied through environment variables rather than committed to Git.

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

A production deployment should eventually use a proper migration system rather than relying solely on table creation.

## 9. Useful validation commands

Backend tests:

```bash
cd backend
python -m unittest discover -s tests -p "test_*.py"
```

RAG evaluation:

```bash
python scripts/evaluate_rag.py
```

AI evaluation:

```bash
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
