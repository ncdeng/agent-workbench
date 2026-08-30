import json

import pytest

from cst_agent_workbench import cli
from cst_agent_workbench.cli import build_parser, main


def test_build_patch_requires_dry_run(tmp_path):
    output_path = tmp_path / "generated.vba"

    with pytest.raises(SystemExit) as exc:
        main([
            "build-patch",
            "--f0", "9.4",
            "--er", "2.2",
            "--h", "1.6",
            "--export-vba", str(output_path),
        ])

    assert "--dry-run" in str(exc.value)
    assert not output_path.exists()


def test_build_patch_dry_run_exports_vba(tmp_path, capsys):
    output_path = tmp_path / "generated.vba"

    code = main([
        "build-patch",
        "--dry-run",
        "--f0", "9.4",
        "--er", "2.2",
        "--h", "1.6",
        "--loss", "0.0009",
        "--material", "Rogers5880",
        "--export-vba", str(output_path),
    ])

    assert code == 0
    assert output_path.exists()
    vba = output_path.read_text(encoding="utf-8")
    assert 'StoreParameter "f0", "9.4"' in vba
    assert "With Brick" in vba
    assert "MS_WG_Port_1" in vba
    assert '.FieldType "Farfield"' in vba
    stdout = capsys.readouterr().out
    assert "dry-run" in stdout
    assert str(output_path) in stdout
    assert "patch_W=" in stdout


def test_build_patch_dry_run_exports_probe_vba(tmp_path, capsys):
    output_path = tmp_path / "probe.vba"

    code = main([
        "build-patch",
        "--dry-run",
        "--feed", "probe",
        "--f0", "9.4",
        "--er", "2.2",
        "--h", "1.6",
        "--loss", "0.0009",
        "--material", "Rogers5880",
        "--export-vba", str(output_path),
    ])

    assert code == 0
    vba = output_path.read_text(encoding="utf-8")
    assert 'StoreParameter "probe_R"' in vba
    assert 'StoreParameter "probe_Y"' in vba
    assert "With DiscretePort" in vba
    stdout = capsys.readouterr().out
    assert "probe_R=" in stdout
    assert "probe_Y=" in stdout



def test_patch_matrix_parser_defaults():
    args = build_parser().parse_args(["patch-matrix"])

    assert args.command == "patch-matrix"
    assert args.cases == ""
    assert args.microstrip_rounds == 3
    assert args.target_mode == "at_f0"



def test_patch_l_sweep_parser_defaults():
    args = build_parser().parse_args(["patch-l-sweep", "--f0", "9.4", "--er", "2.2", "--h", "1.6"])

    assert args.command == "patch-l-sweep"
    assert args.param == "patch_L"
    assert args.feed == "microstrip"
    assert args.steps == 9
    assert args.span_pct == 6.0
    assert args.target_mode == "at_f0"



def test_patch_param_sweep_parser_supports_matching_params():
    args = build_parser().parse_args([
        "patch-param-sweep",
        "--param", "inset_depth",
        "--f0", "9.4",
        "--er", "2.2",
        "--h", "1.6",
    ])

    assert args.command == "patch-param-sweep"
    assert args.param == "inset_depth"
    assert args.feed == "microstrip"
    assert args.steps == 7
    assert args.span_pct == 30.0



def test_patch_l_sweep_values_include_center_for_percentage_sweep():
    values = cli._patch_l_sweep_values(10.0, steps=4, span_pct=10.0)

    assert values == [9.0, 9.6667, 10.0, 10.3333, 11.0]



def test_patch_l_sweep_values_respect_explicit_values():
    values = cli._patch_l_sweep_values(10.0, steps=9, span_pct=6.0, explicit_values="9.7,10.1")

    assert values == [9.7, 10.1]



def test_patch_param_update_values_adjusts_notch_width_for_feed_width():
    updates = cli._patch_param_update_values("feed_W", 5.2, {"inset_gap": "0.64"})

    assert updates == {"feed_W": 5.2, "notch_W": 6.48}



