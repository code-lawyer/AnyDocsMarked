"""Strip markdown and HTML syntax before cross-encoder scoring.

Digits, commas, decimal points, and the wording itself stay. Callers score
the returned string and keep the original passage text.
"""

from __future__ import annotations

import re

# Images before links so `![alt](url)` is dropped whole, not reduced to its alt text.
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Name starts with an ASCII letter and the tag stays on one line. `<50,000.00…>`
# is not a tag, and a later `>` must not swallow the text in between.
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:[ \t/][ !-;=?-~]*)?>")
# CJK and ideographic punctuation are not URL characters. `\S` would eat the
# following sentence when there is no space after the URL.
_BARE_URL = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
_HEADING_MARK = re.compile(r"^#{1,6} ", re.MULTILINE)
_EMPHASIS = re.compile(r"\*\*|__")
_FENCE = re.compile(r"^[ \t]*`{3,}[^\n]*$", re.MULTILINE)
_TABLE_RULE = re.compile(r"^[ \t|:\-]*[|:\-][ \t|:\-]*$", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_passage(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    text = _BARE_URL.sub("", text)
    text = _HEADING_MARK.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = _FENCE.sub("", text)
    text = _TABLE_RULE.sub("", text)
    return _BLANK_RUN.sub("\n\n", text)
