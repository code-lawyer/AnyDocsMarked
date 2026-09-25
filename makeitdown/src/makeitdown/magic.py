from pathlib import Path

_OOXML_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sniff_container(path: Path) -> str:
    """Return 'ooxml', 'ole2', or 'unknown' from the first 8 bytes."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(_OOXML_MAGIC):
        return "ooxml"
    if head.startswith(_OLE2_MAGIC):
        return "ole2"
    return "unknown"
