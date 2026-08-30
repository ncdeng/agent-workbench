import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.eval_statistics import exact_paired_binary_test
from benchmarks import tool_use_memory_llm_pair_runner as runner


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def test_memory_llm_pair_dataset_is_manifest_bound_and_explicitly_not_blinded():
    dataset, identity = runner._load_dataset(runner.DEFAULT_DATASET_PATH)

    assert identity["manifest_verified"] is True
    assert identity["case_count"] == 6
    assert identity["blinded"] is False
    assert len({case["case_id"] for case in dataset["cases"]}) == 6
    assert len({case["query"] for case in dataset["cases"]}) == 6


def test_multifamily_dataset_has_executable_argument_level_oracles(tmp_path: Path):
    dataset_path = runner.DEFAULT_DATASET_PATH.with_name("tool_use_memory_llm_pair_dev_v2.json")

    report = runner.validate_dataset_oracles(
        dataset_path=dataset_path,
        artifact_root=tmp_path / "oracle_artifacts",
    )

    assert report["dataset"]["manifest_verified"] is True
    assert report["case_count"] == 8
    assert len({case["failure_family"] for case in report["cases"]}) == 4
    assert report["all_oracles_executable"] is True


def test_memory_llm_pair_manifest_mismatch_fails_closed(tmp_path: Path):
    dataset_path = tmp_path / "dataset.json"
    manifest_path = tmp_path / "dataset.manifest.json"
    dataset_path.write_bytes(runner.DEFAULT_DATASET_PATH.read_bytes())
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_id": "tool_use_memory_llm_pair_dev_v1",
                "dataset_sha256": "wrong",
                "case_count": 6,
                "case_ids": [f"status_audit_{index:02d}" for index in range(1, 7)],
                "split": "development_regression",
                "role": "developer_visible_pilot",
                "blinded": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest mismatch"):
        runner._load_dataset(dataset_path, manifest_path=manifest_path)


def test_memory_llm_pair_preserves_manifest_tool_catalog_order():
    tools = [_tool("check_cst_status"), _tool("run_solver"), _tool("execute_vba_script")]

    selected = runner._selected_tools(
        SimpleNamespace(tools=tools),
        ["execute_vba_script", "check_cst_status"],
    )

    assert [tool["function"]["name"] for tool in selected] == [
        "execute_vba_script",
        "check_cst_status",
    ]


def test_memory_llm_arm_metrics_do_not_hide_repeat_errors():
    samples = [
        {
            "task_success": True,
            "repeated_failed_tool": False,
            "invalid_call": False,
            "token_usage": {"total": 100},
            "latency_ms": 10.0,
        },
        {
            "task_success": False,
            "repeated_failed_tool": True,
            "invalid_call": False,
            "token_usage": {"total": 200},
            "latency_ms": 30.0,
        },
    ]

    metrics = runner._arm_metrics(samples)

    assert metrics["task_success_rate"] == 0.5
    assert metrics["repeat_error_rate"] == 0.5
    assert metrics["invalid_call_rate"] == 0.0
    assert metrics["mean_tokens"] == 150.0
    assert metrics["mean_latency_ms"] == 20.0


def test_expected_tool_call_scoring_checks_arguments_and_call_count():
    expected = [
        {
            "name": "build_rectangular_patch_fast",
            "arguments": {"f0_ghz": 2.45, "run_solver": False},
            "argument_match": "subset",
        }
    ]
    actual = [
        {
            "name": "build_rectangular_patch_fast",
            "arguments": {"f0_ghz": 2.45, "run_solver": False, "epsilon_r": 4.4},
        }
    ]

    passed, checks = runner._score_expected_calls(actual, expected)
    assert passed is True
    assert checks[0]["arguments_match"] is True

    wrong = [{"name": "build_rectangular_patch_fast", "arguments": {"f0_ghz": 5.8}}]
    passed, checks = runner._score_expected_calls(wrong, expected)
    assert passed is False
    assert checks[0]["arguments_match"] is False


