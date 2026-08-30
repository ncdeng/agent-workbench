from __future__ import annotations

import pytest

from cst_agent_workbench.results.contracts import (
    ResultKind,
    downsample_curve,
    paginate_items,
    validate_result_envelope,
)


def test_downsample_curve_rejects_unusable_limit():
    with pytest.raises(ValueError, match="at least 4"):
        downsample_curve([{"x": i, "y": i} for i in range(10)], max_points=3, y_key="y")


def test_paginate_items_reports_stable_cursor_metadata():
    page = paginate_items(["a", "b", "c"], offset=1, limit=1)
    assert page == {
        "total": 3,
        "offset": 1,
        "limit": 1,
        "returned": 1,
        "has_more": True,
        "next_offset": 2,
        "items": ["b"],
    }


def test_result_envelope_validator_rejects_point_count_drift():
    payload = {
        "success": True,
        "result_kind": ResultKind.CURVE_1D.value,
        "total_points": 2,
        "returned_points": 2,
        "downsampled": False,
        "plot_data": [{"x": 1.0, "y": 2.0}],
    }
    with pytest.raises(ValueError, match="returned_points"):
        validate_result_envelope(payload)


@pytest.mark.parametrize("field", ["points", "total_points", "returned_points"])
def test_result_envelope_validator_rejects_boolean_point_counts(field):
    payload = {
        "success": True,
        "result_kind": ResultKind.CURVE_1D.value,
        "points": 1,
        "total_points": 1,
        "returned_points": 1,
        "downsampled": False,
        "plot_data": [{"x": 1.0, "y": 2.0}],
    }
    payload[field] = True

    with pytest.raises(ValueError, match=field):
        validate_result_envelope(payload)