def test_select_matrix_cases_rejects_unknown_case():
    with pytest.raises(SystemExit) as exc:
        cli._select_matrix_cases("missing_case")

    assert "未知 matrix case" in str(exc.value)



def test_patch_matrix_runs_selected_cases_without_cst(monkeypatch, tmp_path):
    class FakeController:
        project_path = "D:/fake/matrix.cst"

        def connect(self):
            return {"success": True, "message": "fake connected"}

    def fake_execute_patch_case(**kwargs):
        request = kwargs["request"]
        max_rounds = kwargs["max_rounds"]
        final_value = -11.0 if request.feed_strategy == "probe" else -9.0
        check = {
            "criteria_text": f"S11 at {request.f0_ghz} GHz <= -10 dB",
            "status_text": f"S11@{request.f0_ghz}GHz = {final_value:.2f} dB | {'PASS' if final_value <= -10 else 'FAIL'}",
            "data_success": True,
            "min_s11": final_value,
            "min_freq": request.f0_ghz,
            "at_f0_s11": final_value,
            "bandwidth_ghz": 0.25,
            "bandwidth_pct": 2.5,
            "resonances": [{"freq_ghz": request.f0_ghz, "depth_db": final_value}],
            "resonance_count": 1,
            "plot_point_count": 3,
            "met": final_value <= -10,
        }
        rounds = []
        if request.feed_strategy == "microstrip" and max_rounds:
            rounds.append({
                "round": 1,
                "success": True,
                "strategy": "fake",
                "proposal_reason": "test",
                "changed_params": {},
                "rolled_back": True,
                "rollback_reason": "test rollback",
                "check": check,
                "param_snapshot": {},
                "memory_recall": [],
                "memory_impact": {"llm_parse_error": "fake_parse"},
                "tool_events": [],
            })
        return cli.build_patch_optimization_report(
            request=cli._patch_request_to_dict(request),
            target={
                "mode": kwargs["target_mode"],
                "target_db": kwargs["target_db"],
                "configured_target_freq_ghz": kwargs["target_freq"],
                "effective_target_freq_ghz": request.f0_ghz,
                "max_rounds": max_rounds,
                "executed_max_rounds": max_rounds if request.feed_strategy == "microstrip" else 0,
                "optimization_supported": request.feed_strategy == "microstrip",
            },
            connect_result=kwargs["connect_result"],
            project_path=kwargs["cst"].project_path,
            build_message="fake build",
            build_success=True,
            baseline_check=check,
            baseline_params={},
            rounds=rounds,
            final_check=check,
            token_stats={},
            benchmark={
                "command": kwargs.get("command_name", "patch-matrix"),
                "case_name": kwargs.get("case_name", request.feed_strategy),
                "execution_mode": kwargs.get("execution_mode", "test"),
                "git_sha": "test-sha",
                "cst_mode": "fake",
                "runtime_sec": 0.5,
                "optimization_supported": request.feed_strategy == "microstrip",
                "executed_rounds": len(rounds),
            },
        )

    monkeypatch.setattr("cst_agent_workbench.cst.controller.CSTController", FakeController)
    monkeypatch.setattr(cli, "_execute_patch_case", fake_execute_patch_case)

    output_dir = tmp_path / "reports"
    summary_path = tmp_path / "summary.md"
    json_path = tmp_path / "summary.json"
    code = main([
        "patch-matrix",
        "--cases", "rogers5880_9p4_microstrip,rogers5880_9p4_probe",
        "--output-dir", str(output_dir),
        "--summary", str(summary_path),
        "--json-output", str(json_path),
        "--microstrip-rounds", "1",
    ])

    assert code == 0
    assert (output_dir / "rogers5880_9p4_microstrip.md").exists()
    assert (output_dir / "rogers5880_9p4_microstrip.json").exists()
    assert (output_dir / "rogers5880_9p4_probe.md").exists()
    assert (output_dir / "rogers5880_9p4_probe.json").exists()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Real CST Patch Evaluation Matrix" in summary_text
    assert "rogers5880_9p4_microstrip" in summary_text
    assert "rogers5880_9p4_probe" in summary_text
    assert "Build" in summary_text
    assert "S11 data" in summary_text
    assert "R/RB/PE" in summary_text
    assert "Diagnosis" in summary_text
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert [case["name"] for case in data["cases"]] == ["rogers5880_9p4_microstrip", "rogers5880_9p4_probe"]
    first = data["cases"][0]
    assert "baseline_diagnosis" in first
    assert "final_diagnosis" in first
    assert first["schema_version"] == "patch_benchmark_v2"
    assert first["build_success"] is True
    assert first["baseline_s11_success"] is True
    assert first["final_s11_success"] is True
    assert first["optimization_supported"] is True
    assert first["optimization_attempted"] is True
    assert first["target_met"] is False
    assert first["baseline_min_freq_ghz"] == 9.4
    assert first["final_target_s11_db"] == -9.0
    assert first["final_bandwidth_pct"] == 2.5
    assert first["report_json_path"].endswith("rogers5880_9p4_microstrip.json")
    assert first["rolled_back_rounds"] == 1
    assert first["llm_parse_errors"] == 1
    case_json = json.loads((output_dir / "rogers5880_9p4_microstrip.json").read_text(encoding="utf-8"))
    assert case_json["schema_version"] == "patch_benchmark_v2"
    assert case_json["benchmark"]["command"] == "patch-matrix"



