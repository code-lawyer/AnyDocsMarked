"""BM25 keyword variants. No network and no embedding model.

Used only when the first full-text search returns fewer than k hits. The
original query is never one of the variants.
"""

from __future__ import annotations

from .tokenize import tokenize_for_fts

# Whole-token function characters. Single Han characters are dropped as well.
_FUNCTION_CHARS = frozenset("的了是在与及或和或其被把将对从为以而也都就")


def _is_single_han(token: str) -> bool:
    return len(token) == 1 and "\u4e00" <= token <= "\u9fff"


def _keep(token: str) -> bool:
    if not token.strip():
        return False
    if token in _FUNCTION_CHARS or _is_single_han(token):
        return False
    return True


def keyword_variants(query: str) -> list[str]:
    """Up to two BM25 queries with function words removed. Never the original."""
    original = tokenize_for_fts(query)
    if not original:
        return []
    content = [token for token in original.split() if _keep(token)]
    banned = {original, query.strip()}
    variants: list[str] = []

    def _add(text: str) -> None:
        if text and text not in banned and text not in variants:
            variants.append(text)

    # All remaining content words. Skip when that is already the token sequence.
    _add(" ".join(content))
    # Two longest content words, longer first. Equal lengths keep earlier tokens.
    if len(content) >= 3:
        longest = sorted(content, key=len, reverse=True)[:2]
        _add(" ".join(longest))
    return variants[:2]
