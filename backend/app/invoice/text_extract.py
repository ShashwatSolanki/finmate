"""Extract plain text from invoice PDFs and images."""

from __future__ import annotations

import io
import logging
import math
import os
import shutil
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/tiff", "image/bmp"})
_PDF_TYPE = "application/pdf"
_MAX_IMAGE_PIXELS = 25_000_000
_MAX_PDF_PAGES = 100
_MAX_PDF_RENDER_PIXELS = 25_000_000
_PDF_OCR_DPI = 250
_OCR_TIMEOUT_SECONDS = 20
_SUPPORTED_IMAGE_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "TIFF", "BMP"})
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
        return pytesseract.image_to_string(
            prepared,
            config=config,
            timeout=_OCR_TIMEOUT_SECONDS,
        ) or ""
    except pytesseract.TesseractNotFoundError as exc:
        cmd = _resolve_tesseract_cmd()
        hint = (
            f" Set TESSERACT_CMD in backend/.env (detected: {cmd or 'none'})."
            if cmd
            else " Install from https://github.com/tesseract-ocr/tesseract or set TESSERACT_CMD in backend/.env."
        )
        raise RuntimeError("Tesseract OCR binary not found." + hint) from exc
    except RuntimeError as exc:
        if "timeout" in str(exc).lower():
            raise ValueError(
                f"OCR exceeded the {_OCR_TIMEOUT_SECONDS}-second processing limit."
            ) from exc
        raise


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


def _pdf_text_pypdf(data: bytes) -> str:
    """A lightweight second extractor for PDFs pdfplumber cannot open cleanly."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        # Some invoice portals encrypt with an empty password. Try it before reporting
        # a useful, actionable error instead of returning an empty OCR result.
        if reader.decrypt("") == 0:
            raise ValueError("This PDF is password protected. Export an unlocked copy and try again.")
    return "\n".join((page.extract_text() or "") for page in reader.pages[:10]).strip()


def _pdf_page_count(data: bytes) -> int | None:
    """Return a PDF's page count when PyMuPDF can inspect it safely."""
    try:
        import fitz  # pymupdf
    except ImportError:
        return None

    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        return int(doc.page_count)
    except Exception:
        # Let pdfplumber / pypdf report a more useful parse error for unsupported PDFs.
        return None
    finally:
        if doc is not None:
            doc.close()


def _pdf_ocr_fallback(data: bytes) -> str:
    try:
        import fitz  # pymupdf
    except ImportError:
        return ""

    parts: list[str] = []
    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.page_count > _MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds the supported limit of {_MAX_PDF_PAGES} pages.")

        # Index pages directly instead of materializing every page object in a PDF.
        for page_index in range(min(5, doc.page_count)):
            page = doc.load_page(page_index)
            rect = page.rect
            width_px = math.ceil(rect.width * _PDF_OCR_DPI / 72)
            height_px = math.ceil(rect.height * _PDF_OCR_DPI / 72)
            if (
                width_px <= 0
                or height_px <= 0
                or width_px * height_px > _MAX_PDF_RENDER_PIXELS
            ):
                raise ValueError(
                    "PDF page dimensions exceed the supported OCR rendering limit "
                    f"of {_MAX_PDF_RENDER_PIXELS:,} pixels."
                )

            pix = page.get_pixmap(dpi=_PDF_OCR_DPI, alpha=False)
            # Verify actual dimensions too, in case a PDF's crop / rotation changes the estimate.
            if pix.width <= 0 or pix.height <= 0 or pix.width * pix.height > _MAX_PDF_RENDER_PIXELS:
                raise ValueError(
                    "PDF page dimensions exceed the supported OCR rendering limit "
                    f"of {_MAX_PDF_RENDER_PIXELS:,} pixels."
                )
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            parts.append(_ocr_image(img))
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        logger.debug("PDF OCR fallback failed: %s", exc)
    finally:
        if doc is not None:
            doc.close()
    return "\n".join(parts).strip()


def extract_text_from_pdf(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not data.startswith(b"%PDF-"):
        raise ValueError("The uploaded file is not a valid PDF.")

    page_count = _pdf_page_count(data)
    if page_count is not None and page_count > _MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds the supported limit of {_MAX_PDF_PAGES} pages.")

    text = ""
    try:
        text = _pdf_text_pdfplumber(data)
    except Exception as exc:
        warnings.append(f"pdfplumber extraction failed: {exc!s}")

    if len(text) < 40:
        try:
            pypdf_text = _pdf_text_pypdf(data)
            if len(pypdf_text) > len(text):
                text = pypdf_text
                warnings.append("Used compatibility PDF text extraction.")
        except ValueError:
            raise
        except Exception as exc:
            warnings.append(f"compatibility PDF extraction failed: {exc!s}")

    if len(text) < 40:
        warnings.append("Little or no embedded text — trying OCR on PDF pages.")
        try:
            ocr_text = _pdf_ocr_fallback(data)
        except RuntimeError:
            # OCR installation errors must reach the API rather than becoming a
            # misleading "no text" result.
            raise
        if ocr_text:
            text = ocr_text
        elif not text:
            warnings.append("Could not read PDF text. Upload a text-based PDF or a clear image.")

    return text, warnings


def extract_text_from_image(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format or "").upper() not in _SUPPORTED_IMAGE_FORMATS:
                raise ValueError("Unsupported image format. Upload PNG, JPEG, WebP, TIFF, or BMP.")
            if image.width <= 0 or image.height <= 0 or image.width * image.height > _MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions exceed the supported limit of 25 megapixels.")
            image.load()
            img = image.copy()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Image dimensions exceed the supported processing limit.") from exc
    except (OSError, SyntaxError) as exc:
        raise ValueError("The uploaded file is not a valid supported image.") from exc

    text = _ocr_image(img)
    if len(text.strip()) < 10:
        warnings.append("OCR returned very little text — use a clearer, higher-resolution image.")
    return text.strip(), warnings


def extract_invoice_text(*, data: bytes, content_type: str | None, filename: str) -> tuple[str, str, list[str]]:
    """Detect file type from its content; MIME type and filename are untrusted hints."""
    if data.startswith(b"%PDF-"):
        text, warnings = extract_text_from_pdf(data)
        return "pdf", text, warnings

    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            if image_format not in _SUPPORTED_IMAGE_FORMATS:
                raise ValueError("Unsupported file type. Upload a PDF, PNG, JPEG, WebP, TIFF, or BMP.")
            if image.width <= 0 or image.height <= 0 or image.width * image.height > _MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions exceed the supported limit of 25 megapixels.")
            image.verify()
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Image dimensions exceed the supported processing limit.") from exc
    except (OSError, SyntaxError) as exc:
        raise ValueError("Unsupported or invalid file content. Upload a PDF or a supported image.") from exc

    text, warnings = extract_text_from_image(data)
    return "image", text, warnings
