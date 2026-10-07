import fitz
import pytest

from makeitdown.pdf_reader import read_pdf

pytest.importorskip("pdf_inspector")


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
