"""页分隔符。只标记位置，不改写、不替换来源正文。"""


def join_pages(parts: list[str]) -> str:
    """在每一页正文前写一行 ``<!-- page: N -->``。N 从 1 起。

    调用方必须已经按页握有正文（OCR）。不要用它替换另一套转换结果。
    """
    blocks: list[str] = []
    for index, part in enumerate(parts, start=1):
        body = part.strip("\n")
        blocks.append(f"<!-- page: {index} -->\n{body}")
    return "\n".join(blocks)
