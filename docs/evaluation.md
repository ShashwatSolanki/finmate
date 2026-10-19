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
- expected specialist routing and actual execution-path metadata
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

Parser regression coverage includes captured Tesseract output from the café receipt example, wrapped receipt descriptions, OCR decimal commas, Indian currency inference from GST/FSSAI markers, bill numbers and dates, correct separation of subtotal/tax/grand total from product rows, and arithmetic reconciliation. If the printed subtotal cannot be read reliably, the parser reconstructs it only when the itemized sum plus detected tax matches the independently extracted grand total, and lowers confidence with a visible warning. An explicit multi-item prompt with quantity/unit-price clauses must preserve every item and calculate the subtotal, stated percentage tax, and final total. Upload metadata and any extraction warnings must persist in conversation history and remain visible when the conversation is reloaded. Follow-up invoice questions must use the saved extracted fields, not attempt to parse the follow-up prompt as if it were the original file.

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

The controlled research benchmark lives in [`research/evaluation/`](../research/evaluation/README.md). It includes a deterministic synthetic dataset generator, per-case API/direct-model runner, gold-number and memory scoring, retrieval Hit@5/MRR measurements, latency capture, and paired bootstrap summaries. The summary also scores actual execution path from trusted runtime metadata separately from the agent label returned in the response. Numeric saved-memory answers are parsed from user-visible reply text with grouping-separator tolerance and numeric-token matching, rather than raw substring matching or metadata-only evidence. It is deliberately separate from CI because the runner can reset a dedicated test account's chat history, memory, transactions, budgets, and holdings before each case.

For local latency investigations, FinMate logs model-load/cache, prompt-preparation, generation, decode, and postprocessing timings, plus token counts and execution device at info level. These diagnostics omit prompt and financial content, leaving warning logs for actionable problems.

When the budget planner has no recent transaction categories, it uses its deterministic no-data response instead of invoking local generation to repeat that there is no transaction history. The LLM path remains enabled when transaction data exists.

Income and investment amount extraction must require explicit semantic labels. An arbitrary currency amount (for example, a client invoice value) must not be interpreted as monthly income or investable capital; the agentic smoke test exposed this cross-domain parsing risk, so regression tests cover it.

Natural-language invoice requests with conjunctions such as "configuration INR 400 plus support INR 500" must preserve each separately stated line item and derive the INR 900 subtotal/total. Validate the structured payload itself—including currency, line-item amounts, subtotal, and final total—rather than treating HTTP success or artifact presence as proof of invoice correctness. Numeric benchmark correctness is scored from visible reply prose only—not internal metadata or the hidden JSON contract tail. Category-spending totals follow the transactions API convention: negative transaction amounts are expenses and are reported as positive magnitudes, excluding credits/refunds; mixed-currency arithmetic is refused rather than silently combining currencies. Regression prompts also cover CSV import audits and two-complete-calendar-month comparisons. The report path must not route the token `CSV` as a stock ticker, use LLM-generated negative debit totals, invert month-over-month direction, or parse money/date/category references from appended specialist observations. Any additional numeric amount that does not map to a supported expense label makes deterministic remainder calculation abstain instead of silently omitting the item, regardless of whether that amount includes a currency marker. Explicit saved-profile fact lookups use user-authored or onboarding/profile evidence; assistant-generated prior replies are not treated as proof of a saved fact. When risk preference is absent from retrieved context, allocation wording must identify any moderate-risk split as illustrative and must not claim that it is the user's saved profile.

For a controlled agentic-latency experiment, `FINMATE_AGENTIC_SYNTHESIS=false` bypasses only the final LLM synthesis call after specialist execution and is now the default. Set it to `true` only when explicitly testing synthesis.

