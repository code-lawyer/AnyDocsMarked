import pytest
from pathlib import Path
import makeitdown.convert_ocr as co
from makeitdown import convert_ocr
from makeitdown.cloud_consent import CloudConsentRequired
from makeitdown.models import ConversionResult, OCRUnavailableError


class _FakeBackend:
    def __init__(self, label):
        self._label = label

    def convert(self, path):
        return ConversionResult(text=f"md from {self._label}", engine=self._label)


def test_auto_prefers_local_when_available(monkeypatch):
    monkeypatch.setattr(co.LocalOCR, "is_available", staticmethod(lambda: True))
    monkeypatch.setattr(co, "LocalOCR", lambda **k: _FakeBackend("local:pp-structurev3"))
    d = co.OCRDispatcher(engine="auto", token=None)
    r = d.convert(Path("x.png"))
    assert r.engine == "local:pp-structurev3"


def test_auto_falls_back_to_cloud_when_local_missing(monkeypatch):
    monkeypatch.setattr(co.LocalOCR, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(co, "CloudOCR", lambda **k: _FakeBackend("cloud:paddleocr-vl-1.6"))
    # cloud_consent=True required: auto-to-cloud fallback now gates on consent.
    d = co.OCRDispatcher(engine="auto", token="TKN", cloud_consent=True)
    r = d.convert(Path("x.png"))
    assert r.engine == "cloud:paddleocr-vl-1.6"


def test_auto_raises_clear_error_when_neither(monkeypatch):
    monkeypatch.setattr(co.LocalOCR, "is_available", staticmethod(lambda: False))
    d = co.OCRDispatcher(engine="auto", token=None)
    try:
        d.convert(Path("x.png"))
        assert False, "expected OCRUnavailableError"
    except OCRUnavailableError as e:
        msg = str(e)
        assert "makeitdown[local]" in msg
        assert "PADDLEOCR_AISTUDIO_TOKEN" in msg


def test_explicit_cloud_without_token_raises(monkeypatch):
    # cloud_consent=True so the consent gate passes; the token check then fires.
    d = co.OCRDispatcher(engine="cloud", token=None, cloud_consent=True)
    try:
        d.convert(Path("x.png"))
        assert False, "expected OCRUnavailableError"
    except OCRUnavailableError as e:
        assert "PADDLEOCR_AISTUDIO_TOKEN" in str(e)


# ---------------------------------------------------------------------------
# Cross-check orchestration tests (Task 5)
# ---------------------------------------------------------------------------

class _FakePrimary:
    def convert(self, path):
        return ConversionResult(text="金额为500000元", engine="local:pp-structurev3", pages=1)


def _dispatcher_with(monkeypatch, primary, verifier_text):
    d = co.OCRDispatcher(engine="local", cross_check=True, cross_check_ratio=0.1)
    monkeypatch.setattr(d, "_resolve_backend", lambda: primary)
    # verifier returns a ConversionResult with the given text
    class _V:
        @staticmethod
        def is_available():
            return True
        def convert(self, path):
            return ConversionResult(text=verifier_text, engine="mineru", pages=1)
    monkeypatch.setattr(d, "_make_verifier", lambda: _V())
    return d


def test_crosscheck_flags_digit_mismatch(monkeypatch, tmp_path):
    d = _dispatcher_with(monkeypatch, _FakePrimary(), verifier_text="金额为800000元")
    result = d.convert(tmp_path / "x.pdf")
    assert result.text == "金额为500000元"          # primary content untouched
    assert result.cross_check_reasons              # disagreement flagged
    assert "mineru" in result.engine


def test_crosscheck_clean_when_engines_agree(monkeypatch, tmp_path):
    d = _dispatcher_with(monkeypatch, _FakePrimary(), verifier_text="金额为500000元")
    result = d.convert(tmp_path / "x.pdf")
    assert not result.cross_check_reasons
    assert "mineru" in result.engine


def test_crosscheck_degrades_when_verifier_unavailable(monkeypatch, tmp_path):
    d = co.OCRDispatcher(engine="local", cross_check=True)
    monkeypatch.setattr(d, "_resolve_backend", lambda: _FakePrimary())
    monkeypatch.setattr(d, "_make_verifier", lambda: None)  # no verifier
    result = d.convert(tmp_path / "x.pdf")
    assert result.text == "金额为500000元"          # never lose the conversion
    assert result.cross_check_reasons == ["双OCR互校跳过：无可用 MinerU 校验引擎（装本地版 mineru，或设 MINERU_API_TOKEN 并加 --cloud-consent）"]


# ---------------------------------------------------------------------------
# Task 4: cloud-default primary, consent gate, verifier mode
# ---------------------------------------------------------------------------

def test_primary_cloud_default_requires_consent(monkeypatch, tmp_path):
    # engine defaults to cloud; without consent, resolving the backend must refuse.
    d = convert_ocr.OCRDispatcher(token="tok", cloud_consent=False)
    assert d.engine == "cloud"
    with pytest.raises(CloudConsentRequired):
        d.convert(tmp_path / "x.pdf")


def test_primary_local_bypasses_consent(monkeypatch, tmp_path):
    d = convert_ocr.OCRDispatcher(engine="local", cloud_consent=False)
    sentinel = ConversionResult(text="本地结果", engine="local:pp-structurev3", pages=1)

    class _Local:
        @staticmethod
        def is_available(): return True
        def convert(self, p): return sentinel

    monkeypatch.setattr(convert_ocr, "_LocalOCR_cls", _Local)
    monkeypatch.setattr(convert_ocr, "LocalOCR", lambda model=None: _Local())
    assert d.convert(tmp_path / "x.pdf").text == "本地结果"


def test_verifier_mode_local_unavailable_skips(monkeypatch):
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True, cross_check_mode="local")
    monkeypatch.setattr(convert_ocr.MinerULocal, "is_available", staticmethod(lambda: False))
    assert d._make_verifier() is None


