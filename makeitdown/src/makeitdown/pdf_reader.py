"""Optional per-page PDF reader.

No trusted page returns None so the caller keeps today's whole-file path.
A missing inspector or an extract error raises PdfInspectorUnavailable.
"""

import tempfile
from pathlib import Path

from .cloud_consent import CloudConsentRequired
from .models import ConversionResult, ConversionUnavailable, OCRUnavailableError
from .pages import join_pages

_OCR_NOTICE_NO_CONSENT = (
    "pdf page {n} needs OCR; local engine unavailable and cloud consent is off"
)
_OCR_NOTICE_ENGINE = "pdf page {n} needs OCR; OCR engine unavailable"
INSPECTOR_FALLBACK_WARNING = "pdf-inspector unavailable; used the default PDF path"


class PdfInspectorUnavailable(RuntimeError):
    """pdf-inspector could not be imported, or extract_pages_markdown failed."""


def _extract_pages(path: Path):
    """Per-page markdown from pdf-inspector.

    ImportError and extract_pages_markdown errors raise PdfInspectorUnavailable.
    Render and OCR are not done here, so their crashes stay outside this catch.
    """
    try:
        import pdf_inspector

        extracted = pdf_inspector.extract_pages_markdown(str(path))
        return list(extracted.pages)
    except Exception as exc:
        raise PdfInspectorUnavailable("pdf-inspector unavailable") from exc


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

    None means there is no trusted page: every page needs OCR, or its markdown
    is empty after stripping. That case is a silent fallback. A missing
    pdf-inspector or an extract_pages_markdown error raises
    PdfInspectorUnavailable instead of returning None. This function does not
    upload. An unavailable OCR page says consent is off only when
    ``cloud_consent`` is false.
    """
    path = Path(path)
    pages = list(_extract_pages(path))
    if not pages or not any(_has_trusted_text(page) for page in pages):
        return None

    parts: list[str] = []
    notices: list[str] = []
    confidences: list[float] = []
    cross_check_reasons: list[str] = []
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
            except (OCRUnavailableError, ConversionUnavailable, CloudConsentRequired):
                parts.append("")
                template = _OCR_NOTICE_NO_CONSENT if not cloud_consent else _OCR_NOTICE_ENGINE
                notices.append(template.format(n=n))
                continue
            parts.append(ocr_result.text or "")
            if ocr_result.confidences:
                confidences.extend(ocr_result.confidences)
            if ocr_result.cross_check_reasons:
                cross_check_reasons.extend(ocr_result.cross_check_reasons)
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
        confidences=confidences or None,
        cross_check_reasons=cross_check_reasons or None,
    )
