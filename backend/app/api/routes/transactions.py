import csv
import hashlib
import io
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import MemoryChunk, Transaction, User
from app.db.session import get_db

router = APIRouter()


class TransactionCreate(BaseModel):
    amount: Decimal = Field(..., description="Negative for expense, positive for income if you use that convention")
    currency: str = "USD"
    category: str | None = None
    description: str | None = None
    occurred_on: date


class TransactionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal
    currency: str
    category: str | None
    description: str | None
    occurred_on: date

    model_config = {"from_attributes": True}


@router.post("", response_model=TransactionOut)
def create_transaction(
    body: TransactionCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Transaction:
    row = Transaction(
        user_id=current.id,
        amount=body.amount,
        currency=body.currency,
        category=body.category,
        description=body.description,
        occurred_on=body.occurred_on,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[Transaction]:
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.user_id == current.id)
        .order_by(Transaction.occurred_on.desc())
    ).all()
    return list(rows)


class MonthlySummary(BaseModel):
    year: int
    month: int
    currency: str
    total_expenses: Decimal


@router.get("/summary/monthly", response_model=MonthlySummary)
def monthly_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    currency: str | None = Query(default=None, min_length=3, max_length=8),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> MonthlySummary:
    filters = (
        Transaction.user_id == current.id,
        func.extract("year", Transaction.occurred_on) == year,
        func.extract("month", Transaction.occurred_on) == month,
    )
    if currency:
        resolved_currency = currency.strip().upper()
    else:
        currencies = {
            str(value or "USD").upper()
            for value in db.scalars(select(Transaction.currency).where(*filters).distinct()).all()
        }
        if len(currencies) > 1:
            raise HTTPException(
                status_code=422,
                detail="Transactions use multiple currencies. Specify the currency query parameter.",
            )
        resolved_currency = next(iter(currencies), "USD")

    total = db.scalar(
        select(func.coalesce(func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)), 0)).where(
            *filters,
            func.upper(func.coalesce(Transaction.currency, "USD")) == resolved_currency,
        )
    )
    return MonthlySummary(
        year=year,
        month=month,
        currency=resolved_currency,
        total_expenses=Decimal(str(total or 0)),
    )


class CsvImportBody(BaseModel):
    csv_text: str = Field(..., min_length=10, max_length=300000)
    amount_column: str = Field(default="amount")
    date_column: str = Field(default="occurred_on")
    category_column: str = Field(default="category")
    description_column: str = Field(default="description")
    currency_column: str = Field(default="currency")
    default_currency: str = Field(default="USD", max_length=8)
    max_rows: int = Field(default=2000, ge=1, le=20000)


class CsvImportOut(BaseModel):
    imported_count: int
    skipped_count: int
    duplicate_rows_skipped: int = 0
    already_imported: bool = False
    sample_errors: list[str] = Field(default_factory=list)
    imported_preview: list["CsvImportPreview"] = Field(default_factory=list)


class CsvImportPreview(BaseModel):
    amount: Decimal
    currency: str
    category: str | None = None
    description: str | None = None
    occurred_on: date


_CSV_ALIASES = {
    "amount": ("amount", "transaction amount", "value", "sum"),
    "date": ("occurred_on", "date", "transaction date", "txn date", "posting date", "date of transaction"),
    "category": ("category", "type", "expense category"),
    "description": ("description", "narration", "merchant", "particulars", "details", "transaction description"),
    "currency": ("currency", "currency code"),
    "debit": ("debit", "withdrawal", "debit amount", "money out"),
    "credit": ("credit", "deposit", "credit amount", "money in"),
}


def _normalise_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def _find_column(headers: list[str], requested: str, kind: str) -> str | None:
    indexed = {_normalise_header(h): h for h in headers}
    wanted = _normalise_header(requested)
    if wanted in indexed:
        return indexed[wanted]
    for alias in _CSV_ALIASES[kind]:
        if alias in indexed:
            return indexed[alias]
    return None


