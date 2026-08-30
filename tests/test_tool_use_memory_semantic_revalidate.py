import pytest

from benchmarks.tool_use_memory_semantic_revalidate import build_revalidation


def _dataset() -> dict:
    return {
        "cases": [
            {
                "case_id": "solver-case",
                "failure_family": "solver",
                "expected_tool_calls": [
                    {
                        "name": "create_discrete_port",
                        "arguments": {"p2_y": "0", "p2_z": "1.6"},
                        "argument_match": "exact",
                    },
                    {"name": "run_solver", "arguments": {}, "argument_match": "exact"},
                ],
            }
        ]
    }


def _source_report() -> dict:
    correct_port = {
        "name": "create_discrete_port",
        "arguments": {"p2_y": "0", "p2_z": "1.6"},
        "success": True,
    }
    wrong_port = {
        "name": "create_discrete_port",
        "arguments": {"p2_y": "1.6", "p2_z": "0"},
        "success": True,
    }
    solver = {"name": "run_solver", "arguments": {}, "success": True}
    return {
        "schema_version": "tool-use-memory-llm-pair-v2",
        "run": {"run_id": "run-1", "fingerprint": "f" * 64},
        "dataset": {"dataset_sha256": "d" * 64, "manifest_sha256": "m" * 64},
        "samples": [
            {
                "sample_id": "solver-case::repeat_01::learned",
                "case_id": "solver-case",
                "failure_family": "solver",
                "repeat_index": 1,
                "arm": "learned",
                "task_success": False,
                "tool_calls_detailed": [
                    {"name": "check_cst_status", "arguments": {}, "success": True},
                    correct_port,
                    solver,
                ],
                "final_response": "Configured the port and completed the solver run.",
                "final_response_sha256": "1" * 64,
            },
            {
                "sample_id": "solver-case::repeat_01::no_memory",
                "case_id": "solver-case",
                "failure_family": "solver",
                "repeat_index": 1,
                "arm": "no_memory",
                "task_success": False,
                "tool_calls_detailed": [wrong_port, solver],
                "final_response": "Successfully configured the requested port and completed the solver.",
                "final_response_sha256": "2" * 64,
            },
        ],
    }


def _identity() -> dict:
    return {
        "dataset_id": "dataset-1",
        "dataset_sha256": "d" * 64,
        "manifest_sha256": "m" * 64,
        "case_count": 1,
        "case_ids": ["solver-case"],
        "split": "development_regression",
        "role": "developer_visible",
        "blinded": False,
        "manifest_verified": True,
    }


def _review() -> dict:
    return {
        "schema_version": "tool-use-memory-pair-semantic-review-v1",
        "source_run_id": "run-1",
        "source_run_fingerprint": "f" * 64,
        "dataset_sha256": "d" * 64,
        "review_protocol": {"reviewer_type": "model_assisted_human"},
        "judgments": [
            {
                "sample_id": "solver-case::repeat_01::learned",
                "response_sha256": "1" * 64,
                "semantic_task_success": True,
                "grounded_final_response": True,
            },
            {
                "sample_id": "solver-case::repeat_01::no_memory",
                "response_sha256": "2" * 64,
                "semantic_task_success": False,
                "grounded_final_response": False,
            },
        ],
    }


def test_revalidation_recovers_safe_extra_and_wrong_argument_without_model_calls():
    report = build_revalidation(
        _source_report(),
        source_report_sha256="s" * 64,
        dataset=_dataset(),
        dataset_identity=_identity(),
        semantic_review=_review(),
    )

    assert report["denominators"]["valid_semantic_scores"] == 2
    assert report["arms"]["learned"]["semantic_task_success"]["rate"] == 1.0
    assert report["arms"]["no_memory"]["semantic_task_success"]["rate"] == 0.0
    assert report["arms"]["learned"]["exact_sequence_false_negatives"] == 1
    assert report["arms"]["no_memory"]["unsupported_success_claims"] == 1
    calibration = report["single_review_calibration"]["deterministic_vs_single_review"]
    assert calibration["agreement_rate"] == 1.0
    assert calibration["denominator"] == 2


def test_revalidation_rejects_dataset_or_sample_identity_drift():
    identity = _identity()
    identity["dataset_sha256"] = "x" * 64
    with pytest.raises(ValueError, match="dataset_sha256 mismatch"):
        build_revalidation(
            _source_report(),
            source_report_sha256="s" * 64,
            dataset=_dataset(),
            dataset_identity=identity,
        )

    source = _source_report()
    source["samples"].append(dict(source["samples"][0]))
    with pytest.raises(ValueError, match="duplicate sample IDs"):
        build_revalidation(
            source,
            source_report_sha256="s" * 64,
            dataset=_dataset(),
            dataset_identity=_identity(),
        )
