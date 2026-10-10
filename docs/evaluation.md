# Evaluation & Testing

## 1. Purpose

FinMate uses multiple levels of validation:

1. unit tests
2. integration tests
3. deterministic RAG evaluation
4. end-to-end AI regression evaluation

These should be kept separate because they measure different things. Security regression coverage includes OTP hashing, upload-size enforcement, and rejection of files whose contents do not match their supplied type.

GitHub Actions runs Python dependency auditing, backend pytest, the production-dependency npm audit, frontend Vitest tests, and the Vite production build for pull requests and pushes to `main` / `deploy/free-staging-setup`. See [the CI workflow](../.github/workflows/backend-tests.yml). The Resend and staging-settings tests use mocked HTTP/config values; they do not require cloud credentials or provision hosted services.

## 2. Unit tests

Tests cover important isolated behaviors including:

- RAG ranking/context construction
- confidence calculation
- data-backed investment behavior
- invoice behavior
- agentic orchestration
- invoice artifact preservation
- routing edge cases

The backend suite covers authentication, budget validation, financial API behavior, data-backed agents, invoices, orchestration, and retrieval. CI also runs the separate research harness tests from `research/evaluation/`, including scoring helpers, dataset integrity, and the destructive-database guard. Use the CI run as the current source of truth for test count and pass/fail status.

## 3. RAG evaluation

Files:

```text
backend/scripts/evaluate_rag.py
backend/tests/test_rag_evaluation.py
```

The evaluator reports:

- Hit@2
- Mean Reciprocal Rank (MRR)

The evaluation uses a small deterministic fixture.

Important interpretation:

> A perfect score on the fixture validates the retrieval implementation for those fixture cases. It is not evidence that production retrieval is perfect.

## 4. Final AI evaluation

File:

```text
backend/scripts/evaluate_ai.py
```

Dataset:

```text
training/data/final_ai_eval.jsonl
```

The evaluator checks:

- HTTP success
- expected specialist routing
- reply-format compliance
- confidence metadata
- observed RAG usage
- bounded agentic execution
- invoice artifact preservation

Example commands:

```bash
cd backend
python scripts/evaluate_ai.py --token <JWT_TOKEN>
python scripts/evaluate_ai.py --token <JWT_TOKEN> --quick
python scripts/evaluate_ai.py --token <JWT_TOKEN> --cases 3,5,11
```

## 5. Reply-format evaluation

A compliant response must have:

1. an agent tag as the first meaningful line
2. natural-language output
3. a final JSON object

The JSON is expected to contain the structured planning fields used by the application.

## 6. Agentic evaluation

Multi-domain cases validate that:

- multiple specialists are actually executed
- observations are passed between steps
- the bounded step limit is respected
- successful observations survive failed specialists
- synthesis does not silently discard required specialist coverage

## 7. Invoice artifact evaluation

Invoice cases additionally verify structured metadata such as:

- invoice reference
- invoice payload
- invoice actions

This is important because a fluent response without a usable invoice artifact is not sufficient for the application.

## 8. Testing philosophy

The evaluation suite is an implementation regression suite.

It should not be presented as:

- a benchmark against other models
- a statistically representative user study
- a production reliability guarantee
- a calibrated confidence measurement

## 9. Recommended CI gate

For future CI, the project can treat the following as minimum gates:

```text
unit tests
   ↓
integration tests
   ↓
RAG regression
   ↓
AI regression
   ↓
frontend build
```


## 10. Research benchmark (separate from regression tests)

The controlled research benchmark lives in [`research/evaluation/`](../research/evaluation/README.md). It includes a deterministic synthetic dataset generator, per-case API/direct-model runner, gold-number and memory scoring, retrieval Hit@5/MRR measurements, latency capture, and paired bootstrap summaries. Numeric saved-memory answers are parsed from user-visible reply text with grouping-separator tolerance and numeric-token matching, rather than raw substring matching or metadata-only evidence. It is deliberately separate from CI because the runner can reset a dedicated test account's chat history, memory, transactions, budgets, and holdings before each case.

For local latency investigations, FinMate logs model-load/cache, prompt-preparation, generation, decode, and postprocessing timings, plus token counts and execution device. These diagnostics omit prompt and financial content. The timing diagnostic currently uses warning-level logging for visibility during local profiling and should return to info level when profiling is complete.

When the budget planner has no recent transaction categories, it uses its deterministic no-data response instead of invoking local generation to repeat that there is no transaction history. The LLM path remains enabled when transaction data exists.

The `FINMATE_USE_RAG` switch exists to support a semantic-retrieval ablation; it is enabled by default. The old `backend/scripts/evaluate_rag.py` fixture uses mocked embeddings and remains an implementation sanity check—not a live retrieval-quality result. Likewise, `backend/scripts/evaluate_ai.py` checks the response contract and routing metadata, but does not establish financial answer correctness.

**Safety requirement:** use a disposable local evaluation database whose name includes `eval` or `test`, and a fresh synthetic user ID. The runner refuses destructive resets against other database names, bootstraps a non-login evaluation identity when needed, and mints a short-lived local access token if one is not supplied. This guard is a backstop, not a substitute for confirming the database is disposable. Save each run's raw JSONL output and environment settings, and never run the destructive reset option against staging/production or a real user's account. Read the research evaluation README for exact commands and limitations.
