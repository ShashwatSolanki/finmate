"""Parse structured invoice fields from extracted OCR/PDF text."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.invoice.schemas import ParsedLineItem, ParseInvoiceResult, StructuredInvoice

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
_CURRENCY_CODE = re.compile(r"\b(USD|EUR|GBP|INR|JPY|CAD|AUD|CHF|SGD)\b", re.I)

_INV_NUM = re.compile(
    r"(?:invoice\s*(?:#|no\.?|number)?[:\s]*)([A-Z0-9][A-Z0-9\-_/]{2,24})",
    re.I,
)
_DATE = re.compile(
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.I,
)
_VENDOR = re.compile(r"(?:from|vendor|seller|supplier)[:\s]+(.{2,80})", re.I)
_BILL_TO = re.compile(r"(?:bill\s*to|billed\s*to|customer)[:\s]+(.{2,120})", re.I)

# Recognized currency markers that can prefix an amount, e.g. "Rs. 799.00", "$1,234.56", "INR 498.00".
# "Rs[.,]?" tolerates OCR misreading the period in "Rs." as a comma ("Rs,"), which happens often
# enough with small/antialiased screenshot text to be worth handling explicitly.
_CURRENCY_PREFIX = r"(?:Rs[.,]?|INR|USD|EUR|GBP|[\$€£₹])"

# description ... amount  OR  amount description  OR  qty x unit = total
_LINE_DESC_AMT = re.compile(
    r"^(.{2,80}?)\s+([\d,]+\.\d{2})\s*$",
)
_LINE_AMT_DESC = re.compile(
    r"^([\d,]+\.\d{2})\s+(.{2,80}?)\s*$",
)
_LINE_QTY = re.compile(
    r"^(.{2,60}?)\s+(\d+(?:\.\d+)?)\s*x\s*([\d,]+\.\d{2})\s*=\s*([\d,]+\.\d{2})\s*$",
    re.I,
)
# description, optional qty, then one or more currency-prefixed amounts (e.g. unit price + line total).
# Handles rows like: "Wireless Mouse 1 Rs. 799.00 Rs. 799.00"
_LINE_GENERIC = re.compile(
    rf"^(.{{2,80}}?)\s+(?:(\d+(?:\.\d+)?)\s+)?"
    rf"((?:{_CURRENCY_PREFIX}?\s*[\d,]+\.\d{{2}}\s*){{1,3}})$",
    re.I,
)
_AMOUNT_TOKEN = re.compile(rf"{_CURRENCY_PREFIX}?\s*[\d,]+\.\d{{2}}", re.I)

_TOTAL = re.compile(
    rf"(?:grand\s*)?total\s*(?:\([^)]*\))?\s*:?\s*{_CURRENCY_PREFIX}?\s*([\d,]+\.\d{{2}})",
    re.I,
)
_SUBTOTAL = re.compile(
    rf"sub\s*total\s*(?:\([^)]*\))?\s*:?\s*{_CURRENCY_PREFIX}?\s*([\d,]+\.\d{{2}})",
    re.I,
)
_TAX = re.compile(
    rf"(?:tax|vat|gst)\s*(?:\([^)]*\))?\s*:?\s*{_CURRENCY_PREFIX}?\s*([\d,]+\.\d{{2}})",
    re.I,
)

_SKIP_LINE = re.compile(
    r"^(invoice|bill\s*to|ship\s*to|description|qty|quantity|amount|unit\s*price|subtotal|total|tax|vat|gst|notes?|payment)\b",
    re.I,
)

_DEDUP_STRIP_AMOUNT = re.compile(rf"{_CURRENCY_PREFIX}\s*[\d,]+\.\d{{2}}", re.I)
_DEDUP_STRIP_BARE_AMOUNT = re.compile(r"\b[\d,]+\.\d{2}\b")
_DEDUP_SEPARATORS = re.compile(r"[\u2014\u2013|]")  # em dash, en dash, pipe
_DEDUP_TRAILING_QTY = re.compile(r"\s+\d+(?:\.\d+)?$")


def _normalize_desc_for_dedup(desc: str) -> str:
    """Collapse different renderings of the same line item to one dedup key.

    The same row can show up as "Wireless Mouse" (clean match), "Wireless Mouse 1 Rs. 799.00"
    (qty/price still embedded by a looser pattern), or "Wireless Mouse — 1 — Rs. 799.00" (the
    pdfplumber table-cell fallback, dash-joined) — all for the identical item. Without
    normalizing first, each rendering gets its own dedup key and the same item is added 2-3 times.
    """
    s = _DEDUP_STRIP_AMOUNT.sub("", desc)
    s = _DEDUP_STRIP_BARE_AMOUNT.sub("", s)
    s = _DEDUP_SEPARATORS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _DEDUP_TRAILING_QTY.sub("", s).strip()
    return s.lower()


def _to_decimal(raw: str) -> Decimal | None:
    """Convert a numeric/currency string like 'Rs. 2,499.00' or '$1,234.56' to Decimal.

    Strips known currency words/symbols and thousands separators first, since a naive
    ``replace(",", "")`` leaves prefixes like "Rs." in place and breaks Decimal parsing.
    """
    cleaned = re.sub(r"(?i)rs[.,]?|inr|usd|eur|gbp|chf|cad|aud|sgd|[\$€£₹¥,]", "", raw).strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _detect_currency(text: str) -> str:
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    m = _CURRENCY_CODE.search(text)
    return m.group(1).upper() if m else "USD"


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    return m.group(1).strip()


def _parse_line_items(lines: list[str]) -> list[ParsedLineItem]:
    items: list[ParsedLineItem] = []
    seen: set[str] = set()
    # Currency-prefixed token, e.g. "Rs. 799.00" — requires the prefix (used only to *detect*
    # whether a line has 2+ separately-tagged amounts, distinct from _AMOUNT_TOKEN which allows
    # the prefix to be optional when actually extracting values).
    currency_tagged_token = re.compile(rf"{_CURRENCY_PREFIX}\s*[\d,]+\.\d{{2}}", re.I)

    for raw in lines:
        line = raw.strip()
        if not line or _SKIP_LINE.match(line):
            continue

        # Rows with 2+ currency-tagged amounts (e.g. "Item 1 Rs. 799.00 Rs. 799.00" — unit price
        # and line total both tagged) are unambiguous; check this before the older patterns below,
        # since those patterns can greedily swallow the whole row into the description group.
        if len(currency_tagged_token.findall(line)) >= 2:
            m = _LINE_GENERIC.match(line)
            if m:
                desc, qty_s, amounts_blob = m.groups()
                tokens = [t.group(0) for t in _AMOUNT_TOKEN.finditer(amounts_blob)]
                if tokens:
                    amt = _to_decimal(tokens[-1])
                    unit_price = _to_decimal(tokens[0]) if len(tokens) > 1 else None
                    desc = desc.replace("|", "").strip()
                    if amt and amt > 0 and desc:
                        key = f"{_normalize_desc_for_dedup(desc)}:{amt}"
                        if key not in seen:
                            seen.add(key)
                            items.append(
                                ParsedLineItem(
                                    description=desc,
                                    quantity=float(qty_s) if qty_s else None,
                                    unit_price=unit_price,
                                    amount=amt,
                                )
                            )
                continue

        m = _LINE_QTY.match(line)
        if m:
            desc, qty_s, unit_s, amt_s = m.groups()
            amt = _to_decimal(amt_s)
            if amt and amt > 0:
                key = f"{_normalize_desc_for_dedup(desc)}:{amt}"
                if key not in seen:
                    seen.add(key)
                    items.append(
                        ParsedLineItem(
                            description=desc.strip(),
                            quantity=float(qty_s),
                            unit_price=_to_decimal(unit_s),
                            amount=amt,
                        )
                    )
            continue

        m = _LINE_DESC_AMT.match(line)
        if m:
            desc, amt_s = m.groups()
            amt = _to_decimal(amt_s)
            if amt and amt > 0 and not _TOTAL.match(line):
                key = f"{_normalize_desc_for_dedup(desc)}:{amt}"
                if key not in seen:
                    seen.add(key)
                    items.append(ParsedLineItem(description=desc.strip(), amount=amt))
            continue

        m = _LINE_AMT_DESC.match(line)
        if m:
            amt_s, desc = m.groups()
            amt = _to_decimal(amt_s)
            if amt and amt > 0:
                key = f"{_normalize_desc_for_dedup(desc)}:{amt}"
                if key not in seen:
                    seen.add(key)
                    items.append(ParsedLineItem(description=desc.strip(), amount=amt))
            continue

        # Single currency-prefixed amount with a quantity, e.g. "Item 1 Rs. 799.00"
        m = _LINE_GENERIC.match(line)
        if m:
            desc, qty_s, amounts_blob = m.groups()
            tokens = [t.group(0) for t in _AMOUNT_TOKEN.finditer(amounts_blob)]
            if not tokens:
                continue
            amt = _to_decimal(tokens[-1])
            unit_price = _to_decimal(tokens[0]) if len(tokens) > 1 else None
            if amt and amt > 0:
                key = f"{_normalize_desc_for_dedup(desc)}:{amt}"
                if key not in seen:
                    seen.add(key)
                    items.append(
                        ParsedLineItem(
                            description=desc.strip(),
                            quantity=float(qty_s) if qty_s else None,
                            unit_price=unit_price,
                            amount=amt,
                        )
                    )

    return items[:30]


def _confidence(items: list[ParsedLineItem], total: Decimal | None) -> str:
    if items and total is not None:
        return "high"
    if items:
        return "medium"
    return "low"


def parse_invoice_text(
    text: str,
    *,
    source_type: str,
    filename: str,
    warnings: list[str] | None = None,
) -> ParseInvoiceResult:
    warnings = list(warnings or [])
    normalized = text.replace("\r", "\n")
    lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]

    currency = _detect_currency(normalized)
    invoice_number = _first_match(_INV_NUM, normalized)
    invoice_date = _first_match(_DATE, normalized)
    vendor_name = _first_match(_VENDOR, normalized)
    bill_to = _first_match(_BILL_TO, normalized)

    line_items = _parse_line_items(lines)

    # Table rows from pdfplumber use " | ". OCR table detection can be inconsistent and only
    # insert "|" on some rows, but this still helps recover rows the line-by-line parser missed.
    existing_keys = {f"{_normalize_desc_for_dedup(i.description)}:{i.amount}" for i in line_items}
    for line in lines:
        if " | " in line and not _SKIP_LINE.match(line):
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 2:
                # last cell often amount; strip currency words/symbols, not just $ and ₹
                amt = _to_decimal(cells[-1])
                if amt and amt > 0:
                    desc = " — ".join(cells[:-1])[:120]
                    key = f"{_normalize_desc_for_dedup(desc)}:{amt}"
                    if desc and key not in existing_keys:
                        existing_keys.add(key)
                        line_items.append(ParsedLineItem(description=desc, amount=amt))

    subtotal = _to_decimal(_first_match(_SUBTOTAL, normalized) or "")
    tax = _to_decimal(_first_match(_TAX, normalized) or "")
    total_matches = [_to_decimal(m.group(1)) for m in _TOTAL.finditer(normalized)]
    total_matches = [t for t in total_matches if t is not None]
    total = total_matches[-1] if total_matches else None

    if not line_items and total is not None:
        line_items.append(ParsedLineItem(description="Invoice total (single line)", amount=total))

    if line_items and total is None:
        total = sum((i.amount for i in line_items), start=Decimal("0"))
    elif line_items and total is not None and subtotal is None:
        computed = sum((i.amount for i in line_items), start=Decimal("0"))
        if abs(computed - total) > Decimal("0.02") and tax is None:
            subtotal = computed

    if not line_items:
        warnings.append("No line items detected — edit fields manually or upload a clearer document.")

    invoice = StructuredInvoice(
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=None,
        vendor_name=vendor_name,
        bill_to=bill_to,
        currency=currency,
        line_items=line_items,
        subtotal=subtotal,
        tax=tax,
        total=total,
    )

    preview = normalized[:2000] + ("…" if len(normalized) > 2000 else "")

    return ParseInvoiceResult(
        source_type=source_type,
        filename=filename,
        confidence=_confidence(line_items, total),
        extracted_text_preview=preview,
        invoice=invoice,
        warnings=warnings,
    )