from makeitdown.models import ConversionResult, ConversionUnavailable, LegacyConversionUnavailable, OCRUnavailableError


def test_legacy_unavailable_is_conversion_unavailable():
    assert issubclass(LegacyConversionUnavailable, ConversionUnavailable)
    assert issubclass(ConversionUnavailable, RuntimeError)


def test_conversion_result_defaults():
    r = ConversionResult(text="# Hi", engine="markitdown")
    assert r.text == "# Hi"
    assert r.engine == "markitdown"
    assert r.pages is None
    assert r.assets == {}


def test_conversion_result_defaults_anydoc():
    r = ConversionResult(text="x", engine="anydoc")
    assert r.pages is None and r.assets == {} and r.confidences is None


def test_conversion_result_assets_are_independent():
    a = ConversionResult(text="a", engine="x")
    b = ConversionResult(text="b", engine="y")
    a.assets["img/1.png"] = b"\x89PNG"
    assert b.assets == {}  # no shared mutable default


def test_ocr_unavailable_is_exception():
    assert issubclass(OCRUnavailableError, Exception)
