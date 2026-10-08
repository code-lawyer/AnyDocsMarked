import builtins
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import makeitdown.pdf_reader as pdf_reader
import makeitdown.pipeline as pl
from makeitdown.cloud_consent import CloudConsentRequired
from makeitdown.models import ConversionResult, ConversionUnavailable, OCRUnavailableError
from makeitdown.pdf_reader import PdfInspectorUnavailable, read_pdf


def _page(index, markdown, needs_ocr):
    return SimpleNamespace(page=index, markdown=markdown, needs_ocr=needs_ocr)


def _patch_extract(monkeypatch, pages):
    monkeypatch.setattr(pdf_reader, "_extract_pages", lambda path: pages)


def _patch_render(monkeypatch, rendered=None):
    def render(pdf, page_index, dest):
        if rendered is not None:
            rendered.append(page_index)
        Path(dest).write_bytes(b"")

    monkeypatch.setattr(pdf_reader, "_render_page", render)


def _fake_inspector(monkeypatch, *, pages=None, error=None):
    fake = types.ModuleType("pdf_inspector")

    def extract_pages_markdown(path):
        if error is not None:
            raise error
        return SimpleNamespace(pages=pages)

    fake.extract_pages_markdown = extract_pages_markdown
    monkeypatch.setitem(sys.modules, "pdf_inspector", fake)


def test_extract_runtime_error_raises_unavailable(monkeypatch, tmp_path):
    _fake_inspector(monkeypatch, error=RuntimeError("boom"))
    with pytest.raises(PdfInspectorUnavailable):
        read_pdf(tmp_path / "a.pdf", dispatcher=object(), cloud_consent=False)


def test_missing_inspector_import_raises_unavailable(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "pdf_inspector", raising=False)
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pdf_inspector":
            raise ImportError("no pdf_inspector")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(PdfInspectorUnavailable) as exc:
        read_pdf(tmp_path / "a.pdf", dispatcher=object(), cloud_consent=False)
    assert isinstance(exc.value.__cause__, ImportError)


def test_all_pages_need_ocr_returns_none_without_dispatcher(monkeypatch, tmp_path):
    _patch_extract(monkeypatch, [_page(0, "", True), _page(1, "", True)])
    calls = []

    class Disp:
        def convert(self, path):
            calls.append(path)
            return ConversionResult(text="should-not-run", engine="local:pp-structurev3")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is None
    assert calls == []


def test_mixed_page_keeps_amount_and_uses_ocr_for_the_blank_page(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "", True),
        ],
    )
    _patch_render(monkeypatch)
    seen = []

    class Disp:
        def convert(self, path):
            image = Path(path)
            assert image.suffix == ".png"
            assert image.is_file()
            seen.append(image)
            return ConversionResult(
                text="扫描正文",
                engine="local:pp-structurev3",
                pages=1,
                page_map="native",
            )

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert len(seen) == 1
    assert result.text.index("<!-- page: 1 -->") < result.text.index("50,000.00")
    assert result.text.index("<!-- page: 2 -->") < result.text.index("扫描正文")
    assert result.text == (
        "<!-- page: 1 -->\nArticle 3 50,000.00\n<!-- page: 2 -->\n扫描正文"
    )
    assert result.engine == "pdf-inspector+local:pp-structurev3"
    assert result.page_map == "native"
    assert result.pages == 2


def test_ocr_unavailable_keeps_marker_and_puts_reason_in_notices(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "", True),
        ],
    )
    _patch_render(monkeypatch)

    class Disp:
        def convert(self, path):
            raise OCRUnavailableError("no local engine")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert "<!-- page: 2 -->" in result.text
    after = result.text.split("<!-- page: 2 -->", 1)[1]
    assert "需要 OCR" not in after
    assert "needs OCR" not in after
    assert "pdf page 2 needs OCR" not in result.text
    assert result.notices is not None
    assert any("2" in notice for notice in result.notices)
    assert result.notices == [
        "pdf page 2 needs OCR; local engine unavailable and cloud consent is off"
    ]


