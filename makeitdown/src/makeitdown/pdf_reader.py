"""Optional per-page PDF reader. A missing inspector or a full scan returns None."""

import tempfile
from pathlib import Path

from .models import ConversionResult, ConversionUnavailable, OCRUnavailableError
from .pages import join_pages

_OCR_NOTICE = "pdf page {n} needs OCR; local engine unavailable and cloud consent is off"


def _extract_pages(path: Path):
    """Per-page markdown from pdf-inspector. Import and extract errors propagate."""
    import pdf_inspector

    extracted = pdf_inspector.extract_pages_markdown(str(path))
    return extracted.pages


def _render_page(pdf, page_index, dest) -> None:
    """Rasterize one 0-based PDF page to ``dest`` for the existing OCR dispatcher."""
    import fitz

    with fitz.open(pdf) as doc:
        page = doc.load_page(page_index)
        pixmap = page.get_pixmap(dpi=150)
        pixmap.save(str(dest))


def _has_trusted_text(page) -> bool:
    return (not page.needs_ocr) and bool((page.markdown or "").strip())


def read_pdf(path: Path, *, dispatcher, cloud_consent: bool) -> ConversionResult | None:
    """Read one PDF page at a time, or None to keep today's whole-file path.

    None means pdf-inspector is missing, extraction raised, or every page needs
    OCR (or its markdown is empty after stripping). ``cloud_consent`` is part of
    the call contract only: this function does not upload, and the dispatcher
    already applies that policy.
    """
    path = Path(path)
    try:
        pages = list(_extract_pages(path))
    except Exception:
        return None
    if not pages or not any(_has_trusted_text(page) for page in pages):
        return None

    parts: list[str] = []
    notices: list[str] = []
    first_ocr_engine: str | None = None
    other_engines: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="makeitdown-pdf-") as tmp:
        for offset, page in enumerate(pages):
            # Markers and notices follow this list, not pages_needing_ocr.
            n = offset + 1
            if not page.needs_ocr:
                parts.append((page.markdown or "").strip())
                continue
            dest = Path(tmp) / f"page-{n}.png"
            _render_page(path, page.page, dest)
            try:
                ocr_result = dispatcher.convert(dest)
            except (OCRUnavailableError, ConversionUnavailable):
                parts.append("")
                notices.append(_OCR_NOTICE.format(n=n))
                continue
            parts.append(ocr_result.text or "")
            label = ocr_result.engine
            if first_ocr_engine is None:
                first_ocr_engine = label
            elif label != first_ocr_engine and label not in other_engines:
                other_engines.add(label)
                notices.append(f"pdf page {n} OCR engine {label}")

    if first_ocr_engine is None:
        engine = "pdf-inspector"
    else:
        engine = "pdf-inspector+" + first_ocr_engine
    return ConversionResult(
        text=join_pages(parts),
        engine=engine,
        pages=len(parts),
        page_map="native",
        notices=notices or None,
    )
