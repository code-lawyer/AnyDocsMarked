import importlib.metadata

import fitz
import pytest

from makeitdown.pdf_reader import read_pdf


def _require_pdf_inspector_wheel():
    """Skip only when the distribution is absent. A broken installed wheel fails."""
    try:
        importlib.metadata.distribution("pdf-inspector")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("pdf-inspector is not installed")
    import pdf_inspector

    assert hasattr(pdf_inspector, "extract_pages_markdown")


def _two_page_pdf(path):
    doc = fitz.open()
    for text in ("Article 3 Amount 50,000.00", "Schedule B dated 2024-03-15"):
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()


class _ForbiddenDispatcher:
    def convert(self, path):
        raise AssertionError(f"dispatcher must not be called: {path}")


def test_real_wheel_keeps_amount_date_and_page_markers(tmp_path):
    _require_pdf_inspector_wheel()
    path = tmp_path / "two-page.pdf"
    _two_page_pdf(path)
    result = read_pdf(path, dispatcher=_ForbiddenDispatcher(), cloud_consent=False)
    assert result is not None
    assert "50,000.00" in result.text
    assert "2024-03-15" in result.text
    assert result.text.index("<!-- page: 1 -->") < result.text.index("50,000.00")
    assert result.text.index("<!-- page: 2 -->") < result.text.index("2024-03-15")
    assert result.page_map == "native"
    assert result.engine == "pdf-inspector"
    assert "<!-- Page" not in result.text