The 2026-10-10 21-case pilot recorded 21/21 HTTP successes and automated task completions with synthesis both off and on. Multi-agent mean latency was 0.036 seconds with synthesis off versus 50.197 seconds with it on; overall p95 rose from 0.0543 seconds to 57.2059 seconds. Reviewed synthesis-on responses returned the deterministic fallback wording rather than a materially improved synthesis. The synthesis prompt and acceptance guard previously disagreed about whether all specialist tags were required; the prompt was aligned and a mocked regression test added. However, the subsequent live three-agent smoke still took 44.351 seconds and returned the deterministic fallback. Because synthesis added substantial latency without a demonstrated answer-quality improvement in the reviewed pilot, final synthesis is now opt-in and defaults to false. Only repeat synthesis-on pilots after the generated output can be confirmed accepted and useful.

The `FINMATE_USE_RAG` switch exists to support a semantic-retrieval ablation; it is enabled by default. The old `backend/scripts/evaluate_rag.py` fixture uses mocked embeddings and remains an implementation sanity check—not a live retrieval-quality result. Likewise, `backend/scripts/evaluate_ai.py` checks the response contract and routing metadata, but does not establish financial answer correctness.

**Safety requirement:** use a disposable local evaluation database whose name includes `eval` or `test`, and a fresh synthetic user ID. The runner refuses destructive resets against other database names, bootstraps a non-login evaluation identity when needed, and mints a short-lived local access token if one is not supplied. This guard is a backstop, not a substitute for confirming the database is disposable. Save each run's raw JSONL output and environment settings, and never run the destructive reset option against staging/production or a real user's account. Read the research evaluation README for exact commands and limitations.


## Latest validation run (2026-10-11)

A fresh 21-case stratified API pilot was run against application commit `54b3fc0147637e91ff435857922a3e8031c3861a` using condition `full`, a fresh synthetic user, and the dedicated disposable `finmate_eval` PostgreSQL database. Server flags were `FINMATE_USE_RAG=true`, `FINMATE_AGENTIC_MODE=true`, `FINMATE_USE_LLM=true`, and `FINMATE_AGENTIC_SYNTHESIS=false`.

- HTTP success: **21/21**
- Automated task completion: **21/21**
- Runner-reported failed cases: **0**
- Dataset used: local `research/evaluation/benchmark-final-review.jsonl`
- Raw results: local `research/evaluation/results/final-pilot-20261011-021636.jsonl`
- Manifest: local `research/evaluation/results/final-pilot-20261011-021636.manifest.json`

The raw results, manifest, and generated dataset are local evaluation artifacts and are not committed by this documentation update; preserve them for audit and reproducibility. This pilot exercised the scoring checks configured for its 21 selected cases. It is not a human audit of every claim and does not establish population-level financial correctness. Two non-fatal environment warnings appeared: a `RequestsDependencyWarning` about installed HTTP-related dependency versions and a Transformers warning that `TRANSFORMERS_CACHE` is deprecated in favor of `HF_HOME`. Neither stopped the run.


## Full benchmark baseline and follow-up audit (2026-10-11)

The first full-system 120-case run used the frozen local `research/evaluation/benchmark-final-review.jsonl` (SHA-256 `6757ca2f715450d687a69ff04913a56408a877db83049ff47ee9c4455aebc127`) on application commit `4a3f0096c573d1d85920792582c733e542da6b35`, using a dedicated `finmate_eval` database, local API port 8010, and RAG/agentic/LLM/embeddings enabled with final synthesis disabled. It achieved 120/120 HTTP successes and 117/120 automated task completions. Numerical tasks completed 40/40 and memory tasks 40/40; the routing group completed 37/40. Routing checks scored 17/17 for explicit single-specialist labels, execution-path checks 39/40, agentic specialist-sequence checks 18/20, invoice-artifact presence 18/19, and structured invoice correctness 20/20 across the numerical and routing tasks that had invoice gold labels.

Inspection identified (i) an under-specified routing/invoice prompt whose scorer required an invoice artifact despite no line items being supplied and (ii) two missing planner signals: cautious allocation for excess funds did not select investment analysis, and household costs did not select the budget specialist. The generator and regression tests are being revised on a separate branch; the original generated dataset, raw results, and manifest are preserved. Use a new, versioned dataset for subsequent experiments, freeze its hash before running conditions, and rerun the full condition on that version before comparing ablations. These results are a synthetic development benchmark, not evidence of real-world or population-level financial accuracy.
