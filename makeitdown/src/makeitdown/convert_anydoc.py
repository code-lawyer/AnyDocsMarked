from pathlib import Path

import anydoc

from .models import ConversionResult, ConversionUnavailable

_ENGINE = "anydoc"


def convert(path: Path) -> ConversionResult:
    """用 anydoc 的进程内 Rust 解析器把老式二进制 Office(.doc/.ppt/.xls/.xlsb)
    转 Markdown,无需任何外部程序。

    anydoc 的类型化失败 → ConversionUnavailable(可识别但此处不可转,带提示,
    由 pipeline 记为知情跳过)。未知异常照常上抛,由 pipeline 记 failed。"""
    path = Path(path)
    try:
        text = anydoc.to_markdown(str(path))
    except anydoc.EncryptedError as e:
        raise ConversionUnavailable(
            f"{path.suffix or '文件'} 已加密,需先用密码解密再转换 —— 跳过。"
        ) from e
    except (
        anydoc.UnsupportedError,
        anydoc.MalformedError,
        anydoc.MissingPartError,
        anydoc.ResourceLimitError,
        anydoc.ConvertError,
    ) as e:
        raise ConversionUnavailable(
            f"anydoc 无法解析 {path.suffix or '该文件'}({type(e).__name__}) —— 跳过。"
        ) from e
    return ConversionResult(text=text, engine=_ENGINE, pages=None)
