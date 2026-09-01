from __future__ import annotations

import json
from pathlib import Path

from benchmarks.freeze_agent_e2e_dataset import recorded_sha_matches


ROOT = Path(__file__).resolve().parents[1]
SELECTION_PATH = ROOT / "benchmarks" / "pi_harness_expanded_eval_v1.json"


def test_expanded_pi_harness_selection_is_sha_bound_and_stratified():
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    dataset_path = ROOT / selection["source_dataset"]
    manifest_path = ROOT / selection["source_manifest"]
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in dataset["cases"]}
    selected_ids = selection["case_ids"]

    assert recorded_sha_matches(selection["source_dataset_sha256"], dataset_path.read_bytes())
    assert recorded_sha_matches(selection["source_manifest_sha256"], manifest_path.read_bytes())
    assert len(selected_ids) == len(set(selected_ids)) == 12
    assert set(selected_ids).issubset(cases)
    assert selection["repeat_count"] == 3

    selected = [cases[case_id] for case_id in selected_ids]
    assert len({case["scenario_family"] for case in selected}) == 10
    assert len({case["category"] for case in selected}) == 5
    assert len({case["failure_family"] for case in selected}) == 3
    assert len({case["design_family"] for case in selected}) == 4
    assert sum(case["scenario_family"] == "multi_tool_solver_workflow" for case in selected) == 2
