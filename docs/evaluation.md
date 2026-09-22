# Evaluation & Testing

## 1. Purpose

FinMate uses multiple levels of validation:

1. unit tests
2. integration tests
3. deterministic RAG evaluation
4. end-to-end AI regression evaluation

These should be kept separate because they measure different things.

## 2. Unit tests

Tests cover important isolated behaviors including:

- RAG ranking/context construction
- confidence calculation
- data-backed investment behavior
- invoice behavior
- agentic orchestration
- invoice artifact preservation
- routing edge cases

The current Member 2 development cycle reached 29 backend unit tests passing locally.

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
