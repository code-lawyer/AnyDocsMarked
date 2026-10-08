from dataclasses import dataclass, field


@dataclass
class ConversionResult:
    """Uniform output of every converter.

    text:   the Markdown body (no frontmatter)
    engine: label of the engine used, e.g. "markitdown",
            "local:pp-structurev3", "cloud:paddleocr-vl-1.6"
    pages:  page count when known (PDFs), else None
    assets: relative-path -> raw bytes for extracted images to write alongside the md
    confidences: per-region OCR recognition scores when the backend exposes them,
            else None; consumed by the quality check to flag low-confidence output.
    notices: reasons that belong in the report warning list, never in ``text``.
    page_map: "native" when ``text`` already carries authoritative page markers.
    """

    text: str
    engine: str
    pages: int | None = None
    assets: dict[str, bytes] = field(default_factory=dict)
    confidences: list[float] | None = None
    cross_check_reasons: list[str] | None = None
    notices: list[str] | None = None
    page_map: str | None = None


class OCRUnavailableError(RuntimeError):
    """Raised when no usable OCR backend is configured/available."""


class ConversionUnavailable(RuntimeError):
    """文件可识别但当前后端无法转换(无可用转换器 / 加密 / 损坏)。
    携带可执行提示;pipeline 将其转为"知情跳过"而非失败。"""


class LegacyConversionUnavailable(ConversionUnavailable):
    """老式二进制(.doc/.wps)无 COM/LibreOffice/anydoc 后端可用。带可执行提示。"""
