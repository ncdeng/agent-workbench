from __future__ import annotations

import json
from pathlib import Path

from benchmarks.agent_ablation_runner import DeterministicProposalProvider, OpenAICompatibleProposalProvider, build_markdown_summary, build_run_metadata, build_stdout_summary, run_ablation, validate_quality_thresholds
from benchmarks.fake_cst_simulator import FakeCSTCase, FakeCSTSimulator, make_benchmark_cases


def test_fake_cst_patch_length_controls_resonance_frequency():
    case = FakeCSTCase(
        name="trend",
        seed=1,
        initial_params={"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.8},
    )
    sim = FakeCSTSimulator(case)

    base = sim.evaluate({"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.8})
    longer = sim.evaluate({"patch_L": 17.0, "inset_depth": 3.0, "feed_W": 2.8})
    shorter = sim.evaluate({"patch_L": 15.0, "inset_depth": 3.0, "feed_W": 2.8})

    assert longer["min_freq_ghz"] < base["min_freq_ghz"]
    assert shorter["min_freq_ghz"] > base["min_freq_ghz"]


def test_make_benchmark_cases_is_deterministic():
    first = make_benchmark_cases(20)
    second = make_benchmark_cases(20)

    assert len(first) == 20
    assert [case.name for case in first] == [case.name for case in second]
    assert [case.initial_params for case in first] == [case.initial_params for case in second]


def test_run_metadata_records_reproducible_context_without_secrets(monkeypatch):
    class Provider:
        name = "metadata_provider"
        model = "provider-model"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    cases = make_benchmark_cases(2)

    metadata = build_run_metadata(Provider(), cases)

    assert metadata["proposal_provider"] == "metadata_provider"
    assert metadata["provider_model"] == "provider-model"
    assert metadata["openai_api_key_configured"] is True
    assert metadata["openai_base_url_configured"] is True
    assert metadata["case_seed_min"] == 10000
    assert metadata["case_seed_max"] == 10001
    assert metadata["case_names"] == ["freq_too_high_00", "freq_too_low_01"]
    assert metadata["timestamp_utc"]
    assert "sk-test-secret" not in str(metadata)
    assert "example.invalid" not in str(metadata)


def test_run_ablation_outputs_required_metrics():
    report = run_ablation(count=5, max_rounds=4)

    assert report["config"]["case_count"] == 5
    assert report["config"]["proposal_provider"] == "deterministic_proxy"
    assert set(report["groups"]) == {
        "heuristic_only",
        "algorithm_baseline",
        "llm_no_memory",
        "llm_with_memory",
        "llm_memory_reflection",
    }
    for group in report["groups"].values():
        assert 0.0 <= group["success_rate"] <= 1.0
        assert "avg_rounds_to_success" in group
        assert "avg_tokens" in group
        assert "proposal_reject_rate" in group
        assert "fallback_rate" in group
        assert "repeat_error_rate" in group
        assert "memory_recall_hit_rate" in group
        assert "memory_changed_proposal_rate" in group
        assert "memory_compliance_rate" in group
        assert "memory_violation_rate" in group
        assert "memory_enforced_rate" in group
        assert isinstance(group["failure_cases"], list)
        assert len(group["cases"]) == 5


def test_memory_group_records_recall_and_changed_proposals():
    report = run_ablation(
        count=5,
        max_rounds=4,
        group_names=["llm_with_memory", "llm_no_memory"],
        proposal_provider=DeterministicProposalProvider(),
    )
    no_memory = report["groups"]["llm_no_memory"]
    with_memory = report["groups"]["llm_with_memory"]

    assert no_memory["memory_recall_hit_rate"] == 0.0
    assert with_memory["memory_recall_hit_rate"] > 0.0
    assert with_memory["memory_changed_proposal_rate"] > 0.0
    assert with_memory["memory_compliance_rate"] > 0.0
    assert with_memory["memory_violation_rate"] == 0.0
    memory_impacts = [
        round_record["memory_impact"]
        for case in with_memory["cases"]
        for round_record in case["rounds"]
        if round_record["memory_impact"]["used_memory"]
    ]
    assert memory_impacts
    assert any(impact["recalled_memory_ids"] for impact in memory_impacts)
    assert any(impact["memory_expected_param"] == "feed_W" for impact in memory_impacts)


def test_mixed_offset_uses_traceable_joint_candidate_fallback():
    report = run_ablation(count=5, max_rounds=1, group_names=["llm_with_memory"], proposal_provider=DeterministicProposalProvider())
    mixed_case = next(case for case in report["groups"]["llm_with_memory"]["cases"] if case["case"].startswith("mixed_offset"))
    round_record = mixed_case["rounds"][0]

    assert round_record["source"] == "validator_joint_fallback"
    assert len(round_record["updates"]) > 1
    assert round_record["memory_impact"]["fallback_reason"] == "mixed frequency and matching error; joint candidate search selected a better step"
    assert round_record["target_s11_after"] < round_record["target_s11_before"]



def test_fifty_case_memory_benchmark_stays_above_ninety_percent_success():
    report = run_ablation(count=50, max_rounds=6, group_names=["llm_with_memory"], proposal_provider=DeterministicProposalProvider())
    group = report["groups"]["llm_with_memory"]

    assert group["success_rate"] >= 0.9
    assert any(
        round_record["source"] == "validator_joint_fallback"
        for case in group["cases"]
        for round_record in case["rounds"]
    )



def test_memory_constraint_violation_is_enforced_by_fallback():
    class IgnoringProvider:
        name = "ignoring_provider"

        def propose(self, *, params, summary, case, memory, history):
            return {
                "param": "inset_depth",
                "delta_mm": 0.3,
                "reason": "ignore recalled memory",
                "source": "llm",
                "provider": self.name,
                "recalled_memory_ids": ["lesson:0"],
                "recalled_lessons": ["S11 匹配差且 feed_W 过小时，优先增大 feed_W。"],
                "memory_expected_param": "feed_W",
                "memory_expected_delta_sign": 1,
                "memory_constraints": ["当前 feed_W 偏小，必须优先增大 feed_W。"],
                "memory_complied": False,
                "memory_violation": True,
                "memory_violation_reason": "memory expected feed_W, got inset_depth",
                "changed_by_memory": False,
            }

    report = run_ablation(count=4, max_rounds=1, group_names=["llm_with_memory"], proposal_provider=IgnoringProvider())
    group = report["groups"]["llm_with_memory"]
    enforced_rounds = [
        round_record
        for case in group["cases"]
        for round_record in case["rounds"]
        if round_record["memory_impact"]["memory_enforced_by_validator"]
    ]

    assert group["memory_violation_rate"] > 0.0
    assert group["memory_enforced_rate"] > 0.0
    assert enforced_rounds
    assert all(round_record["param"] == "feed_W" for round_record in enforced_rounds)


def test_run_ablation_accepts_custom_proposal_provider():
    class Provider:
        name = "custom_provider"

        def propose(self, *, params, summary, case, memory, history):
            return {
                "param": "patch_L",
                "delta_mm": 0.45 if summary["min_freq_ghz"] > case.target_freq_ghz else -0.45,
                "reason": "custom provider proposal",
                "source": "llm",
                "provider": self.name,
                "usage": {"prompt": 12, "completion": 4},
            }

    report = run_ablation(count=1, max_rounds=1, group_names=["llm_no_memory"], proposal_provider=Provider())
    case = report["groups"]["llm_no_memory"]["cases"][0]

    assert report["config"]["proposal_provider"] == "custom_provider"
    assert case["rounds"][0]["provider"] == "custom_provider"
    assert case["tokens"] == {"prompt": 12, "completion": 4, "total": 16}


def test_run_ablation_writes_incremental_trace_jsonl(tmp_path):
    trace_path = tmp_path / "trace.jsonl"

    run_ablation(count=1, max_rounds=1, group_names=["llm_no_memory"], trace_jsonl=trace_path)

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert events
    assert any(event["event"] == "round_complete" for event in events)
    assert any(event["event"] == "case_complete" for event in events)
    assert any(event["event"] == "group_complete" for event in events)


def test_provider_errors_are_recorded_without_aborting():
    class FailingProvider:
        name = "failing_provider"

        def propose(self, *, params, summary, case, memory, history):
            raise RuntimeError("provider unavailable")

    report = run_ablation(count=1, max_rounds=1, group_names=["llm_no_memory"], proposal_provider=FailingProvider())
    group = report["groups"]["llm_no_memory"]
    case = group["cases"][0]
    round_record = case["rounds"][0]

    assert group["provider_error_count"] == 1
    assert case["provider_errors"] == [{"round": 1, "type": "RuntimeError", "message": "provider unavailable"}]
    assert round_record["provider_error"]["type"] == "RuntimeError"
    assert round_record["source"] == "provider_error"


def test_openai_compatible_provider_adds_memory_and_usage_metadata(monkeypatch):
    captured_calls = []

    def fake_propose(client, model, s11_summary, target_freq_ghz, current_params, **kwargs):
        captured_calls.append(
            {
                "client": client,
                "model": model,
                "s11_summary": s11_summary,
                "target_freq_ghz": target_freq_ghz,
                "current_params": current_params,
                "kwargs": kwargs,
            }
        )
        return ({"param": "feed_W", "delta_mm": 0.2, "reason": "use recalled failure"}, {"prompt": 21, "completion": 8})

    monkeypatch.setattr("cst_agent_workbench.agent.analyzer.propose_patch_optimization_with_llm", fake_propose)
    provider = OpenAICompatibleProposalProvider.__new__(OpenAICompatibleProposalProvider)
    provider.client = object()
    provider.model = "fake-model"
    provider.timeout = 7
    report = run_ablation(count=4, max_rounds=1, group_names=["llm_with_memory"], proposal_provider=provider)
    round_record = next(
        round_record
        for case in report["groups"]["llm_with_memory"]["cases"]
        for round_record in case["rounds"]
        if round_record["memory_impact"]["memory_constraints"]
    )

    assert report["config"]["proposal_provider"] == "openai_compatible"
    assert round_record["provider"] == "openai_compatible"
    assert round_record["memory_impact"]["used_memory"] is True
    matching_call = next(call for call in captured_calls if call["s11_summary"]["at_f0_s11_db"] == round_record["target_s11_before"])
    assert matching_call["client"] is provider.client
    assert matching_call["model"] == "fake-model"
    assert matching_call["target_freq_ghz"] == 9.4
    assert matching_call["kwargs"]["timeout"] == 7
    assert isinstance(matching_call["kwargs"]["failure_reasons"], list)
    assert matching_call["kwargs"]["memory_constraints"] == round_record["memory_impact"]["memory_constraints"]
    assert report["groups"]["llm_with_memory"]["cases"][0]["tokens"] == {"prompt": 21, "completion": 8, "total": 29}


def test_memory_robust_to_misleading_lessons_does_not_degrade_success():
    """B3: 注入与多数 case 状态矛盾的误导性 lesson（"feed_W 过大需继续增大"），
    验证 _memory_guidance 的 feed_w < 2.3 状态守卫阻止错误 lesson 触发 enforce，
    memory 组成功率不显著低于正常 memory 组（状态守卫挡住了错误信号）。
    """
    from benchmarks.agent_ablation_runner import _make_memory

    normal_report = run_ablation(
        count=10,
        max_rounds=4,
        group_names=["llm_with_memory"],
        proposal_provider=DeterministicProposalProvider(),
    )
    robust_report = run_ablation(
        count=10,
        max_rounds=4,
        group_names=["llm_with_memory"],
        proposal_provider=DeterministicProposalProvider(),
        memory_factory=lambda: _make_memory(robust_to_misleading_lessons=True),
    )

    normal_rate = normal_report["groups"]["llm_with_memory"]["success_rate"]
    robust_rate = robust_report["groups"]["llm_with_memory"]["success_rate"]
    # 误导性 lesson 不应让成功率崩盘（状态守卫应挡住），允许最多下降 1 个 case
    assert robust_rate >= normal_rate - 0.1, (
        f"误导性 lesson 导致成功率从 {normal_rate:.0%} 跌到 {robust_rate:.0%}，"
        f"状态守卫未能挡住错误信号"
    )


def test_stdout_summary_omits_full_case_traces():
    report = run_ablation(count=2, max_rounds=2, group_names=["heuristic_only"])
    summary = build_stdout_summary(report, Path("report.json"))

    assert summary["report_path"] == "report.json"
    group_summary = summary["groups"]["heuristic_only"]
    assert "success_rate" in group_summary
    assert "cases" not in group_summary


def test_markdown_summary_includes_metrics_and_memory_delta():
    report = run_ablation(count=5, max_rounds=4)
    markdown = build_markdown_summary(report)

    assert "# Fake CST Agent Ablation Summary" in markdown
    assert "| Group | Success | Avg rounds" in markdown
    assert "llm_no_memory" in markdown
    assert "llm_with_memory" in markdown
    assert "## Memory impact" in markdown
    assert "Success-rate delta" in markdown
    assert "Memory compliance" in markdown
    assert "Memory violation" in markdown
    assert "## Failure cases" in markdown


def test_quality_thresholds_pass_for_default_benchmark():
    report = run_ablation(count=5, max_rounds=4)

    assert validate_quality_thresholds(report) == []


def test_quality_thresholds_report_memory_regression():
    report = {
        "groups": {
            "llm_no_memory": {"success_rate": 0.8},
            "llm_with_memory": {
                "success_rate": 0.4,
                "memory_recall_hit_rate": 0.0,
                "memory_changed_proposal_rate": 0.0,
            },
        }
    }

    failures = validate_quality_thresholds(report)

    assert "llm_with_memory success_rate is lower than llm_no_memory" in failures
    assert "llm_with_memory did not recall memory in any round" in failures
    assert "llm_with_memory did not change any proposal after memory recall" in failures