def test_first_executor_memory_evidence_uses_request_not_final_session_metadata():
    record = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "[工具使用经验；仅用于安全白名单内的排序与参数提示]\n"
                    "- failed tools=create_brick; reason=missing parameter; correction=store first"
                ),
            }
        ],
        "tool_names": ["store_parameter", "create_brick"],
    }

    evidence = runner._first_executor_memory_evidence(
        record,
        allowed_tools=["create_brick", "store_parameter"],
    )

    assert evidence["guidance_presented"] is True
    assert evidence["recalled_record_count"] == 1
    assert evidence["tool_order_changed_from_manifest"] is True
    assert evidence["memory_applied"] is True


def test_exact_paired_binary_test_uses_only_discordant_pairs():
    result = exact_paired_binary_test(
        [(False, True), (False, True), (True, False), (True, True), (False, False)]
    )

    assert result["treatment_wins"] == 2
    assert result["treatment_losses"] == 1
    assert result["ties"] == 2
    assert result["discordant_pairs"] == 3
    assert result["p_value"] == 1.0


def test_arm_schedule_is_deterministic_and_balanced():
    cases = [{"case_id": f"case_{index}"} for index in range(5)]

    first = runner._arm_schedule(cases, 3, "a" * 64)
    second = runner._arm_schedule(cases, 3, "a" * 64)

    assert first == second
    cold_first = sum(item["arm_order"][0] == "no_memory" for item in first)
    learned_first = len(first) - cold_first
    assert abs(cold_first - learned_first) <= 1


def _sample(case: dict, arm: str, repeat_index: int) -> dict:
    pair_id = f"{case['case_id']}::repeat_{repeat_index:02d}"
    success = arm == "learned"
    detailed_calls = (
        [
            {
                "name": item["name"],
                "arguments": dict(item.get("arguments") or {}),
                "success": True,
            }
            for item in (case.get("expected_tool_calls") or [])
        ]
        if success
        else [{"name": "execute_vba_script", "arguments": {}, "success": True}]
    )
    sample = {
        "sample_id": f"{pair_id}::{arm}",
        "pair_id": pair_id,
        "case_id": case["case_id"],
        "failure_family": case["failure_family"],
        "repeat_index": repeat_index,
        "arm": arm,
        "query": case["query"],
        "allowed_tools": list(case["allowed_tools"]),
        "presented_tool_order": list(case["allowed_tools"]),
        "expected_tool_sequence": list(case["expected_tool_sequence"]),
        "tool_calls": list(case["expected_tool_sequence"]) if success else ["execute_vba_script"],
        "tool_calls_detailed": detailed_calls,
        "first_tool": "check_cst_status" if success else "execute_vba_script",
        "exact_tool_sequence": success,
        "task_success": success,
        "repeated_failed_tool": not success,
        "invalid_call": False,
        "recalled_memory_count": 1 if success else 0,
        "recalled_memory_sha256": "r",
        "memory_guidance_sha256": "g",
        "final_response": "ok",
        "final_response_sha256": "f",
        "token_usage": {"prompt": 10, "completion": 5, "total": 15, "calls": 1},
        "latency_ms": 1.0,
    }
    sample["agent_completion_ok"] = True
    sample["deterministic_semantic"] = runner.semantic_grader.score_semantic_sample(
        sample,
        case,
        agent_completion_ok=True,
        completion_evidence_source="test_runtime",
    )
    return sample