def test_verifier_mode_cloud_needs_consent(monkeypatch):
    # cross_check_mode=cloud but no consent → verifier unavailable (skip), never upload.
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True,
                                  cross_check_mode="cloud", cloud_consent=False,
                                  mineru_token="tok")
    assert d._make_verifier() is None


def test_verifier_mode_cloud_builds_with_consent(monkeypatch):
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True,
                                  cross_check_mode="cloud", cloud_consent=True,
                                  mineru_token="tok")
    v = d._make_verifier()
    assert v is not None and v.engine_label == "mineru-cloud"


def test_verifier_cloud_with_token_but_no_consent_never_builds(monkeypatch):
    # local primary + cross-check on + cloud mode + token present, but NO consent:
    # the verifier must resolve to None (clean skip) — never construct MinerUCloud.
    import makeitdown.cloud_consent as cc
    monkeypatch.setattr(cc.os, "environ", {}, raising=False)  # no env consent
    d = convert_ocr.OCRDispatcher(
        engine="local", cross_check=True, cross_check_mode="cloud",
        cloud_consent=False, mineru_token="a-real-token",
    )
    built = {"cloud": False}
    monkeypatch.setattr(convert_ocr, "MinerUCloud",
                        lambda *a, **k: built.__setitem__("cloud", True))
    assert d._make_verifier() is None
    assert built["cloud"] is False  # MinerUCloud was never constructed → no upload


# ---------------------------------------------------------------------------
# Rotation reorientation tests (dead-code ocr_rotate wired into OCR path)
# ---------------------------------------------------------------------------

class _Backend:
    def __init__(self, confs):
        self._confs = confs

    def convert(self, path):
        return ConversionResult(text="upright", engine="local:pp-structurev3",
                                pages=1, confidences=self._confs)


def _rotate_dispatcher(monkeypatch, primary_confs):
    d = co.OCRDispatcher(engine="local")
    monkeypatch.setattr(d, "_resolve_backend", lambda: _Backend(primary_confs))
    return d


def test_low_confidence_scan_reorients_to_best_angle(monkeypatch, tmp_path):
    d = _rotate_dispatcher(monkeypatch, [0.30])
    monkeypatch.setattr(d, "_ocr_at_angle", lambda path, angle: ConversionResult(
        text=f"rot{angle}", engine="local:pp-structurev3",
        confidences=[0.95] if angle == 90 else [0.10]))
    img = tmp_path / "scan.png"
    img.write_bytes(b"stub")
    r = d.convert(img)
    assert r.text == "rot90"


def test_high_confidence_scan_never_probes(monkeypatch, tmp_path):
    d = _rotate_dispatcher(monkeypatch, [0.92])
    calls = []
    monkeypatch.setattr(d, "_ocr_at_angle", lambda path, angle: calls.append(angle))
    img = tmp_path / "scan.png"
    img.write_bytes(b"stub")
    r = d.convert(img)
    assert r.text == "upright"
    assert calls == []


def test_non_image_never_probes(monkeypatch, tmp_path):
    d = _rotate_dispatcher(monkeypatch, [0.10])
    calls = []
    monkeypatch.setattr(d, "_ocr_at_angle", lambda path, angle: calls.append(angle))
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"stub")
    d.convert(pdf)
    assert calls == []  # PDF not rotatable here → no cost even at low confidence


def _real_png(path):
    from PIL import Image
    Image.new("RGB", (4, 2), "white").save(path)


def test_reorient_error_keeps_primary(monkeypatch, tmp_path):
    # 低置信触发重探，但 _ocr_at_angle 抛错（PIL/后端问题）→ 绝不丢已成功的主结果。
    d = _rotate_dispatcher(monkeypatch, [0.10])

    def boom(path, angle):
        raise OSError("cannot rotate this image")

    monkeypatch.setattr(d, "_ocr_at_angle", boom)
    img = tmp_path / "scan.png"
    _real_png(img)
    r = d.convert(img)
    assert r.text == "upright"  # primary preserved, not a hard failure


def test_crosscheck_verifier_sees_rotated_orientation(monkeypatch, tmp_path):
    # reorient 选了 90° → 校验器必须 OCR 同样旋正的页，而非原始朝向（否则两引擎比的是
    # 不同朝向、误报巨大分歧）。
    d = co.OCRDispatcher(engine="local", cross_check=True, cross_check_ratio=0.1)
    monkeypatch.setattr(d, "_resolve_backend", lambda: _Backend([0.30]))
    monkeypatch.setattr(d, "_ocr_at_angle", lambda path, angle: ConversionResult(
        text="upright", engine="local:pp-structurev3",
        confidences=[0.95] if angle == 90 else [0.10]))
    seen = {}

    class _V:
        @staticmethod
        def is_available():
            return True

        def convert(self, p):
            seen["path"] = Path(p)
            return ConversionResult(text="v", engine="mineru")

    monkeypatch.setattr(d, "_make_verifier", lambda: _V())
    img = tmp_path / "scan.png"
    _real_png(img)
    d.convert(img)
    # 校验器收到的不是原始文件（是按选定角度旋转出的临时件）
    assert seen["path"] != img