def _parse_csv_date(raw: str) -> date:
    value = raw.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for fmt in (
        "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
        "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported date '{raw}' (use YYYY-MM-DD, DD/MM/YYYY, or DD-MM-YYYY)")


def _parse_csv_amount(raw: str) -> Decimal:
    value = raw.strip().replace(",", "").replace("₹", "").replace("$", "").replace("€", "")
    value = value.replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("() ")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount '{raw}'") from exc
    return -abs(amount) if negative else amount


def _csv_import_fingerprint(body: CsvImportBody) -> str:
    """Fingerprint the file and column-mapping settings to make repeated imports idempotent."""
    parts = (
        body.csv_text.lstrip("\ufeff"),
        body.amount_column,
        body.date_column,
        body.category_column,
        body.description_column,
        body.currency_column,
        body.default_currency.upper(),
        str(body.max_rows),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _transaction_import_signature(
    occurred_on: date,
    amount: Decimal,
    currency: str,
    category: str | None,
    description: str | None,
) -> tuple[date, str, Decimal, str, str]:
    """Normalized natural key used only to flag exact-match duplicate-looking rows."""
    return (
        occurred_on,
        (category or "").strip().casefold(),
        Decimal(amount).quantize(Decimal("0.01")),
        (currency or "USD").strip().upper(),
        (description or "").strip().casefold(),
    )


@router.post("/import/csv", response_model=CsvImportOut)
def import_transactions_csv(
    body: CsvImportBody,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> CsvImportOut:
    fingerprint = _csv_import_fingerprint(body)
    prior_import = db.scalar(
        select(MemoryChunk.id).where(
            MemoryChunk.user_id == current.id,
            MemoryChunk.source == "transaction_csv_import_fingerprint",
            MemoryChunk.content == fingerprint,
        )
    )
    if prior_import is not None:
        return CsvImportOut(
            imported_count=0,
            skipped_count=0,
            duplicate_rows_skipped=0,
            already_imported=True,
            sample_errors=[
                "This exact CSV content and column mapping was already imported for this account. "
                "No transactions were added again."
            ],
            imported_preview=[],
        )

    csv_text = body.csv_text.lstrip("\ufeff")
    stream = io.StringIO(csv_text)
    sample = csv_text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(stream, dialect=dialect)
    if not reader.fieldnames:
        return CsvImportOut(imported_count=0, skipped_count=0, sample_errors=["CSV header not found"])

    headers = [h for h in reader.fieldnames if h]
    amount_col = _find_column(headers, body.amount_column, "amount")
    date_col = _find_column(headers, body.date_column, "date")
    category_col = _find_column(headers, body.category_column, "category")
    description_col = _find_column(headers, body.description_column, "description")
    currency_col = _find_column(headers, body.currency_column, "currency")
    debit_col = _find_column(headers, "debit", "debit")
    credit_col = _find_column(headers, "credit", "credit")
    if not date_col or (not amount_col and not debit_col and not credit_col):
        needed = "date and amount (or debit/credit)"
        return CsvImportOut(
            imported_count=0,
            skipped_count=0,
            sample_errors=[f"Could not identify {needed} columns. Found: {', '.join(headers)}"],
        )

    # Parse first, then compare exact transaction signatures against existing rows.
    # This protects pre-fingerprint accounts when the same CSV is uploaded again.
    candidates: list[tuple[int, Decimal, date, str, str | None, str | None]] = []
    skipped = 0
    errors: list[str] = []
    for idx, row in enumerate(reader, start=2):
        if idx - 1 > body.max_rows:
            break
        try:
            raw_date = (row.get(date_col) or "").strip()
            raw_amount = (row.get(amount_col) or "").strip() if amount_col else ""
            if not raw_amount or not raw_date:
                debit = (row.get(debit_col) or "").strip() if debit_col else ""
                credit = (row.get(credit_col) or "").strip() if credit_col else ""
                if debit:
                    raw_amount = str(-abs(_parse_csv_amount(debit)))
                elif credit:
                    raw_amount = str(abs(_parse_csv_amount(credit)))
                else:
                    raise ValueError("missing amount")
            amount = _parse_csv_amount(raw_amount)
            occurred = _parse_csv_date(raw_date)
            currency = (row.get(currency_col) or body.default_currency).strip() if currency_col else body.default_currency
            currency = (currency or body.default_currency).strip()[:8].upper()
            category = ((row.get(category_col) or "").strip() or None) if category_col else None
            description = ((row.get(description_col) or "").strip() or None) if description_col else None
            candidates.append((idx, amount, occurred, currency, category, description))
        except Exception as exc:
            skipped += 1
            if len(errors) < 12:
                errors.append(f"line {idx}: {exc}")

    existing_counts: dict[tuple[date, str, Decimal, str, str], int] = {}
    if candidates:
        earliest = min(candidate[2] for candidate in candidates)
        latest = max(candidate[2] for candidate in candidates)
        existing_rows = db.scalars(
            select(Transaction).where(
                Transaction.user_id == current.id,
                Transaction.occurred_on >= earliest,
                Transaction.occurred_on <= latest,
            )
        ).all()
        for row in existing_rows:
            signature = _transaction_import_signature(
                row.occurred_on,
                row.amount,
                row.currency,
                row.category,
                row.description,
            )
            existing_counts[signature] = existing_counts.get(signature, 0) + 1

    incoming_counts: dict[tuple[date, str, Decimal, str, str], int] = {}
    imported = 0
    duplicate_rows_skipped = 0
    preview: list[CsvImportPreview] = []
    for idx, amount, occurred, currency, category, description in candidates:
        signature = _transaction_import_signature(occurred, amount, currency, category, description)
        incoming_counts[signature] = incoming_counts.get(signature, 0) + 1
        if incoming_counts[signature] <= existing_counts.get(signature, 0):
            skipped += 1
            duplicate_rows_skipped += 1
            if len(errors) < 12:
                errors.append(
                    f"line {idx}: exact-match transaction already exists; skipped to avoid duplicate import "
                    f"({occurred.isoformat()}, {category or 'uncategorized'}, {amount} {currency}, "
                    f"{description or 'no description'})."
                )
            continue

        db.add(
            Transaction(
                user_id=current.id,
                amount=amount,
                currency=currency,
                category=category,
                description=description,
                occurred_on=occurred,
            )
        )
        imported += 1
        if len(preview) < 8:
            preview.append(
                CsvImportPreview(
                    amount=amount,
                    currency=currency,
                    category=category,
                    description=description,
                    occurred_on=occurred,
                )
            )

    # Store a durable fingerprint in the same transaction as inserted rows. A
    # repeat upload is now a no-op even when the import had no newly added rows
    # because it matched legacy transactions created before this guard existed.
    if candidates:
        db.add(
            MemoryChunk(
                user_id=current.id,
                source="transaction_csv_import_fingerprint",
                content=fingerprint,
            )
        )
    db.commit()
    return CsvImportOut(
        imported_count=imported,
        skipped_count=skipped,
        duplicate_rows_skipped=duplicate_rows_skipped,
        already_imported=False,
        sample_errors=errors,
        imported_preview=preview,
    )

def _safe_csv_cell(value: object) -> str:
    """Prevent spreadsheet software from evaluating user-controlled CSV cells as formulas."""
    text_value = "" if value is None else str(value)
    if text_value.lstrip("\ufeff \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text_value
    return text_value


@router.get("/export/csv")
def export_transactions_csv(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Response:
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.user_id == current.id)
        .order_by(Transaction.occurred_on.desc())
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["occurred_on", "amount", "category", "description", "currency"])
    for row in rows:
        writer.writerow(
            [
                row.occurred_on.isoformat(),
                str(row.amount),
                _safe_csv_cell(row.category or ""),
                _safe_csv_cell(row.description or ""),
                _safe_csv_cell(row.currency),
            ]
        )

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="finmate-transactions.csv"'},
    )