def test_optimize_patch_report_writes_optional_json_without_cst(monkeypatch, tmp_path):
    class FakeController:
        project_path = "D:/fake/opt.cst"

        def connect(self):
            return {"success": True, "message": "fake connected", "mode": "fake"}

    def fake_execute_patch_case(**kwargs):
        request = kwargs["request"]
        check = {
            "criteria_text": f"S11 at {request.f0_ghz} GHz <= -10 dB",
            "status_text": f"S11@{request.f0_ghz}GHz = -11.00 dB | PASS",
            "data_success": True,
            "min_s11": -12.0,
            "min_freq": request.f0_ghz,
            "at_f0_s11": -11.0,
            "bandwidth_ghz": 0.25,
            "bandwidth_pct": 2.5,
            "resonances": [{"freq_ghz": request.f0_ghz, "depth_db": -12.0}],
            "resonance_count": 1,
            "plot_point_count": 3,
            "met": True,
        }
        return cli.build_patch_optimization_report(
            request=cli._patch_request_to_dict(request),
            target={
                "mode": kwargs["target_mode"],
                "target_db": kwargs["target_db"],
                "configured_target_freq_ghz": kwargs["target_freq"],
                "effective_target_freq_ghz": request.f0_ghz,
                "max_rounds": kwargs["max_rounds"],
                "executed_max_rounds": 0,
                "optimization_supported": request.feed_strategy == "microstrip",
            },
            connect_result=kwargs["connect_result"],
            project_path=kwargs["cst"].project_path,
            build_message="fake build",
            build_success=True,
            baseline_check=check,
            baseline_params={},
            rounds=[],
            final_check=check,
            token_stats={},
            benchmark={
                "command": "optimize-patch-report",
                "case_name": "",
                "execution_mode": kwargs.get("execution_mode", "test"),
                "git_sha": "test-sha",
                "cst_mode": "fake",
                "runtime_sec": 0.25,
                "optimization_supported": True,
                "executed_rounds": 0,
            },
        )

    monkeypatch.setattr("cst_agent_workbench.cst.controller.CSTController", FakeController)
    monkeypatch.setattr(cli, "_execute_patch_case", fake_execute_patch_case)

    output_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"
    code = main([
        "optimize-patch-report",
        "--f0", "9.4",
        "--er", "2.2",
        "--h", "1.6",
        "--loss", "0.0009",
        "--material", "Rogers5880",
        "--max-rounds", "0",
        "--output", str(output_path),
        "--json-output", str(json_path),
    ])

    assert code == 0
    assert "Benchmark evidence" in output_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "patch_benchmark_v2"
    assert data["benchmark"]["command"] == "optimize-patch-report"



