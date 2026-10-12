"""Parse structured invoice fields from extracted OCR/PDF text."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.invoice.schemas import ParsedLineItem, ParseInvoiceResult, StructuredInvoice

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
_CURRENCY_CODE = re.compile(r"\b(USD|EUR|GBP|INR|JPY|CAD|AUD|CHF|SGD)\b", re.I)

_INV_NUM = re.compile(
    r"(?:(?:invoice|bill)\s*(?:#|no\.?|number)?[:\s]*)([A-Z0-9][A-Z0-9\-_/]{2,24})",
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
    rf"(?:tax|vat|(?:c|s|i)?gst)\s*(?:\d+(?:\.\d+)?\s*%)?\s*"
    rf"(?:\([^)]*\))?\s*:?\s*{_CURRENCY_PREFIX}?\s*([\d,]+\.\d{{2}})",
    re.I,
)

_SKIP_LINE = re.compile(
    r"^(invoice|bill\s*to|ship\s*to|description|qty|quantity|amount|unit\s*price|subtotal|grand\s*total|total|tax|vat|(?:c|s|i)?gst|notes?|payment)\b",
    re.I,
)

# Receipt/OCR table rows with a quantity, unit price, and line amount.
_RECEIPT_ITEM_ROW = re.compile(
    rf"^(?P<description>.+?)\s+(?P<quantity>\d+(?:\.\d+)?)\s+"
    rf"(?P<unit_price>(?:{_CURRENCY_PREFIX}\s*)?[\d,]+\.\d{{2}})\s+"
    rf"(?P<amount>(?:{_CURRENCY_PREFIX}\s*)?[\d,]+\.\d{{2}})\s*$",
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
    if m:
        return m.group(1).upper()
    # Indian restaurant receipts often lose the rupee glyph during OCR, but the
    # CGST/SGST/IGST labels are a reliable signal that amounts are denominated in INR.
    if re.search(r"\b(?:CGST|SGST|IGST|FSSAI)\b", text, re.I):
        return "INR"
    if re.search(r"\bRs[.,]?\s*\d", text, re.I):
        return "INR"
    return "USD"


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


def _prepare_receipt_table_lines(lines: list[str]) -> tuple[list[str], bool]:
    """Join wrapped item descriptions around OCR rows with aligned numeric columns.

    Receipt OCR frequently puts the first line of a description beside its price,
    then emits the remaining description words on following lines before the next
    priced row. Those continuation lines belong to the previous item, not a new row.
    """
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.search(r"\bitem\b", line, re.I)
            and re.search(r"\b(?:qty|quantity)\b", line, re.I)
            and re.search(r"\b(?:price|amount)\b", line, re.I)
        ),
        None,
    )
    if header_index is None:
        return lines, False

    end_index = len(lines)
    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        if (
            re.match(r"^\s*(?:sub\s*$|subtotal\b|grand\s*total\b|total\b)", line, re.I)
            or re.match(r"^\s*(?:c?gst|sgst|igst|tax|vat)\b", line, re.I)
        ):
            end_index = index
            break

    parsed_rows: list[dict[str, str]] = []
    pending_description: list[str] = []
    for line in lines[header_index + 1 : end_index]:
        match = _RECEIPT_ITEM_ROW.match(line)
        if not match:
            # Ignore a repeated table header, but retain otherwise unpriced text as
            # a likely wrapped description line.
            if re.search(r"\bitem\b", line, re.I) and re.search(r"\b(?:qty|quantity|price|amount)\b", line, re.I):
                continue
            pending_description.append(line.strip())
            continue

        description = match.group("description").strip(" |:-")
        if parsed_rows and pending_description:
            parsed_rows[-1]["description"] = " ".join(
                [parsed_rows[-1]["description"], *pending_description]
            ).strip()
        elif not parsed_rows and pending_description:
            description = " ".join([*pending_description, description]).strip()
        pending_description.clear()

        parsed_rows.append(
            {
                "description": description,
                "quantity": match.group("quantity"),
                "unit_price": match.group("unit_price"),
                "amount": match.group("amount"),
            }
        )

    if pending_description and parsed_rows:
        parsed_rows[-1]["description"] = " ".join(
            [parsed_rows[-1]["description"], *pending_description]
        ).strip()

    if not parsed_rows:
        # Keep the old line-based parser as a fallback, but let the caller know a
        # table was present so a printed grand total isn't fabricated into an item.
        return lines, True

    canonical_rows = [
        f"{row['description']} {row['quantity']} {row['unit_price']} {row['amount']}"
        for row in parsed_rows
        if row["description"]
    ]
    return [*lines[: header_index + 1], *canonical_rows, *lines[end_index:]], True


def _infer_receipt_vendor(lines: list[str]) -> str | None:
    """Use a plausible title line above a receipt table when no vendor label exists."""
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.search(r"\bitem\b", line, re.I)
            and re.search(r"\b(?:qty|quantity)\b", line, re.I)
            and re.search(r"\b(?:price|amount)\b", line, re.I)
        ),
        None,
    )
    if header_index is None:
        return None

    for line in lines[:header_index]:
        candidate = line.strip(" -|:")
        if not candidate or len(candidate) > 100:
            continue
        if not re.search(r"[A-Za-z]{3}", candidate):
            continue
        if re.search(
            r"\b(?:fssai|gstin|gst|lic(?:ence|ense)?\s*no|phone|mobile|email|www|https?://|"
            r"road|rd\.?|street|st\.?|main\s*road|address|invoice|bill\s*no|date|cashier|"
            r"table\s*no|total|subtotal|grand\s*total|dine\s*in)\b",
            candidate,
            re.I,
        ):
            continue
        if re.search(r"\d{3,}", candidate):
            continue
        # First plausible title-like line before the address/details is the receipt merchant.
        return candidate
    return None


def _confidence(
    items: list[ParsedLineItem],
    total: Decimal | None,
    warnings: list[str] | None = None,
) -> str:
    if not items:
        return "low"
    if warnings or total is None:
        return "medium"
    return "high"


def parse_invoice_text(
    text: str,
    *,
    source_type: str,
    filename: str,
    warnings: list[str] | None = None,
) -> ParseInvoiceResult:
    warnings = list(warnings or [])
    normalized = text.replace("\r", "\n")
    # Tesseract commonly turns a small ₹ glyph into a lowercase "n" (as in
    # "n1,499.00"). Only replace it when it directly prefixes a money value,
    # never in ordinary prose such as vendor names or descriptions.
    normalized = re.sub(r"(?<![A-Za-z])n\s*(?=\d[\d,]*\.\d{2}\b)", "₹", normalized)
    # Occasionally OCR inserts a spurious digit between "Grand Total" and the
    # amount (e.g. "Grand Total 7 1029.00"). Strip only that narrow, label-adjacent
    # artifact before extracting totals; keep the original preview for user review.
    total_text = re.sub(
        r"(?im)^(\s*grand\s*total)\s+\d{1,3}\s+(?=(?:₹\s*)?[\d,]+\.\d{2}\s*$)",
        r"\1 ",
        normalized,
    )
    lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]
    item_parse_lines, has_item_table = _prepare_receipt_table_lines(lines)

    currency = _detect_currency(normalized)
    invoice_number = _first_match(_INV_NUM, normalized)
    invoice_date = _first_match(_DATE, normalized)
    vendor_name = _first_match(_VENDOR, normalized)
    if vendor_name is None and has_item_table:
        vendor_name = _infer_receipt_vendor(lines)
    bill_to = _first_match(_BILL_TO, normalized)

    line_items = _parse_line_items(item_parse_lines)

    # Table rows from pdfplumber use " | ". OCR table detection can be inconsistent and only
    # insert "|" on some rows, but this still helps recover rows the line-by-line parser missed.
    existing_keys = {f"{_normalize_desc_for_dedup(i.description)}:{i.amount}" for i in line_items}
    for line in item_parse_lines:
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
    # Indian invoices frequently split GST into CGST and SGST. They belong in
    # one tax total, never as invoice line items.
    tax_values = [_to_decimal(m.group(1)) for m in _TAX.finditer(normalized)]
    tax = sum((value for value in tax_values if value is not None), start=Decimal("0")) or None
    total_matches = [_to_decimal(m.group(1)) for m in _TOTAL.finditer(total_text)]
    total_matches = [t for t in total_matches if t is not None]
    total = total_matches[-1] if total_matches else None

    if not line_items and total is not None and not has_item_table:
        line_items.append(ParsedLineItem(description="Invoice total (single line)", amount=total))
        warnings.append(
            "No individual line items were detected; the printed total is retained as a placeholder. "
            "Review the invoice before exporting it."
        )

    if line_items and total is None:
        total = sum((i.amount for i in line_items), start=Decimal("0"))
    elif line_items and total is not None and subtotal is None:
        computed = sum((i.amount for i in line_items), start=Decimal("0"))
        if abs(computed - total) > Decimal("0.02") and tax is None:
            subtotal = computed

    if not line_items:
        warnings.append("No line items detected — edit fields manually or upload a clearer document.")
    elif line_items:
        item_sum = sum((item.amount for item in line_items), start=Decimal("0"))
        if subtotal is not None and abs(item_sum - subtotal) > Decimal("0.02"):
            warnings.append(
                f"Line-item amounts sum to {item_sum:.2f}, which does not match the printed "
                f"subtotal {subtotal:.2f}. Review OCR before exporting."
            )
    if subtotal is not None and tax is not None and total is not None:
        if abs((subtotal + tax) - total) > Decimal("0.02"):
            warnings.append(
                f"Printed subtotal plus tax ({subtotal + tax:.2f}) does not match the printed "
                f"total ({total:.2f}). Review the extracted fields."
            )

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
        confidence=_confidence(line_items, total, warnings),
        extracted_text_preview=preview,
        invoice=invoice,
        warnings=warnings,
    )
