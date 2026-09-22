# Invoice System

## 1. Overview

Invoice functionality is distributed across the invoice agent, extraction/parsing code, API routes, PDF generation and frontend Invoice Studio.

Main areas:

```text
backend/app/agents/invoice_generator.py
backend/app/invoice/
backend/app/api/routes/invoices.py
frontend/src/
```

## 2. Chat invoice flow

A user can request an invoice using natural language.

The invoice specialist:

1. detects invoice intent
2. extracts line items
3. parses amounts
4. computes the total
5. creates a stable invoice reference
6. builds structured invoice metadata
7. returns export actions
8. optionally uses the local model for natural-language presentation

The structured artifact is kept separate from the generated prose.

## 3. Supported input patterns

The parser supports:

- description-first lines
- amount-first lines
- common invoice request prefixes
- expense-grounded invoice requests

For example:

```text
Create an invoice:
Website design 1200
Hosting 300
```

or:

```text
1200 Website design
300 Hosting
```

## 4. Expense-grounded invoices

The invoice flow can use recent available user transactions when the request clearly asks for an invoice based on expenses.

The implementation preserves the transaction descriptions/categories and amounts as structured line items.

This allows the final invoice artifact to be grounded in stored data rather than generated totals.

## 5. Invoice metadata

Successful invoice responses can preserve:

- `invoice_ref`
- `invoice_payload`
- `invoice_actions`
- `parsed_items_count`
- `parsed_total`
- `currency`

These fields are also propagated through bounded agentic execution.

## 6. Import pipeline

Supported document sources include:

- text-based PDF
- scanned/image invoice
- invoice CSV

The extraction path produces structured invoice data before the frontend displays or edits it.

## 7. OCR

Image/scanned document extraction uses Tesseract through `pytesseract`.

Text PDFs can be parsed without OCR. Scanned PDFs can fall back to image rendering plus OCR.

If Tesseract is not installed/configured, OCR-dependent requests can fail even though text-based PDF parsing continues to work.

## 8. Invoice CSV

Invoice CSV parsing can recognize fields such as:

- invoice number
- item
- quantity
- unit price
- amount
- subtotal
- CGST
- SGST
- total

CGST and SGST are treated as tax components of the invoice rather than separate line items.

## 9. PDF generation

ReportLab is used for PDF generation.

API endpoints:

```text
POST /api/invoices/pdf
POST /api/invoices/pdf/structured
```

The structured endpoint is suitable for edited/imported invoice data.

## 10. Frontend Invoice Studio

Invoice Studio supports:

1. importing invoice documents
2. extracting structured fields
3. editing line items
4. editing invoice data
5. exporting a PDF

Chat-created invoices can also expose PDF/CSV export actions directly in the conversation.

## 11. Important boundary

The invoice agent prepares structured invoice information and user-facing guidance.

The PDF endpoint is responsible for actually rendering the PDF.

This separation makes invoice artifacts deterministic and exportable.