def test_patch_l_sweep_runs_without_cst(monkeypatch, tmp_path):
    class FakeController:
        project_path = "D:/fake/sweep.cst"

        def connect(self):
            return {"success": True, "message": "fake connected"}

    def fake_execute_patch_l_sweep(**kwargs):
        request = kwargs["request"]
        target_freq = request.f0_ghz
        check_1 = {
            "criteria_text": f"S11 at {target_freq} GHz <= -10 dB",
            "status_text": f"S11@{target_freq}GHz = -6.00 dB | FAIL",
            "min_s11": -14.0,
            "min_freq": 9.68,
            "at_f0_s11": -6.0,
            "met": False,
            "plot_point_count": 3,
            "resonances": [{"freq_ghz": 9.68, "depth_db": -14.0}],
        }
        check_2 = {
            "criteria_text": f"S11 at {target_freq} GHz <= -10 dB",
            "status_text": f"S11@{target_freq}GHz = -11.00 dB | PASS",
            "min_s11": -13.0,
            "min_freq": 9.42,
            "at_f0_s11": -11.0,
            "met": True,
            "plot_point_count": 3,
            "resonances": [{"freq_ghz": 9.42, "depth_db": -13.0}],
        }
        entries = [
            cli._patch_l_sweep_entry(
                index=1,
                patch_l_mm=9.7,
                baseline_patch_l_mm=9.7,
                target_freq_ghz=target_freq,
                check=check_1,
                update_result={"success": True, "message": "baseline cached"},
                solver_result={"success": True, "message": "baseline cached"},
            ),
            cli._patch_l_sweep_entry(
                index=2,
                patch_l_mm=9.9,
                baseline_patch_l_mm=9.7,
                target_freq_ghz=target_freq,
                check=check_2,
                update_result={"success": True, "message": "updated"},
                solver_result={"success": True, "message": "solved"},
            ),
        ]
        return {
            "timestamp_utc": "2026-05-05T00:00:00Z",
            "request": cli._patch_request_to_dict(request),
            "target": {
                "mode": kwargs["target_mode"],
                "target_db": kwargs["target_db"],
                "configured_target_freq_ghz": kwargs["target_freq"],
                "effective_target_freq_ghz": target_freq,
            },
            "connect": kwargs["connect_result"],
            "project_path": kwargs["cst"].project_path,
            "build": {"success": True, "message": "fake build"},
            "baseline": {
                "patch_l_mm": 9.7,
                "parameter": kwargs.get("sweep_param", "patch_L"),
                "value_mm": 9.7,
                "check": check_1,
                "param_snapshot": {"patch_L": "9.7"},
            },
            "sweep": {
                "parameter": kwargs.get("sweep_param", "patch_L"),
                "center_patch_l_mm": 9.7,
                "center_value_mm": 9.7,
                "span_pct": kwargs["span_pct"],
                "steps": kwargs["steps"],
                "explicit_values": kwargs["values"],
                "entries": entries,
            },
            "best": cli._best_patch_l_sweep_entries(entries),
            "token_stats": {},
        }

    monkeypatch.setattr("cst_agent_workbench.cst.controller.CSTController", FakeController)
    monkeypatch.setattr(cli, "_execute_patch_l_sweep", fake_execute_patch_l_sweep)

    output_path = tmp_path / "sweep.md"
    json_path = tmp_path / "sweep.json"
    code = main([
        "patch-l-sweep",
        "--f0", "9.4",
        "--er", "2.2",
        "--h", "1.6",
        "--output", str(output_path),
        "--json-output", str(json_path),
        "--steps", "5",
        "--span-pct", "4",
    ])

    assert code == 0
    markdown = output_path.read_text(encoding="utf-8")
    assert "# CST patch_L Sweep Report" in markdown
    assert "Best target-frequency S11" in markdown
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data["sweep"]["entries"]) == 2
    assert data["best"]["by_target_s11"]["patch_l_mm"] == 9.9