def test_run_pair_checkpoint_resume_does_not_repeat_completed_samples(tmp_path: Path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    checkpoint_path = tmp_path / "checkpoint.json"
    executed: list[str] = []
    fixture_project_paths: dict[str, Path] = {}

    monkeypatch.setattr(
        runner,
        "_make_agent",
        lambda **kwargs: SimpleNamespace(
            tools=[_tool("execute_vba_script"), _tool("check_cst_status")]
        ),
    )

    def fake_seed(**kwargs):
        kwargs["memory_root"].mkdir(parents=True, exist_ok=True)
        fixture_project_paths[kwargs["fixture"]["fixture_id"]] = kwargs["project_path"]
        return {"fixture_id": kwargs["fixture"]["fixture_id"], "write_evidence": {"written": True}}

    def fake_run_sample(**kwargs):
        executed.append(f"{kwargs['case']['case_id']}::{kwargs['repeat_index']}::{kwargs['arm']}")
        assert kwargs["project_path"] == fixture_project_paths[kwargs["case"]["learn_fixture_id"]]
        return _sample(kwargs["case"], kwargs["arm"], kwargs["repeat_index"])

    monkeypatch.setattr(runner, "_seed_production_failure", fake_seed)
    monkeypatch.setattr(runner, "_run_sample", fake_run_sample)

    first = runner.run_pair(
        artifact_root=artifact_root,
        model="test-model",
        repeat_count=1,
        checkpoint_path=checkpoint_path,
    )
    assert len(executed) == 12
    assert first["run"]["checkpoint"]["executed_sample_count"] == 12
    executed.clear()

    resumed = runner.run_pair(
        artifact_root=artifact_root,
        model="test-model",
        repeat_count=1,
        checkpoint_path=checkpoint_path,
        resume=True,
    )

    assert executed == []
    assert resumed["run"]["checkpoint"]["resumed_sample_count"] == 12
    assert resumed["paired"]["case_majority"]["rate_delta"] == 1.0
    assert resumed["paired"]["case_majority"]["paired_test"]["p_value"] == pytest.approx(0.03125)
    assert resumed["deterministic_semantic"]["case_majority"]["rate_delta"] == 1.0


def test_checkpoint_fingerprint_changes_with_manifest_identity():
    identity = {"dataset_sha256": "a", "manifest_sha256": "b"}
    schedule = [{"pair_id": "p", "case_id": "c", "repeat_index": 1, "arm_order": ["no_memory", "learned"]}]

    first = runner._checkpoint_fingerprint(
        dataset_identity=identity,
        model="m",
        repeat_count=1,
        schedule=schedule,
        tool_catalog_sha256="tools",
    )
    second = runner._checkpoint_fingerprint(
        dataset_identity={**identity, "manifest_sha256": "changed"},
        model="m",
        repeat_count=1,
        schedule=schedule,
        tool_catalog_sha256="tools",
    )

    assert first != second


def test_max_new_samples_pauses_only_after_atomic_checkpoint(tmp_path: Path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    checkpoint_path = tmp_path / "checkpoint.json"
    executed: list[str] = []
    monkeypatch.setattr(
        runner,
        "_make_agent",
        lambda **kwargs: SimpleNamespace(
            tools=[_tool("execute_vba_script"), _tool("check_cst_status")]
        ),
    )

    def fake_seed(**kwargs):
        kwargs["memory_root"].mkdir(parents=True, exist_ok=True)
        return {"fixture_id": kwargs["fixture"]["fixture_id"], "write_evidence": {"written": True}}

    def fake_run_sample(**kwargs):
        executed.append(f"{kwargs['case']['case_id']}::{kwargs['repeat_index']}::{kwargs['arm']}")
        return _sample(kwargs["case"], kwargs["arm"], kwargs["repeat_index"])

    monkeypatch.setattr(runner, "_seed_production_failure", fake_seed)
    monkeypatch.setattr(runner, "_run_sample", fake_run_sample)

    with pytest.raises(runner.EvaluationPaused) as paused:
        runner.run_pair(
            artifact_root=artifact_root,
            model="test-model",
            repeat_count=1,
            checkpoint_path=checkpoint_path,
            max_new_samples=3,
        )

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert paused.value.progress["completed_samples"] == 3
    assert len(checkpoint["samples"]) == 3
    assert len(executed) == 3

    runner.run_pair(
        artifact_root=artifact_root,
        model="test-model",
        repeat_count=1,
        checkpoint_path=checkpoint_path,
        resume=True,
    )
    assert len(executed) == 12
