"""Verify a public synthetic Model-IR through the real CST Host runtime."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.runtime_state import finish_trace_run, start_optimizer_trace_run
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.model_ir import (
    AcceptanceCriterion,
    AntennaModelIR,
    GeometryOperation,
    MaterialSpec,
    ParameterSpec,
    SimulationObject,
    compile_model_ir,
    confirm_model_ir,
    execute_model_ir,
)
try:
    from benchmarks.cst_solver_evidence import (
        bound_sources_match_commit,
        build_source_bindings,
        repository_commit,
        sha256_path,
    )
except ModuleNotFoundError:  # direct ``python benchmarks/<runner>.py`` execution
    from cst_solver_evidence import (
        bound_sources_match_commit,
        build_source_bindings,
        repository_commit,
        sha256_path,
    )


EXPECTED_SOLIDS = {
    "antenna:ground",
    "antenna:patch",
    "antenna:probe",
    "antenna:substrate",
}
EXPECTED_PARAMETERS = {
    "copper_t": 0.035,
    "feed_y": -8.0,
    "ground_l": 50.0,
    "ground_w": 50.0,
    "patch_l": 28.5,
    "patch_w": 36.0,
    "probe_r": 0.5,
    "slot_l": 12.0,
    "slot_w": 1.5,
    "substrate_h": 1.6,
}


def build_public_slot_patch_ir() -> AntennaModelIR:
    """Return a synthetic fixture unrelated to any unpublished user design."""

    parameters = tuple(
        ParameterSpec(name, str(value), provenance="user_provided")
        for name, value in EXPECTED_PARAMETERS.items()
    )
    geometry = (
        GeometryOperation(
            "ground",
            "brick",
            {
                "name": "ground",
                "component": "antenna",
                "material": "PEC",
                "xmin": "-ground_l/2",
                "xmax": "ground_l/2",
                "ymin": "-ground_w/2",
                "ymax": "ground_w/2",
                "zmin": "0",
                "zmax": "copper_t",
            },
        ),
        GeometryOperation(
            "substrate",
            "brick",
            {
                "name": "substrate",
                "component": "antenna",
                "material": "SyntheticSubstrate",
                "xmin": "-ground_l/2",
                "xmax": "ground_l/2",
                "ymin": "-ground_w/2",
                "ymax": "ground_w/2",
                "zmin": "copper_t",
                "zmax": "copper_t+substrate_h",
            },
            depends_on=("ground",),
        ),
        GeometryOperation(
            "patch",
            "brick",
            {
                "name": "patch",
                "component": "antenna",
                "material": "PEC",
                "xmin": "-patch_l/2",
                "xmax": "patch_l/2",
                "ymin": "-patch_w/2",
                "ymax": "patch_w/2",
                "zmin": "copper_t+substrate_h",
                "zmax": "2*copper_t+substrate_h",
            },
            depends_on=("substrate",),
        ),
        GeometryOperation(
            "slot_cutter",
            "brick",
            {
                "name": "slot_cutter",
                "component": "antenna",
                "material": "PEC",
                "xmin": "-slot_w/2",
                "xmax": "slot_w/2",
                "ymin": "-slot_l/2",
                "ymax": "slot_l/2",
                "zmin": "copper_t+substrate_h-copper_t",
                "zmax": "substrate_h+3*copper_t",
            },
            depends_on=("patch",),
        ),
        GeometryOperation(
            "slot_subtract",
            "boolean_subtract",
            {"obj1": "antenna:patch", "obj2": "antenna:slot_cutter"},
            depends_on=("slot_cutter",),
        ),
        GeometryOperation(
            "probe",
            "cylinder",
            {
                "name": "probe",
                "component": "antenna",
                "material": "PEC",
                "axis": "z",
                "outer_radius": "probe_r",
                "xcenter": "0",
                "ycenter": "feed_y",
                "zmin": "copper_t",
                "zmax": "2*copper_t+substrate_h",
            },
            depends_on=("slot_subtract",),
        ),
    )
    model = AntennaModelIR(
        ir_id="public-synthetic-slot-patch-5p8",
        title="Public synthetic 5.8 GHz slot-loaded patch",
        task_mode="requirement_synthesis",
        source_ids=("public-synthetic-requirement-v1",),
        parameters=parameters,
        materials=(
            MaterialSpec(
                "synthetic-substrate",
                "SyntheticSubstrate",
                {"epsilon": "3.5", "tand": "0.002", "tand_freq": "5.8"},
                provenance="user_provided",
            ),
        ),
        geometry=geometry,
        ports=(
            SimulationObject(
                "port-1",
                "discrete_port",
                {
                    "port_number": 1,
                    "p1_x": "0",
                    "p1_y": "-8",
                    "p1_z": "0.035",
                    "p2_x": "0",
                    "p2_y": "-8",
                    "p2_z": "1.67",
                    "impedance": "50",
                },
            ),
        ),
        boundaries=(SimulationObject("open", "boundary", {}),),
        monitors=(
            SimulationObject(
                "farfield-5p8",
                "farfield",
                {"name": "farfield (f=5.8)", "frequency": "5.8"},
            ),
        ),
        solver=SimulationObject(
            "time-domain-solver",
            "time_domain",
            {"fmin": "4.0", "fmax": "7.0"},
        ),
        acceptance_criteria=(
            AcceptanceCriterion(
                "s11-at-5p8",
                "s11_db",
                "<=",
                -10,
                "dB",
                frequency_ghz=5.8,
            ),
        ),
    )
    return confirm_model_ir(model, confirmed_by="public-fixture-owner")


def _stage(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "success": bool(result.get("success")),
        "message": str(result.get("message") or ""),
        "verification": str(result.get("verification") or ""),
        "raw": dict(result),
    }


def _host_call(agent: Any, tool_name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    agent._active_tool_call_id = call_id
    prompt_result = json.loads(agent._execute_tool(tool_name, arguments))
    return dict(agent.session.artifacts.tool_results.get(call_id) or prompt_result)


def _read_parameters(project_path: Path) -> dict[str, float]:
    parameters_path = project_path.with_suffix("") / "Model" / "Parameters.json"
    payload = json.loads(parameters_path.read_text(encoding="utf-8"))
    return {
        str(item["name"]): float(item["value"])
        for item in payload.get("parameters") or []
        if str(item.get("name") or "") in EXPECTED_PARAMETERS
    }


def run_smoke(
    output_root: Path,
    *,
    controller_factory: Callable[[], Any] = CSTController,
    agent_factory: Callable[[Any], Any] = CSTAgent,
) -> tuple[dict[str, Any], Path]:
    output_root = output_root.resolve()
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST Model-IR smoke 输出必须位于 D 盘")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    project_path = run_dir / "public_slot_patch_model_ir.cst"
    repository_root = Path(__file__).resolve().parents[1]

    environment_keys = ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    previous_tempdir = tempfile.tempdir
    previous_config_temp = config.CST_TEMP_DIR
    previous_config_memory = config.AGENT_MEMORY_DIR

    controller = None
    agent = None
    trace = None
    stages: list[dict[str, Any]] = []
    configuration_report: dict[str, Any] = {}
    configuration_full_results: list[dict[str, Any]] = []
    inventory_before: dict[str, Any] = {}
    inventory_after: dict[str, Any] = {}
    parameters_after_save: dict[str, float] = {}
    model = build_public_slot_patch_ir()
    compiled_plan = compile_model_ir(model)

    try:
        os.environ.update(
            {
                "TEMP": str(temp_dir),
                "TMP": str(temp_dir),
                "CST_TEMP_DIR": str(temp_dir),
                "AGENT_MEMORY_DIR": str(run_dir / "agent_memory"),
            }
        )
        tempfile.tempdir = str(temp_dir)
        config.CST_TEMP_DIR = str(temp_dir)
        config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")

        controller = controller_factory()
        agent = agent_factory(controller)
        trace = start_optimizer_trace_run(
            agent,
            user_input="configure confirmed public synthetic Model-IR in CST",
            working_messages=[],
            pending_history=[],
        )
        if trace is not None:
            trace["turns"].append(
                {
                    "turn_index": 1,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "finished_at": None,
                    "decision_summary": {},
                    "tool_calls": [],
                }
            )

        create_result = _host_call(
            agent,
            "create_cst_project",
            {"project_path": str(project_path)},
            "model-ir-lifecycle-create",
        )
        stages.append(_stage("create_project", create_result))
        workflow_ready = bool(create_result.get("success"))

        if workflow_ready:
            execution_report = execute_model_ir(
                agent,
                model,
                execution_id=f"model-ir-config-{timestamp}",
            )
            configuration_report = execution_report.to_dict()
            for item in execution_report.call_results:
                full_result = dict(
                    agent.session.artifacts.tool_results.get(item.tool_call_id)
                    or item.result
                )
                configuration_full_results.append(full_result)
                stages.append(_stage(item.tool_name, full_result))
            workflow_ready = execution_report.configuration_execution_success is True

        if workflow_ready:
            inventory_before = controller.list_solids(timeout=60)
            stages.append(_stage("inventory_before_save", inventory_before))
            workflow_ready = bool(inventory_before.get("success"))
        if workflow_ready:
            save_result = _host_call(
                agent,
                "save_cst_project",
                {"include_results": False},
                "model-ir-lifecycle-save",
            )
            stages.append(_stage("save_project", save_result))
            workflow_ready = bool(save_result.get("success"))
        if workflow_ready:
            try:
                parameters_after_save = _read_parameters(project_path)
                parameter_result = {
                    "success": parameters_after_save == EXPECTED_PARAMETERS,
                    "message": "read persisted Parameters.json",
                    "parameters": parameters_after_save,
                }
            except Exception as exc:
                parameter_result = {
                    "success": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            stages.append(_stage("parameters_after_save", parameter_result))
            workflow_ready = bool(parameter_result.get("success"))
        if workflow_ready:
            close_result = _host_call(
                agent,
                "save_and_close_cst_project",
                {"include_results": False},
                "model-ir-lifecycle-close",
            )
            stages.append(_stage("save_and_close_project", close_result))
            workflow_ready = bool(close_result.get("success"))
        if workflow_ready:
            reopen_result = _host_call(
                agent,
                "open_cst_project",
                {"project_path": str(project_path)},
                "model-ir-lifecycle-reopen",
            )
            stages.append(_stage("reopen_project", reopen_result))
            workflow_ready = bool(reopen_result.get("success"))
        if workflow_ready:
            inventory_after = controller.list_solids(timeout=60)
            stages.append(_stage("inventory_after_reopen", inventory_after))
            workflow_ready = bool(inventory_after.get("success"))
        if workflow_ready:
            final_close = _host_call(
                agent,
                "save_and_close_cst_project",
                {"include_results": False},
                "model-ir-lifecycle-final-close",
            )
            stages.append(_stage("final_close", final_close))
    except Exception as exc:
        stages.append(
            _stage(
                "unexpected_exception",
                {"success": False, "message": f"{type(exc).__name__}: {exc}"},
            )
        )
    finally:
        if controller is not None and getattr(controller, "project_path", ""):
            try:
                emergency_close = controller.close_project(controller.project_path, timeout=60)
            except Exception as exc:
                emergency_close = {
                    "success": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            stages.append(_stage("emergency_final_close", emergency_close))

        if trace is not None and agent is not None:
            if trace.get("turns"):
                trace["turns"][-1]["finished_at"] = datetime.now(timezone.utc).isoformat()
            finish_trace_run(
                agent,
                status=(
                    "completed"
                    if stages and all(stage["success"] for stage in stages)
                    else "failed"
                ),
                final_response="public synthetic Model-IR configuration smoke finished",
            )

        for key, previous in previous_environment.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        tempfile.tempdir = previous_tempdir
        config.CST_TEMP_DIR = previous_config_temp
        config.AGENT_MEMORY_DIR = previous_config_memory

    names_before = set(map(str, inventory_before.get("solids") or []))
    names_after = set(map(str, inventory_after.get("solids") or []))
    typed_configuration_success = bool(
        configuration_full_results
        and len(configuration_full_results) == len(compiled_plan.calls)
        and all(item.get("success") for item in configuration_full_results)
        and all(item.get("executed") for item in configuration_full_results)
        and all(item.get("verification") == "history_accepted" for item in configuration_full_results)
    )
    workflow_execution_success = bool(
        stages
        and all(stage["success"] for stage in stages)
        and configuration_report.get("configuration_execution_success") is True
        and typed_configuration_success
        and names_before == EXPECTED_SOLIDS
        and names_after == EXPECTED_SOLIDS
        and parameters_after_save == EXPECTED_PARAMETERS
        and project_path.is_file()
        and controller is not None
        and not getattr(controller, "project_path", "")
    )

    source_paths = {
        "runner": "benchmarks/cst_model_ir_configuration_smoke.py",
        "model_ir_models": "cst_agent_workbench/model_ir/models.py",
        "model_ir_validation": "cst_agent_workbench/model_ir/validation.py",
        "model_ir_compiler": "cst_agent_workbench/model_ir/compiler.py",
        "model_ir_executor": "cst_agent_workbench/model_ir/executor.py",
        "tool_runtime": "cst_agent_workbench/agent/tool_runtime.py",
        "tool_contracts": "cst_agent_workbench/agent/tool_contracts.py",
        "primitives": "cst_agent_workbench/cst/primitives.py",
        "controller": "cst_agent_workbench/cst/controller.py",
    }
    commit = repository_commit(repository_root)
    source_bindings = build_source_bindings(
        repository_root,
        source_paths,
        snapshot_root=run_dir / "source_snapshot",
    )
    source_snapshot_complete = all(
        Path(str(binding.get("snapshot_file") or "")).is_file()
        and sha256_path(Path(str(binding["snapshot_file"])))
        == binding.get("snapshot_sha256")
        for binding in source_bindings.values()
    )
    execution_evidence_success = workflow_execution_success and source_snapshot_complete
    report = {
        "schema_version": "cst-model-ir-configuration-smoke-v1",
        "run_id": timestamp,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixture": {
            "classification": "public_synthetic_requirement",
            "unpublished_user_work_used": False,
            "model_ir": model.to_dict(),
            "compiled_plan": compiled_plan.to_dict(),
        },
        "project_file": str(project_path),
        "project_exists": project_path.is_file(),
        "project_sha256": sha256_path(project_path) if project_path.is_file() else "",
        "python_command": list(getattr(controller, "cst_python_command", []) or []),
        "repository_commit": commit,
        "source_bindings": source_bindings,
        "bound_sources_match_commit": bound_sources_match_commit(
            repository_root, commit, source_bindings
        ),
        "source_snapshot_complete": source_snapshot_complete,
        "source_provenance_note": (
            "Every bound source is archived in this D-drive evidence directory; "
            "commit_blob_match identifies which bytes equal repository_commit."
        ),
        "configuration_report": configuration_report,
        "configuration_full_results": configuration_full_results,
        "expected_solids": sorted(EXPECTED_SOLIDS),
        "inventory_before_save": sorted(names_before),
        "inventory_after_reopen": sorted(names_after),
        "expected_parameters": EXPECTED_PARAMETERS,
        "parameters_after_save": parameters_after_save,
        "stages": stages,
        "trace": (
            agent.trace_history[-1]
            if agent is not None and getattr(agent, "trace_history", None)
            else {}
        ),
        "controller_final_project_path": (
            str(getattr(controller, "project_path", "")) if controller is not None else ""
        ),
        "success_contract": {
            "compile_success": configuration_report.get("compile_success"),
            "configuration_execution_success": configuration_report.get(
                "configuration_execution_success"
            ),
            "typed_configuration_history_accepted": typed_configuration_success,
            "persisted_parameter_binding_success": parameters_after_save
            == EXPECTED_PARAMETERS,
            "save_close_reopen_inventory_success": names_before
            == EXPECTED_SOLIDS
            == names_after,
            "cleanup_success": bool(
                controller is not None and not getattr(controller, "project_path", "")
            ),
            "workflow_execution_success": workflow_execution_success,
            "execution_evidence_success": execution_evidence_success,
            "solver_execution_success": None,
            "typed_result_success": None,
            "physical_target_success": None,
        },
        "verification_semantics": {
            "history_accepted": "CST accepted each canonical typed configuration command into History.",
            "persisted_parameters": "Saved Parameters.json contains the exact synthetic parameter values.",
            "solid_inventory": "Official Solid.GetNameOfShapeFromIndex inventory matches before save and after reopen.",
            "boundary": "No solver, S-parameter, convergence, port validity, or electromagnetic target is claimed.",
        },
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/model_ir_configuration"),
    )
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root)
    success = report["success_contract"]["execution_evidence_success"]
    print(json.dumps({"success": success, "report": str(report_path)}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
