from cst_agent_workbench.optimization.report import (
    build_patch_optimization_markdown,
    build_patch_optimization_report,
    build_round_record,
    build_s11_check,
)


def test_build_s11_check_includes_target_and_bandwidth_metadata():
    check = build_s11_check(
        {
            "success": True,
            "plot_data": [
                {"freq": 9.0, "s_db": -5.0},
                {"freq": 9.4, "s_db": -12.0},
                {"freq": 9.8, "s_db": -6.0},
            ],
        },
        mode="at_f0",
        target_db=-10.0,
        effective_target_freq=9.4,
        configured_target_freq=9.4,
    )

    assert check["met"] is True
    assert check["data_success"] is True
    assert check["min_s11"] == -12.0
    assert check["at_f0_s11"] == -12.0
    assert check["plot_point_count"] == 3
    assert check["target_freq_ghz"] == 9.4
    assert check["resonance_count"] >= 1
    assert check["resonances"]


def test_patch_optimization_markdown_records_memory_validator_trace():
    round_record = build_round_record(
        round_index=1,
        optimizer_result={
            "success": True,
            "strategy": "llm",
            "proposal_reason": "[LLM] 根据记忆调整。",
            "changed_params": {"feed_W": {"old": "1", "new": "1.35"}},
            "message": "done",
            "rolled_back": True,
            "rollback_reason": "worse metric",
            "diagnosis": {
                "resonance_shift": "too_high",
                "matching_quality": "shallow",
                "recommended_parameter_family": "patch_L",
                "recommended_delta_sign": 1,
                "reason": "谐振偏高，应优先增大 patch_L。",
            },
            "attempted_check": {
                "criteria_text": "S11 at 9.4 GHz <= -10.0 dB",
                "status_text": "S11@9.4GHz = -9.00 dB | FAIL",
                "min_s11": -14.0,
                "min_freq": 9.55,
                "at_f0_s11": -9.0,
                "met": False,
            },
        },
        check={
            "criteria_text": "S11 at 9.4 GHz <= -10.0 dB",
            "status_text": "S11@9.4GHz = -11.20 dB | PASS",
            "min_s11": -13.0,
            "min_freq": 9.41,
            "at_f0_s11": -11.2,
            "met": True,
            "plot_point_count": 2,
        },
        param_snapshot={"f0": "9.4", "feed_W": "1.35", "patch_L": "10"},
        memory_recall=[{"id": "m1", "entry_type": "failure", "text": "feed_W 过小会导致 S11 继续恶化"}],
        memory_impact={
            "memory_constraints": ["当前 feed_W=1.000 mm 偏小；必须优先增大 feed_W。"],
            "memory_violation": True,
            "memory_violation_reason": "memory expected feed_W, got patch_L",
            "memory_enforced_by_validator": True,
            "proposal_before_validation": {"param": "patch_L", "delta_mm": -0.6, "reason": "bad"},
            "proposal_after_validation": {"param": "feed_W", "delta_mm": 0.35, "reason": "memory constraint fallback"},
            "llm_parse_error": "no_parseable_optimization_proposal",
            "llm_raw_excerpt": "建议：先调 feed_W，但我没有按 JSON 输出。",
        },
        tool_events=[
            {
                "phase": "1_环境与材料",
                "tool_name": "programmatic_patch_proposal",
                "success": True,
                "description": "程序化优化策略(llm): memory fallback",
            }
        ],
    )
    report = build_patch_optimization_report(
        request={
            "f0_ghz": 9.4,
            "substrate_name": "Rogers5880",
            "epsilon_r": 2.2,
            "loss_tangent": 0.0009,
            "substrate_thickness_mm": 1.6,
            "conductor_name": "Copper (annealed)",
            "conductor_thickness_mm": 0.035,
            "feed_strategy": "microstrip",
        },
        target={"mode": "at_f0", "target_db": -10.0, "effective_target_freq_ghz": 9.4},
        connect_result={"success": True, "message": "connected"},
        project_path="D:/demo/project.cst",
        build_message="built",
        build_success=True,
        baseline_check={
            "criteria_text": "S11 at 9.4 GHz <= -10.0 dB",
            "status_text": "S11@9.4GHz = -8.00 dB | FAIL",
            "min_s11": -12.0,
            "min_freq": 9.8,
            "at_f0_s11": -8.0,
            "plot_point_count": 2,
        },
        baseline_params={"f0": "9.4", "feed_W": "1", "patch_L": "10"},
        rounds=[round_record],
        final_check=round_record["check"],
        token_stats={"prompt": 5, "completion": 3, "calls": 1},
        timestamp_utc="2026-04-30T00:00:00+00:00",
        benchmark={
            "command": "patch-matrix",
            "case_name": "rogers5880_9p4_microstrip",
            "execution_mode": "cli_patch_matrix_rogers5880_9p4_microstrip",
            "git_sha": "abc123",
            "cst_mode": "fake",
            "runtime_sec": 1.25,
            "optimization_supported": True,
            "executed_rounds": 1,
        },
    )

    markdown = build_patch_optimization_markdown(report)

    assert "# CST Patch Optimization Closed-Loop Report" in markdown
    assert "## Benchmark evidence" in markdown
    assert "patch_benchmark_v2" in markdown
    assert "patch-matrix" in markdown
    assert "rogers5880_9p4_microstrip" in markdown
    assert "abc123" in markdown
    assert "CST mode: `fake`" in markdown
    assert "Runtime: 1.25 s" in markdown
    assert "Executed rounds: 1" in markdown
    assert "### Round 1" in markdown
    assert "`feed_W` 1 → 1.35" in markdown
    assert "Memory recalled: yes" in markdown
    assert "Diagnosis: too_high -> patch_L; 谐振偏高，应优先增大 patch_L。" in markdown
    assert "Attempted result before rollback: S11@9.4GHz = -9.00 dB | FAIL" in markdown
    assert "resonances=-" in markdown
    assert "Recommended parameter family: patch_L" in markdown
    assert "Validator enforced memory: yes" in markdown
    assert "Memory violation: yes — memory expected feed_W, got patch_L" in markdown
    assert "feed_W 过小会导致 S11 继续恶化" in markdown
    assert "Proposal before validation: `patch_L` -0.6000 mm" in markdown
    assert "Proposal after validation: `feed_W` +0.3500 mm" in markdown
    assert "LLM parse error: no_parseable_optimization_proposal" in markdown
    assert "建议：先调 feed_W" in markdown
    assert "Prompt tokens: 5" in markdown
