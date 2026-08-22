import os
import threading
from contextlib import contextmanager
from pathlib import Path

from .cloud_consent import has_consent, require_cloud_consent
from .models import ConversionResult, OCRUnavailableError
from .ocr_cloud import CloudOCR
from .ocr_crosscheck import compare
from .ocr_local import LocalOCR
from .ocr_mineru import MinerUCloud, MinerULocal
from .ocr_rotate import resolve_best_angle
from .router import IMAGE_EXTS

# Private alias that keeps the original class reference even when the module-level
# `LocalOCR` name is replaced by monkeypatching in tests.  Availability checks
# go through this alias so `is_available()` remains on the real class.
_LocalOCR_cls = LocalOCR

_INSTALL_HINT = (
    "No OCR backend available. Either install the local engine "
    "(`pip install \"makeitdown[local]\"`) or set a cloud token "
    "(env PADDLEOCR_AISTUDIO_TOKEN or --cloud-token)."
)
_CLOUD_HINT = (
    "Cloud OCR selected but no token. Set env PADDLEOCR_AISTUDIO_TOKEN "
    "or pass --cloud-token."
)


class OCRDispatcher:
    """Selects and caches an OCR backend per the chosen engine mode."""

    def __init__(
        self,
        engine: str = "cloud",
        model: str | None = None,
        token: str | None = None,
        poll_interval: float = 5.0,
        cross_check: bool = False,
        cross_check_ratio: float = 0.1,
        cross_check_mode: str = "cloud",
        cloud_consent: bool = False,
        mineru_token: str | None = None,
        rotate: bool = True,
        rotate_min_confidence: float = 0.6,
    ):
        self.engine = engine
        self.model = model
        self.token = token
        self.poll_interval = poll_interval
        self.cross_check = cross_check
        self.cross_check_ratio = cross_check_ratio
        self.cross_check_mode = cross_check_mode
        self.cloud_consent = cloud_consent
        self.mineru_token = mineru_token
        # 旋转纠偏（FLOOR，默认开）：仅对首轮 OCR 低置信的扫描件重探，限住成本。
        self.rotate = rotate
        self.rotate_min_confidence = rotate_min_confidence
        self._backend = None
        self._verifier = None
        self._verifier_resolved = False
        self._lock = threading.Lock()

    def _make_cloud(self) -> CloudOCR:
        return CloudOCR(token=self.token, model=self.model, poll_interval=self.poll_interval)

    def _resolve_backend(self):
        # Resolved once and cached; the lock keeps concurrent workers from
        # racing to build two backends on the first conversion.
        if self._backend is not None:
            return self._backend
        with self._lock:
            if self._backend is not None:
                return self._backend
            if self.engine == "local":
                if not _LocalOCR_cls.is_available():
                    raise OCRUnavailableError(_INSTALL_HINT)
                self._backend = LocalOCR(model=self.model)
            elif self.engine == "cloud":
                require_cloud_consent(self.cloud_consent)
                if not self.token:
                    raise OCRUnavailableError(_CLOUD_HINT)
                self._backend = self._make_cloud()
            elif self.engine == "auto":
                if _LocalOCR_cls.is_available():
                    self._backend = LocalOCR(model=self.model)
                elif self.token:
                    require_cloud_consent(self.cloud_consent)
                    self._backend = self._make_cloud()
                else:
                    raise OCRUnavailableError(_INSTALL_HINT)
            else:
                raise ValueError(f"unknown ocr engine: {self.engine}")
        return self._backend

    def _make_verifier(self):
        """The cross-check verifier (MinerU), or None to skip cleanly. Resolved once."""
        if self._verifier_resolved:
            return self._verifier
        with self._lock:
            if not self._verifier_resolved:
                self._verifier = self._resolve_verifier()
                self._verifier_resolved = True
        return self._verifier

    def _resolve_verifier(self):
        mode = self.cross_check_mode
        want_local = mode in ("local", "auto")
        want_cloud = mode in ("cloud", "auto")
        if want_local and MinerULocal.is_available():
            return MinerULocal()
        # Cloud only with explicit consent and a token; otherwise skip (never upload).
        if want_cloud and self.mineru_token and has_consent(self.cloud_consent):
            return MinerUCloud(token=self.mineru_token)
        return None

    @contextmanager
    def _oriented(self, path: Path, angle: int):
        """Yield a path to ``path`` rotated by ``angle`` (a temp file, auto-cleaned).
        angle 0 yields the original path unchanged (no temp, no cost)."""
        if angle == 0:
            yield path
            return
        import tempfile

        from PIL import Image

        with Image.open(path) as img:
            rotated = img.rotate(angle, expand=True)
            fd, tmp = tempfile.mkstemp(suffix=path.suffix)
            os.close(fd)
            try:
                rotated.save(tmp)
                yield Path(tmp)
            finally:
                Path(tmp).unlink(missing_ok=True)

    def _ocr_at_angle(self, path: Path, angle: int) -> ConversionResult:
        """Rotate a scanned image by ``angle`` and OCR it (for the reorient probe)."""
        with self._oriented(path, angle) as p:
            return self._resolve_backend().convert(p)

    def _reorient(self, path: Path, primary: ConversionResult) -> tuple[ConversionResult, int]:
        """When a scanned image OCRs with low confidence, pick the upright angle.
        Returns (chosen result, chosen angle). Only image inputs are rotatable here."""
        if path.suffix.lower() not in IMAGE_EXTS:
            return primary, 0
        results = {0: primary}

        def probe(angle: int):
            results[angle] = self._ocr_at_angle(path, angle)
            return results[angle].confidences

        best = resolve_best_angle(primary.confidences, probe, self.rotate_min_confidence)
        return results.get(best, primary), best

    def convert(self, path: Path) -> ConversionResult:
        result = self._resolve_backend().convert(path)
        angle = 0
        if self.rotate:
            try:
                result, angle = self._reorient(path, result)
            except Exception:  # noqa: BLE001  旋转纠偏是加强项，绝不因它丢掉已成功的主结果
                result, angle = result, 0
        if not self.cross_check:
            return result
        verifier = self._make_verifier()
        if verifier is None:
            result.cross_check_reasons = ["双OCR互校跳过：无可用 MinerU 校验引擎（装本地版 mineru，或设 MINERU_API_TOKEN 并加 --cloud-consent）"]
            return result
        try:
            # 校验器必须 OCR 与主结果**同一朝向**的页——reorient 选了非 0 角度时，让校验器
            # 也读旋正后的临时件，否则两引擎比的是不同朝向、误报巨大分歧。
            with self._oriented(path, angle) as vp:
                other = verifier.convert(vp)
            cc = compare(result.text, other.text, ratio_threshold=self.cross_check_ratio)
            result.cross_check_reasons = cc.reasons
            # other.engine is the verifier's label (MinerULocal.convert sets it to engine_label="mineru")
            result.engine = f"{result.engine} × {other.engine}"
        except Exception as e:  # never lose the primary conversion
            result.cross_check_reasons = [f"双OCR互校失败（已保留主引擎结果）：{type(e).__name__}"]
        return result