def test_ocr_unavailable_with_consent_does_not_claim_consent_is_off(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "", True),
        ],
    )
    _patch_render(monkeypatch)

    class Disp:
        def convert(self, path):
            raise OCRUnavailableError("no engine")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=True)
    assert result is not None
    assert result.notices is not None
    joined = "\n".join(result.notices)
    assert "2" in joined
    assert "consent is off" not in joined
    assert result.notices == ["pdf page 2 needs OCR; OCR engine unavailable"]
    assert "consent is off" not in result.text


def test_cloud_consent_required_blanks_page_and_keeps_amount(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "", True),
        ],
    )
    _patch_render(monkeypatch)

    class Disp:
        def convert(self, path):
            raise CloudConsentRequired("withheld")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert result.text == (
        "<!-- page: 1 -->\nArticle 3 50,000.00\n<!-- page: 2 -->\n"
    )
    assert "50,000.00" in result.text
    assert "consent is off" not in result.text
    assert "needs OCR" not in result.text
    assert result.notices == [
        "pdf page 2 needs OCR; local engine unavailable and cloud consent is off"
    ]


def test_cloud_consent_required_with_consent_does_not_claim_consent_is_off(
    monkeypatch, tmp_path,
):
    _patch_extract(monkeypatch, [_page(0, "kept", False), _page(1, "", True)])
    _patch_render(monkeypatch)

    class Disp:
        def convert(self, path):
            raise CloudConsentRequired("withheld")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=True)
    assert result is not None
    assert result.text == "<!-- page: 1 -->\nkept\n<!-- page: 2 -->\n"
    assert "consent is off" not in result.text
    assert result.notices == ["pdf page 2 needs OCR; OCR engine unavailable"]
    assert "consent is off" not in "\n".join(result.notices)


def test_unrelated_ocr_error_still_propagates(monkeypatch, tmp_path):
    _patch_extract(monkeypatch, [_page(0, "kept", False), _page(1, "", True)])
    _patch_render(monkeypatch)

    class Disp:
        def convert(self, path):
            raise RuntimeError("ocr crash")

    with pytest.raises(RuntimeError, match="ocr crash"):
        read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)


def test_conversion_unavailable_uses_the_same_blank_page_notice(monkeypatch, tmp_path):
    _patch_extract(monkeypatch, [_page(0, "kept", False), _page(1, "", True)])
    _patch_render(monkeypatch)

    class Disp:
        def convert(self, path):
            raise ConversionUnavailable("no converter")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=True)
    assert result is not None
    assert result.text == "<!-- page: 1 -->\nkept\n<!-- page: 2 -->\n"
    assert "需要 OCR" not in result.text
    assert result.notices == ["pdf page 2 needs OCR; OCR engine unavailable"]
    assert "consent is off" not in "\n".join(result.notices)


def test_all_trusted_pages_use_pdf_inspector_and_skip_dispatcher(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "signed", False),
        ],
    )
    calls = []

    class Disp:
        def convert(self, path):
            calls.append(path)
            return ConversionResult(text="nope", engine="local:pp-structurev3")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert result.engine == "pdf-inspector"
    assert calls == []
    assert result.page_map == "native"
    assert result.text == (
        "<!-- page: 1 -->\nArticle 3 50,000.00\n<!-- page: 2 -->\nsigned"
    )


def test_blank_markdown_returns_none_without_dispatcher(monkeypatch, tmp_path):
    _patch_extract(monkeypatch, [_page(0, "  \n", False), _page(1, "", False)])
    calls = []

    class Disp:
        def convert(self, path):
            calls.append(path)
            return ConversionResult(text="nope", engine="local:pp-structurev3")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is None
    assert calls == []


