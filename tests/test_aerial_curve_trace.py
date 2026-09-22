from __future__ import annotations

import numpy as np
import pytest

from torii_sumo.core.aerial_curve_trace import trace_probability_curve


def test_trace_does_not_bridge_a_wide_unsupported_gap():
    probability = np.ones((24, 40))
    probability[:, 14:26] = 0
    with pytest.raises(ValueError, match="support|reach"):
        trace_probability_curve(probability, start=(4.0, 12.0), end=(35.0, 12.0),
                                start_heading_deg=0, end_heading_deg=0,
                                minimum_probe_probability=0.25)


def test_rectangle_probe_follows_a_curved_probability_ridge() -> None:
    probability = np.zeros((64, 64), dtype=float)
    expected = []
    for x in range(6, 56):
        y = round(48 - 0.012 * (x - 6) ** 2)
        expected.append((float(x), float(y)))
        probability[max(0, y - 2) : y + 3, max(0, x - 2) : x + 3] = 1.0

    traced = trace_probability_curve(
        probability,
        start=expected[0],
        end=expected[-1],
        start_heading_deg=0.0,
        end_heading_deg=-50.0,
        step_px=2.0,
        probe_length_px=5.0,
        probe_width_px=3.0,
        beam_width=24,
    )

    mean_error = np.mean(
        [min(np.hypot(x - rx, y - ry) for rx, ry in expected) for x, y in traced]
    )
    assert traced[0] == expected[0]
    assert traced[-1] == expected[-1]
    assert mean_error < 2.0


def test_rectangle_probe_rejects_a_blank_probability_field() -> None:
    probability = np.zeros((32, 32), dtype=float)

    try:
        trace_probability_curve(
            probability,
            start=(3.0, 16.0),
            end=(28.0, 16.0),
            start_heading_deg=0.0,
            end_heading_deg=0.0,
        )
    except ValueError as error:
        assert "probability" in str(error)
    else:
        raise AssertionError("blank probability field must be rejected")
