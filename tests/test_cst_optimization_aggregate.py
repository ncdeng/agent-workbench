from __future__ import annotations

import json

import pytest

from benchmarks.cst_optimization_aggregate import EXPECTED_ARMS, aggregate_reports


def test_aggregate_rejects_selected_unsuccessful_arm(tmp_path):
    paths = {}
    protocol = {"solver_budget_upper_bound": 4}
    for arm in EXPECTED_ARMS:
        path = tmp_path / f"{arm}.json"
        payload = {
            "source_project": "D:/case.cst",
            "protocol": protocol,
            "arms": {
                arm: {
                    "success": arm != "llm",
                    "protocol_violation": False,
                    "physical_solver_evaluations": 2,
                    "target_met": False,
                    "improvement_db": 1.0,
                }
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[arm] = path

    with pytest.raises(ValueError, match="not successful"):
        aggregate_reports(selected={"case": paths}, output_path=tmp_path / "out.json")
