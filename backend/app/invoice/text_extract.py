"""Extract plain text from invoice PDFs and images."""

from __future__ import annotations

import io
import logging
import os
import shutil
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/tiff", "image/bmp"})
_PDF_TYPE = "application/pdf"
_tesseract_configured = False

_WINDOWS_TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")),
    Path(os.path.expandvars(r"%ProgramFiles%\Tesseract-OCR\tesseract.exe")),
    Path(os.path.expandvars(r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe")),
)


def _resolve_tesseract_cmd() -> str | None:
    from app.config import settings

    if settings.tesseract_cmd:
        path = Path(settings.tesseract_cmd)
        if path.is_file():
            return str(path.resolve())
        logger.warning("TESSERACT_CMD set but file not found: %s", settings.tesseract_cmd)

    env_cmd = os.environ.get("TESSERACT_CMD", "").strip()
    if env_cmd and Path(env_cmd).is_file():
        return str(Path(env_cmd).resolve())

    which = shutil.which("tesseract")
    if which:
        return which

    for candidate in _WINDOWS_TESSERACT_CANDIDATES:
        if candidate.is_file():
            return str(candidate.resolve())

    return None


def _configure_tesseract() -> None:
    global _tesseract_configured
    if _tesseract_configured:
        return
    _tesseract_configured = True

    cmd = _resolve_tesseract_cmd()
    if not cmd:
        return

    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = cmd
        logger.info("Using Tesseract at %s", cmd)
    except ImportError:
        pass


def _prepare_image_for_ocr(img: Image.Image) -> Image.Image:
    """Convert to grayscale for OCR.

    Note: a contrast-enhancement step (ImageEnhance.Contrast, ~1.6x) used to run here.
    It was removed because it degrades already-clean, high-contrast source images
    (e.g. screenshots, rendered PDF pages, exported invoices) — pushing antialiased
    text edges to the point where Tesseract's character segmentation breaks down and
    produces garbled output, even though the same image OCRs cleanly without it.
    Grayscale conversion alone is safe and can still help on some scanned documents.
    If contrast enhancement is reintroduced for genuinely low-contrast scans, make it
    conditional (e.g. based on measured image contrast) rather than applied unconditionally.
    """
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    gray = ImageOps.grayscale(img)
    return gray


def _ocr_image(img: Image.Image) -> str:
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "pytesseract is not installed. pip install pytesseract pillow and install Tesseract OCR."
        ) from exc

    _configure_tesseract()
    prepared = _prepare_image_for_ocr(img)
    config = "--psm 6 --oem 3"

    try:
        return pytesseract.image_to_string(prepared, config=config) or ""
    except pytesseract.TesseractNotFoundError as exc:
        cmd = _resolve_tesseract_cmd()
        hint = (
            f" Set TESSERACT_CMD in backend/.env (detected: {cmd or 'none'})."
            if cmd
            else " Install from https://github.com/tesseract-ocr/tesseract or set TESSERACT_CMD in backend/.env."
        )
        raise RuntimeError("Tesseract OCR binary not found." + hint) from exc


def _pdf_text_pdfplumber(data: bytes) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:10]:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
            tables = page.extract_tables() or []
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    cells = [str(c).strip() for c in row if c]
                    if cells:
                        parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _pdf_ocr_fallback(data: bytes) -> str:
    try:
        import fitz  # pymupdf
    except ImportError:
        return ""

    parts: list[str] = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        for page in doc[:5]:
            pix = page.get_pixmap(dpi=250)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            parts.append(_ocr_image(img))
        doc.close()
    except Exception as exc:
        logger.debug("PDF OCR fallback failed: %s", exc)
    return "\n".join(parts).strip()


def extract_text_from_pdf(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    text = ""
    try:
        text = _pdf_text_pdfplumber(data)
    except Exception as exc:
        warnings.append(f"pdfplumber extraction failed: {exc!s}")

    if len(text) < 40:
        warnings.append("Little or no embedded text — trying OCR on PDF pages.")
        ocr_text = _pdf_ocr_fallback(data)
        if ocr_text:
            text = ocr_text
        elif not text:
            warnings.append("Could not read PDF text. Upload a text-based PDF or a clear image.")

    return text, warnings


def extract_text_from_image(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    img = Image.open(io.BytesIO(data))
    text = _ocr_image(img)
    if len(text.strip()) < 10:
        warnings.append("OCR returned very little text — use a clearer, higher-resolution image.")
    return text.strip(), warnings


def extract_invoice_text(*, data: bytes, content_type: str | None, filename: str) -> tuple[str, str, list[str]]:
    """Return (source_type, text, warnings)."""
    ct = (content_type or "").split(";")[0].strip().lower()
    name = (filename or "").lower()

    if ct == _PDF_TYPE or name.endswith(".pdf"):
        text, warnings = extract_text_from_pdf(data)
        return "pdf", text, warnings

    if ct in _IMAGE_TYPES or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")):
        text, warnings = extract_text_from_image(data)
        return "image", text, warnings

    raise ValueError(f"Unsupported file type: {content_type or filename}. Upload PDF or PNG/JPEG/WebP.")