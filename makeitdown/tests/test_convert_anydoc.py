import anydoc
import pytest

from makeitdown import convert_anydoc
from makeitdown.models import ConversionResult, ConversionUnavailable


def test_success_returns_anydoc_result(monkeypatch):
    monkeypatch.setattr(anydoc, "to_markdown", lambda p: "# 判决书\n金额 1,234,567.89")
    r = convert_anydoc.convert("case.xls")
    assert isinstance(r, ConversionResult)
    assert r.engine == "anydoc"
    assert r.pages is None
    assert "1,234,567.89" in r.text


@pytest.mark.parametrize("exc_name", [
    "UnsupportedError", "MalformedError", "MissingPartError",
    "ResourceLimitError", "ConvertError",
])
def test_typed_errors_become_unavailable(monkeypatch, exc_name):
    exc = getattr(anydoc, exc_name)

    def boom(p):
        raise exc("boom")

    monkeypatch.setattr(anydoc, "to_markdown", boom)
    with pytest.raises(ConversionUnavailable):
        convert_anydoc.convert("case.ppt")


def test_encrypted_gives_password_hint(monkeypatch):
    def boom(p):
        raise anydoc.EncryptedError("enc")

    monkeypatch.setattr(anydoc, "to_markdown", boom)
    with pytest.raises(ConversionUnavailable, match="加密"):
        convert_anydoc.convert("case.doc")
