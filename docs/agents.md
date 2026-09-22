# Agents & Orchestration

## 1. Specialist agents

FinMate currently has three specialist agents:

1. Budget Planner
2. Investment Analyser
3. Invoice Generator

Shared types are defined in:

```text
backend/app/agents/types.py
```

## 2. Hybrid routing

The normal intent classifier is implemented in:

```text
backend/app/agents/intent.py
```

It combines:

- regex/keyword signals
- sentence-embedding similarity
- prototype examples

The goal is to avoid relying on a single routing mechanism.

## 3. Routing safeguards

The orchestrator preserves authoritative selections.

Important safeguards include:

- explicit specialist selections are respected
- clear specialist intent can be selected before the general LLM path
- follow-up routing can preserve an ongoing specialist conversation
- an LLM-generated tag cannot silently override an already selected specialist
- invoice artifacts are preserved through agentic synthesis

## 4. Budget Agent

File:

```text
backend/app/agents/budget_planner.py
```

Typical workflow:

1. determine the 30-day lookback
2. load the user's transactions
3. determine currency
4. aggregate spending by category
5. calculate spending insights
6. add relevant RAG context
7. optionally generate a natural-language response
8. fall back to deterministic output if generation fails

The transaction database remains the source of truth.

## 5. Investment Agent

File:

```text
backend/app/agents/investment_analyser.py
```

The agent supports:

- ticker detection
- company-name-to-ticker mapping
- Yahoo Finance validation
- historical market data
- last close
- previous-session change
- percentage change
- 20-day SMA
- session/52-week ranges
- stored portfolio analysis
- risk-based allocation mode

Portfolio questions are grounded in stored `InvestmentHolding` records.

## 6. Invoice Agent

File:

```text
backend/app/agents/invoice_generator.py
```

The invoice agent supports natural-language invoice requests and structured line-item parsing.

It can preserve:

- invoice reference
- invoice payload
- parsed item count
- parsed total
- currency
- export actions

Expense-grounded invoice requests can use recent available transactions as line items.

## 7. Bounded agentic workflow

Implementation:

```text
backend/app/agents/agentic_orchestrator.py
```

The planner identifies requests spanning multiple domains.

Example:

```text
"Analyze my spending, tell me how much I can invest,
and generate an invoice for my expenses."
```

The plan can contain:

```text
Budget → Investment → Invoice
```

The default maximum is three specialist steps.

For each step:

1. select the specialist
2. execute it
3. capture the verified observation
4. pass prior observations to later specialists
5. continue until the bounded plan is complete

The final result is synthesized from the specialist observations.

## 8. Failure handling

A specialist failure is recorded rather than destroying successful observations.

If synthesis is weak or unavailable, deterministic synthesis preserves the specialist results.

If the complete agentic execution cannot produce useful observations, the normal orchestrator remains available as a fallback.

## 9. Confidence

Implementation:

```text
backend/app/agents/confidence.py
```

Confidence is an explainable heuristic, not a calibrated probability.

Current factors:

| Factor | Weight |
|---|---:|
| Execution success | 40% |
| Retrieval availability signal | 20% |
| Evidence signal | 25% |
| Response completeness | 15% |

Levels:

- high: ≥ 0.80
- medium: ≥ 0.60
- low: < 0.60

The metadata exposes the score, level, method and component factors.

## 10. Future recursive orchestration

A true loop such as:

```text
Goal
 ↓
Plan
 ↓
Act
 ↓
Observe
 ↓
Goal satisfied?
 ├─ yes → synthesize
 └─ no  → re-plan
```

is future work.

Do not document this as an existing feature.