def test_marker_and_notice_use_return_order_not_page_field(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(4, "Article 3 50,000.00", False),
            _page(7, "garbled", True),
        ],
    )
    rendered = []
    _patch_render(monkeypatch, rendered)

    class Disp:
        def convert(self, path):
            raise OCRUnavailableError("no local engine")

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert rendered == [7]
    assert "<!-- page: 1 -->" in result.text
    assert "<!-- page: 2 -->" in result.text
    assert "<!-- page: 5 -->" not in result.text
    assert "<!-- page: 8 -->" not in result.text
    assert "garbled" not in result.text
    assert result.notices == [
        "pdf page 2 needs OCR; local engine unavailable and cloud consent is off"
    ]


def test_later_ocr_engine_label_is_noted_but_not_used(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "", True),
            _page(2, "", True),
        ],
    )
    _patch_render(monkeypatch)
    engines = iter(["local:pp-structurev3", "cloud:paddleocr-vl-1.6"])

    class Disp:
        def convert(self, path):
            return ConversionResult(text="扫描正文", engine=next(engines), pages=1)

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert result.engine == "pdf-inspector+local:pp-structurev3"
    assert result.notices == ["pdf page 3 OCR engine cloud:paddleocr-vl-1.6"]
    assert "cloud:paddleocr-vl-1.6" not in result.text
    assert result.text.index("<!-- page: 3 -->") < result.text.rindex("扫描正文")


def test_ocr_scores_and_cross_check_reasons_follow_page_order(monkeypatch, tmp_path):
    _patch_extract(
        monkeypatch,
        [
            _page(0, "Article 3 50,000.00", False),
            _page(1, "", True),
            _page(2, "", True),
            _page(3, "", True),
        ],
    )
    _patch_render(monkeypatch)
    results = iter([
        ConversionResult(
            text="扫描一",
            engine="local:pp-structurev3",
            pages=1,
            confidences=[0.99, 0.4],
            cross_check_reasons=["digit mismatch"],
            assets={"imgs/a.png": b"PNGBYTES"},
        ),
        ConversionResult(
            text="扫描二",
            engine="local:pp-structurev3",
            pages=1,
            confidences=None,
            cross_check_reasons=[],
        ),
        ConversionResult(
            text="扫描三",
            engine="local:pp-structurev3",
            pages=1,
            confidences=[0.2],
            cross_check_reasons=["digit mismatch"],
        ),
    ])

    class Disp:
        def convert(self, path):
            return next(results)

    result = read_pdf(tmp_path / "a.pdf", dispatcher=Disp(), cloud_consent=False)
    assert result is not None
    assert result.confidences == [0.99, 0.4, 0.2]
    assert result.cross_check_reasons == ["digit mismatch", "digit mismatch"]
    assert result.assets == {}
    assert "0.99" not in result.text
    assert "0.4" not in result.text
    assert "digit mismatch" not in result.text
    assert result.notices is None or all(
        "digit mismatch" not in notice and "0.99" not in notice
        for notice in result.notices
    )


def test_render_page_writes_png(tmp_path):
    import fitz

    pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    dest = tmp_path / "page.png"
    pdf_reader._render_page(pdf, 0, dest)
    assert dest.is_file()
    assert dest.stat().st_size > 8


def test_pipeline_includes_notices_in_warnings(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    notice = "pdf page 2 needs OCR; local engine unavailable and cloud consent is off"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(
        pl,
        "convert_native",
        lambda p: ConversionResult(
            text="正常的文档内容" * 5,
            engine="markitdown",
            notices=[notice],
        ),
    )
    report = pl.convert_tree(
        src,
        out,
        ocr_engine="local",
        ocr_model=None,
        cloud_token=None,
        workers=1,
        skip_existing=False,
        text_threshold=50,
        report_path=out / "report.json",
        progress=False,
    )
    assert report["warned"] == 1
    assert report["warnings"][0]["reasons"][0] == notice
    md = (out / "a.md").read_text(encoding="utf-8")
    assert notice in md
    assert "正常的文档内容" in md.split("---", 2)[-1]
