import pytest

import makeitdown.router as router


@pytest.mark.parametrize("name,expected", [
    ("a.ppt", "anydoc"), ("a.xls", "anydoc"), ("a.xlsb", "anydoc"),
    ("a.doc", "legacy"), ("a.wps", "legacy"),
    ("a.PPT", "anydoc"), ("a.Xls", "anydoc"),  # 扩展名大小写不敏感
])
def test_anydoc_and_legacy_routing(tmp_path, name, expected):
    p = tmp_path / name
    p.write_bytes(b"")  # 非 PDF 路由仅看扩展名,内容无关
    assert router.classify(p) == expected


def test_native_extensions(tmp_path):
    for name in ["a.docx", "b.xlsx", "c.pptx", "d.html", "e.csv", "f.json", "g.txt", "h.epub"]:
        p = tmp_path / name
        p.write_text("x", encoding="utf-8")
        assert router.classify(p) == "native"


def test_image_extensions(tmp_path):
    for name in ["a.png", "b.jpg", "c.jpeg", "d.bmp", "e.tiff"]:
        p = tmp_path / name
        p.write_bytes(b"\x00")
        assert router.classify(p) == "ocr"


def test_unsupported_extension(tmp_path):
    p = tmp_path / "a.zip.unknownext"
    p.write_bytes(b"\x00")
    assert router.classify(p) == "unsupported"


def test_legacy_binary_extensions(tmp_path):
    for name in ["a.doc", "b.wps"]:
        p = tmp_path / name
        p.write_bytes(b"\x00")
        assert router.classify(p) == "legacy"


def test_pdf_with_text_layer_is_native(tmp_path, monkeypatch):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(router, "_pdf_avg_chars_per_page", lambda path: 500.0)
    assert router.classify(p, text_threshold=50) == "native"


def test_pdf_without_text_layer_is_ocr(tmp_path, monkeypatch):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(router, "_pdf_avg_chars_per_page", lambda path: 3.0)
    assert router.classify(p, text_threshold=50) == "ocr"


OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP = b"PK\x03\x04"


def test_docx_with_ole_header_is_legacy(tmp_path):
    path = tmp_path / "contract.docx"
    path.write_bytes(OLE2 + b"not a zip")
    assert router.classify(path) == "legacy"


def test_docx_with_zip_header_stays_native(tmp_path):
    path = tmp_path / "contract.docx"
    path.write_bytes(ZIP + b"not a real docx")
    assert router.classify(path) == "native"
