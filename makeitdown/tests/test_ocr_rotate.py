import pytest

from makeitdown.ocr_rotate import (
    best_rotation_angle,
    mean_confidence,
    resolve_best_angle,
)


def test_picks_highest_confidence_angle():
    assert best_rotation_angle({0: 0.40, 90: 0.95, 180: 0.30, 270: 0.20}) == 90


def test_tie_prefers_no_rotation():
    assert best_rotation_angle({0: 0.9, 90: 0.9}) == 0


def test_empty_defaults_to_zero():
    assert best_rotation_angle({}) == 0


def test_mean_confidence_of_empty_is_none():
    assert mean_confidence(None) is None
    assert mean_confidence([]) is None


def test_mean_confidence_averages():
    assert mean_confidence([0.2, 0.4]) == pytest.approx(0.3)


def test_no_probe_when_primary_confident():
    # 高置信页不重探——probe 绝不被调用（限住 4× OCR 成本）。
    calls = []

    def probe(angle):
        calls.append(angle)
        return [0.99]

    assert resolve_best_angle([0.9, 0.92], probe, min_confidence=0.6) == 0
    assert calls == []


def test_no_probe_when_confidence_unknown():
    # 后端不报分数（confidences=None）→ 无法判低置信 → 不重探。
    called = []
    assert resolve_best_angle(None, lambda a: called.append(a), min_confidence=0.6) == 0
    assert called == []


def test_probes_and_picks_best_when_primary_low():
    scores = {90: [0.95], 180: [0.30], 270: [0.20]}

    def probe(angle):
        return scores[angle]

    assert resolve_best_angle([0.35], probe, min_confidence=0.6) == 90


def test_low_primary_but_no_better_angle_stays_zero():
    # 低置信但四个方向都不比 0° 好 → 仍不旋转。
    assert resolve_best_angle([0.35], lambda a: [0.10], min_confidence=0.6) == 0
