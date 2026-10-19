# Financial audit regression prompts

Use these prompts in a test FinMate account after loading controlled fixtures. Do not use a production account for duplicate-data tests.

## 1. Last-30-day transaction integrity

**Input**

> Audit my recorded transactions for the last 30 days. Report the date range, transaction count, total expenses, and totals by category in INR. Show how many transactions were imported from the most recent CSV and identify duplicate-looking rows, but do not delete or modify any transactions. Treat expense debits as positive spending in the report.

**Expected checks**

- The date range is 30 inclusive calendar dates.
- Expenses are calculated from recorded expense debits, displayed as positive values, and grouped by currency/category.
- CSV imported/skipped counts come from the saved import receipt, not from the reporting-window transaction count.
- Duplicate-looking groups are flagged but not removed or excluded.
- A duplicate-risk summary quantifies repeated groups, affected rows, extra matching rows, and repeated-copy value separately from the full reported total.
- Data completeness is called **unverified** when rows exist; an empty period is explicitly **insufficient**, not zero spending.
- Uploaded invoices/receipts are not counted as transactions unless explicitly imported.

## 2. Complete-calendar-month comparison

**Input**

> Compare my previous complete calendar month with the complete calendar month before it. Show actual spending totals by category for both months, the absolute change, and percentage change. Identify increases and decreases. Use recorded transactions only, convert expense debits to positive values, and explicitly say when either month has insufficient data.

**Expected checks**

- Uses the two most recent complete calendar months, excluding the current partial month.
- Shows category and total changes using recorded expense debits only.
- For a month with no recorded transactions, explicitly says data is insufficient and actual spending is unknown, not zero.
- For a month with rows, explicitly says data completeness cannot be verified from the rows alone.
- Displays duplicate-risk findings independently for each month.
- A percentage change is not invented when the older-period denominator is zero.

## 3. Financial-data provenance audit

**Input**

> Audit my financial data before giving any recommendation. Identify which figures are directly supported by recorded transactions, which are from my uploaded receipt, which are from market quotes, and which are missing. Do not treat an uploaded receipt as a transaction, do not assume unrecorded expenses are zero, and do not invent monthly totals or currency conversions. Explain what additional data is required before estimating my investable surplus.

**Expected checks**

- The plan executes Budget Planner, Investment Analyser, and Invoice Generator when transaction data, market quotes, and an uploaded invoice/receipt are all in scope.
- Recorded transaction totals are labelled as ledger-derived and duplicate-risk caveats remain visible.
- A recent dated market snapshot in retrieved conversation context is enumerated with ticker, session date, closing price/currency, and SMA only where recorded.
- A saved holding alone is not represented as proof of a historical quote. If no dated quote snapshot can be found, the output says the quote provenance is unverified instead of claiming that no ticker exists.
- The uploaded document is reported as a separate source. It is not added to transaction totals, and an invoice total is not represented as paid income without payment evidence.
- Income source is identified as current-request or saved/retrieved context and is not described as independently verified pay data unless a suitable source exists.
- No USD/INR conversion or guaranteed investable-surplus estimate is made without a dated FX source, verified income, adequate expense coverage, available cash/balances, liabilities, upcoming obligations, and reserve target.

## 4. Invoice payload, PDF, and CSV consistency

**Input**

> Create an invoice from Shashwat Solanki to Acme Technologies: Website Development 2 units at INR 12,000 each, Hosting 1 unit at INR 2,000, and SEO Audit 1 unit at INR 3,000, with 18% tax. Preserve the invoice fields for PDF and CSV export.

**Expected values**

- Website Development: 2 × INR 12,000 = INR 24,000.
- Hosting: 1 × INR 2,000 = INR 2,000.
- SEO Audit: 1 × INR 3,000 = INR 3,000.
- Subtotal: INR 29,000.
- Tax at 18%: INR 5,220.
- Total: INR 34,220.

**Manual export checks**

1. Compare the invoice reference displayed in chat with `invoice_payload.invoice_number`.
2. Download the PDF and verify its printed invoice number matches that same reference.
3. Download the CSV and verify its invoice number, vendor, bill-to name, currency, quantities, unit prices, line amounts, subtotal, tax, and total match the structured payload.
4. Import/review an uploaded invoice that already has an invoice number and confirm the source invoice number is preserved rather than replaced with a new random reference.
5. Generating or downloading exports must not silently mutate transaction rows.

## 5. Investable-surplus safeguards

**Input**

> Using only verified saved income and recorded transactions, estimate my investable surplus. Show the source of income, expenses in the same currency and date range, and every missing input. Do not assume missing expenses are zero and do not convert currencies without a dated exchange rate.

**Expected checks**

- If income is found only in saved/retrieved context, the response identifies that provenance and says it is not independently verified against a pay record.
- If income or expense coverage is missing, FinMate abstains from estimating investable surplus.
- A simple income-minus-recorded-expenses remainder is labelled as provisional, not guaranteed investable surplus.
- No portfolio recommendation is generated from a negative/unknown remainder or from portfolio market value alone.

## Test-run notes

The repository regression tests for these issues live in `backend/tests/test_financial_audit_regressions.py`. Run them from the backend's configured test environment with the project test command (typically `pytest tests/test_financial_audit_regressions.py`). Also run the existing backend test suite before merging. These prompts test behavior; they do not replace automated assertions.
