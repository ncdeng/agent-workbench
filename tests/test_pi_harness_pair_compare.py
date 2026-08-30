from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.pi_harness_pair_compare import build_comparison, render_markdown


def _report(brain: str, outcomes: dict[str, list[bool]]) -> dict:
    cases = []
    case_ids = list(outcomes)
    for case_position, (case_id, repeats) in enumerate(outcomes.items(), start=1):
        for run_index, success in enumerate(repeats, start=1):
            cases.append(
                {
                    "case_id": case_id,
                    "sample_id": f"{case_id}::repeat_{run_index:02d}",
                    "run_index": run_index,
                    "seed": case_position * 100 + run_index,
                    "valid": True,
                    "task_success": success,
                    "strict_grounded_success": success,
                    "trace_status": "completed",
                    "tool_calls": ["check_cst_status"],
                    "token_usage": {"total": 10},
                    "provider_diagnostics": [],
                    "final_response": "done",
                }
            )
    repeat_count = len(next(iter(outcomes.values())))
    return {
        "schema_version": "agent-e2e-report-v1",
        "run": {
            "run_id": f"{brain}-run",
            "provider": "openai_compatible",
            "model": "gpt-test",
            "agent_brain": brain,
            "dataset_id": "dataset-v1",
            "dataset_split": "frozen_evaluation",
            "dataset_role": "developer_visible_frozen_evaluation",
            "dataset_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "manifest_verified": True,
            "evaluation_scope": "filtered_debug",
            "code_revision": "c" * 40,
            "case_count": len(outcomes),
            "sample_count": len(cases),
            "repeat_count": repeat_count,
            "filters": {
                "case_ids": case_ids,
                "categories": [],
                "scenario_families": [],
                "design_families": [],
                "failure_families": [],
            },
            "groups": ["full"],
            "harness_identity": {
                "agent_brain": brain,
                "combined_sha256": ("d" if brain == "native" else "e") * 64,
            },
        },
        "groups": {"full": {"cases": cases}},
    }


def _write_pair(tmp_path: Path, native: dict, pi: dict) -> tuple[Path, Path, bytes, bytes]:
    native_path = tmp_path / "native.json"
    pi_path = tmp_path / "pi.json"
    native_raw = json.dumps(native, ensure_ascii=False).encode("utf-8")
    pi_raw = json.dumps(pi, ensure_ascii=False).encode("utf-8")
    native_path.write_bytes(native_raw)
    pi_path.write_bytes(pi_raw)
    return native_path, pi_path, native_raw, pi_raw


def test_build_comparison_uses_unique_case_endpoints_and_binds_sources(tmp_path):
    native = _report(
        "native",
        {"both": [True, True, True], "native": [True, True, False], "pi": [False, False, True], "fail": [False, False, False]},
    )
    pi = _report(
        "pi",
        {"both": [True, True, True], "native": [False, False, True], "pi": [True, True, False], "fail": [False, False, False]},
    )
    native_path, pi_path, native_raw, pi_raw = _write_pair(tmp_path, native, pi)

    result = build_comparison(native_path, pi_path)

    majority = result["endpoints"]["task_success_majority"]
    assert majority["unique_case_count"] == 4
    assert {key: majority[key] for key in ("both_pass", "native_only", "pi_only", "both_fail")} == {
        "both_pass": 1,
        "native_only": 1,
        "pi_only": 1,
        "both_fail": 1,
    }
    assert majority["paired_delta"] == 0.0
    assert majority["paired_test"]["pair_count"] == 4
    assert result["sources"]["native"]["sha256"] == hashlib.sha256(native_raw).hexdigest()
    assert result["sources"]["pi"]["sha256"] == hashlib.sha256(pi_raw).hexdigest()
    assert result["execution"]["new_model_calls"] == 0


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda report: report["run"].update(model="other"), "run.model"),
        (lambda report: report["run"].update(repeat_count=2), "run.repeat_count"),
        (lambda report: report["groups"]["full"]["cases"][0].update(seed=999), "mismatch for seed"),
    ],
)
def test_alignment_mismatches_fail_closed(tmp_path, mutator, message):
    native = _report("native", {"case": [True, True, True]})
    pi = _report("pi", {"case": [True, True, True]})
    mutator(pi)
    native_path, pi_path, _, _ = _write_pair(tmp_path, native, pi)

    with pytest.raises(ValueError, match=message):
        build_comparison(native_path, pi_path)


def test_invalid_or_duplicate_samples_are_rejected(tmp_path):
    native = _report("native", {"case": [True, True, True]})
    pi = _report("pi", {"case": [True, True, True]})
    pi["groups"]["full"]["cases"][1]["sample_id"] = "case::repeat_01"
    native_path, pi_path, _, _ = _write_pair(tmp_path, native, pi)

    with pytest.raises(ValueError, match="duplicate sample_id"):
        build_comparison(native_path, pi_path)


def test_provider_failures_remain_separate_and_markdown_states_limits(tmp_path):
    native = _report("native", {"case": [True, True, True]})
    pi = _report("pi", {"case": [True, True, True]})
    failed = pi["groups"]["full"]["cases"][0]
    failed.update(
        task_success=False,
        strict_grounded_success=False,
        trace_status="failed",
        tool_calls=[],
        token_usage={"total": 0},
        provider_diagnostics=[{"error": "InternalServerError: Error code: 503"}],
        final_response="Service temporarily unavailable",
    )
    native_path, pi_path, _, _ = _write_pair(tmp_path, native, pi)

    result = build_comparison(native_path, pi_path)
    markdown = render_markdown(result)

    assert result["provider_outcomes"]["pi"]["provider_failure_count"] == 1
    assert "repeated model runs are correlated observations" in markdown
    assert "not blinded or sealed" in markdown
