from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.agent_e2e_outcome_revalidate import build_revalidation, render_markdown


def _write_source(path: Path) -> bytes:
    report = {
        "schema_version": "agent-e2e-report-v1",
        "run": {
            "run_id": "run-1",
            "agent_brain": "pi",
            "provider": "openai_compatible",
            "model": "gpt-test",
        },
        "groups": {
            "full": {
                "cases": [
                    {
                        "case_id": "ok",
                        "sample_id": "ok::repeat_01",
                        "task_success": True,
                        "strict_grounded_success": True,
                        "trace_status": "completed",
                        "tool_calls": ["check_cst_status"],
                        "token_usage": {"total": 10},
                        "provider_diagnostics": [],
                        "final_response": "online",
                    },
                    {
                        "case_id": "outage",
                        "sample_id": "outage::repeat_01",
                        "task_success": False,
                        "strict_grounded_success": False,
                        "trace_status": "failed",
                        "tool_calls": [],
                        "token_usage": {"total": 0},
                        "provider_diagnostics": [
                            {"error": "InternalServerError: Error code: 503"}
                        ],
                        "final_response": "Service temporarily unavailable",
                    },
                ]
            }
        },
    }
    raw = json.dumps(report, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_revalidation_binds_source_and_separates_metrics(tmp_path):
    source = tmp_path / "source.json"
    raw = _write_source(source)

    result = build_revalidation(source)

    assert result["execution"] == {
        "new_model_calls": 0,
        "new_cst_calls": 0,
        "method": "deterministic_post_hoc_revalidation",
    }
    assert result["source_report"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["source_report"]["run_id"] == "run-1"
    metrics = result["groups"]["full"]["metrics"]
    assert metrics["eligible_sample_count"] == 1
    assert metrics["agent_success_count"] == 1
    assert metrics["agent_failure_count"] == 0
    assert metrics["provider_failure_count"] == 1
    assert metrics["eligible_task_success_rate"] == 1.0
    assert metrics["provider_availability_rate"] == 0.5
    assert metrics["end_to_end_task_success_rate"] == 0.5
    assert result["groups"]["full"]["cases"][1]["provider_errors"][0]["status_code"] == 503


def test_markdown_states_capability_and_availability_separately(tmp_path):
    source = tmp_path / "source.json"
    _write_source(source)

    markdown = render_markdown(build_revalidation(source))

    assert "| full | 1/2 | 1 | 0 | 1 | 100.0% | 50.0% | 50.0% |" in markdown
    assert "excluded only from Agent-capability denominators" in markdown


def test_invalid_source_without_groups_is_rejected(tmp_path):
    source = tmp_path / "invalid.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="groups object"):
        build_revalidation(source)