def test_patch_param_sweep_runs_without_cst(monkeypatch, tmp_path):
    class FakeController:
        project_path = "D:/fake/sweep.cst"

        def connect(self):
            return {"success": True, "message": "fake connected"}

    captured = {}

    def fake_execute_patch_l_sweep(**kwargs):
        captured.update(kwargs)
        check = {
            "criteria_text": "S11 at 9.4 GHz <= -10 dB",
            "status_text": "S11@9.4GHz = -10.50 dB | PASS",
            "min_s11": -12.0,
            "min_freq": 9.5,
            "at_f0_s11": -10.5,
            "met": True,
            "plot_point_count": 3,
            "resonances": [{"freq_ghz": 9.5, "depth_db": -12.0}],
        }
        entries = [
            cli._patch_l_sweep_entry(
                index=1,
                patch_l_mm=4.0,
                baseline_patch_l_mm=3.5,
                target_freq_ghz=9.4,
                check=check,
                update_result={"success": True, "message": "updated"},
                solver_result={"success": True, "message": "solved"},
                param_name=kwargs["sweep_param"],
            )
        ]
        return {
            "timestamp_utc": "2026-05-05T00:00:00Z",
            "request": cli._patch_request_to_dict(kwargs["request"]),
            "target": {"mode": "at_f0", "target_db": -10.0, "configured_target_freq_ghz": 0.0, "effective_target_freq_ghz": 9.4},
            "connect": kwargs["connect_result"],
            "project_path": kwargs["cst"].project_path,
            "build": {"success": True, "message": "fake build"},
            "baseline": {"parameter": kwargs["sweep_param"], "value_mm": 3.5, "check": check, "param_snapshot": {"inset_depth": "3.5"}},
            "sweep": {"parameter": kwargs["sweep_param"], "center_value_mm": 3.5, "span_pct": kwargs["span_pct"], "steps": kwargs["steps"], "explicit_values": kwargs["values"], "entries": entries},
            "best": cli._best_patch_l_sweep_entries(entries),
            "token_stats": {},
        }

    monkeypatch.setattr("cst_agent_workbench.cst.controller.CSTController", FakeController)
    monkeypatch.setattr(cli, "_execute_patch_l_sweep", fake_execute_patch_l_sweep)

    output_path = tmp_path / "inset.md"
    code = main([
        "patch-param-sweep",
        "--param", "inset_depth",
        "--f0", "9.4",
        "--er", "2.2",
        "--h", "1.6",
        "--output", str(output_path),
        "--json-output", "",
    ])

    assert code == 0
    assert captured["sweep_param"] == "inset_depth"
    markdown = output_path.read_text(encoding="utf-8")
    assert "# CST inset_depth Sweep Report" in markdown
    assert "| # | inset_depth mm" in markdown



def test_patch_l_sweep_requires_real_cst_connection(monkeypatch, tmp_path):
    output_path = tmp_path / "sweep.md"

    class FakeController:
        def connect(self):
            return {"success": False, "message": "not installed"}

    monkeypatch.setattr("cst_agent_workbench.cst.controller.CSTController", FakeController)

    with pytest.raises(SystemExit) as exc:
        main([
            "patch-l-sweep",
            "--f0", "9.4",
            "--er", "2.2",
            "--h", "1.6",
            "--output", str(output_path),
        ])

    assert "CST 连接失败" in str(exc.value)
    assert not output_path.exists()



def test_optimize_patch_report_requires_real_cst_connection(monkeypatch, tmp_path):
    output_path = tmp_path / "report.md"

    class FakeController:
        def connect(self):
            return {"success": False, "message": "not installed"}

    monkeypatch.setattr("cst_agent_workbench.cst.controller.CSTController", FakeController)

    with pytest.raises(SystemExit) as exc:
        main([
            "optimize-patch-report",
            "--f0", "9.4",
            "--er", "2.2",
            "--h", "1.6",
            "--output", str(output_path),
        ])

    assert "CST 连接失败" in str(exc.value)
    assert not output_path.exists()
