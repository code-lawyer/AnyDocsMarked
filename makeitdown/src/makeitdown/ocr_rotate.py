"""Rotation correction: pick the upright orientation for a low-confidence scan.

The decision is pure (highest mean OCR confidence wins, ties prefer 0°). Probing
the three rotated angles costs three extra OCR passes, so it only runs when the
un-rotated page is *confidently low* — a page whose primary OCR already scored
well (or a backend that reports no scores) is never re-probed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

_ANGLES = (0, 90, 180, 270)


def best_rotation_angle(confidence_by_angle: dict[int, float]) -> int:
    """Return the angle in {0,90,180,270} with highest confidence; ties -> 0."""
    if not confidence_by_angle:
        return 0
    return max(_ANGLES, key=lambda a: (confidence_by_angle.get(a, -1.0), -a))


def mean_confidence(confidences: Sequence[float] | None) -> float | None:
    """Mean of per-line OCR scores; None when unknown/empty (can't judge)."""
    if not confidences:
        return None
    return sum(confidences) / len(confidences)


def resolve_best_angle(
    primary_confidences: Sequence[float] | None,
    probe: Callable[[int], Sequence[float] | None],
    min_confidence: float,
) -> int:
    """Decide the upright angle for a page.

    ``primary_confidences`` are the un-rotated pass's per-line scores. Returns 0
    (no rotation, no cost) unless the primary mean is *known and below*
    ``min_confidence``. Only then is ``probe`` called for 90/180/270 to compare.
    """
    primary = mean_confidence(primary_confidences)
    if primary is None or primary >= min_confidence:
        return 0
    by_angle: dict[int, float] = {0: primary}
    for angle in (90, 180, 270):
        m = mean_confidence(probe(angle))
        if m is not None:  # unknown angles fall to best_rotation_angle's own default
            by_angle[angle] = m
    return best_rotation_angle(by_angle)
