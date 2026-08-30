"""Build the developer-visible 40-case Agent E2E frozen-evaluation dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v1.json"

ONLINE_FACT = [
    "fake 在线",
    "CST（在线）",
    "CST (在线)",
    "connected to CST",
    "CST is connected",
]
OFFLINE_FACT = ["fake 离线", "CST 未连接", "CST（离线）", "CST (离线)"]


def _turn(
    content: str,
    *,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": content,
        "tool_name": tool_name,
        "tool_arguments": dict(arguments or {}),
    }
    if tool_calls is not None:
        result["tool_calls"] = tool_calls
    if constraints:
        result["planner_constraints"] = constraints
    return result


def _fixture(
    *,
    connected: bool = True,
    lessons: list[str] | None = None,
    tool_memory: list[dict[str, Any]] | None = None,
    failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "connected": connected,
        "structured_lessons": list(lessons or []),
        "tool_use_memory": list(tool_memory or []),
        "injected_failures": list(failures or []),
    }


def _oracle(
    *,
    allowed: list[str],
    sequences: list[list[str]],
    facts: list[Any],
    required: list[str] | None = None,
    forbidden: list[str] | None = None,
    recovery: str | None = None,
    expected_arguments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "allowed_tools": allowed,
        "required_tools_all": list(required if required is not None else allowed),
        "forbidden_tools": list(forbidden or []),
        "valid_tool_sequences": sequences,
        "required_grounding_facts": facts,
        "expected_recovery": recovery,
        "expected_tool_arguments": list(expected_arguments or []),
        "max_tool_calls": max((len(sequence) for sequence in sequences), default=0),
    }


def _case(
    *,
    case_id: str,
    category: str,
    scenario: str,
    design: str,
    failure: str,
    seed: int,
    turns: list[dict[str, Any]],
    fixture: dict[str, Any],
    oracle: dict[str, Any],
    tags: list[str],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "scenario_family": scenario,
        "design_family": design,
        "failure_family": failure,
        "seed": seed,
        "turns": turns,
        "fixture": fixture,
        "oracle": oracle,
        "tags": tags,
    }


def _tool_call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "arguments": dict(arguments or {})}


def build_dataset() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    seed = 61000

    for index, language in enumerate(("zh", "en", "mixed", "zh"), start=1):
        seed += 1
        marker = f"STATUS-{index}"
        connected = index not in {2, 4}
        if language == "en":
            prompt = f"Audit {marker}: check whether CST is connected using only the read-only status tool."
        elif language == "mixed":
            prompt = f"执行 {marker} status audit，只调用只读连接状态工具。"
        else:
            prompt = f"执行 {marker} 状态审计，只调用只读连接状态工具检查 CST 是否在线。"
        cases.append(
            _case(
                case_id=f"status_read_{index:02d}",
                category="groundedness",
                scenario="status_read",
                design="no_design",
                failure="none" if connected else "offline_state",
                seed=seed,
                turns=[_turn(prompt, tool_name="check_cst_status")],
                fixture=_fixture(connected=connected),
                oracle=_oracle(
                    allowed=["check_cst_status"],
                    sequences=[["check_cst_status"]],
                    facts=[marker, ONLINE_FACT if connected else OFFLINE_FACT],
                    forbidden=["run_solver", "execute_vba_script"],
                ),
                tags=[language, "read_only", "status"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"SAFE-STATUS-{index}"
        cases.append(
            _case(
                case_id=f"status_constraints_{index:02d}",
                category="planning",
                scenario="status_read_with_constraints",
                design="no_design",
                failure="none",
                seed=seed,
                turns=[
                    _turn(
                        f"{marker}：只检查 CST 状态；禁止求解、VBA 和任何建模修改。",
                        tool_name="check_cst_status",
                        constraints=["禁止求解", "禁止 VBA", "禁止建模修改"],
                    )
                ],
                fixture=_fixture(),
                oracle=_oracle(
                    allowed=["check_cst_status"],
                    sequences=[["check_cst_status"]],
                    facts=[marker, ONLINE_FACT],
                    forbidden=["run_solver", "execute_vba_script", "create_brick"],
                ),
                tags=["hard_constraint", "read_only"],
            )
        )

    no_tool_prompts = (
        "记录审计代号 {marker}，本轮只确认，不得调用任何工具。",
        "用户取消了 {marker}，只回复已取消，不得调用工具。",
        "对 {marker} 先提出澄清，不执行 CST 操作，也不调用工具。",
        "暂缓 {marker}；仅确认等待下一步，不得调用任何工具。",
    )
    for index, prompt_template in enumerate(no_tool_prompts, start=1):
        seed += 1
        marker = f"NO-TOOL-{index}"
        cases.append(
            _case(
                case_id=f"no_tool_{index:02d}",
                category="planning",
                scenario="no_tool_acknowledgement",
                design="no_design",
                failure="none",
                seed=seed,
                turns=[_turn(prompt_template.format(marker=marker), constraints=["不得调用任何工具"])],
                fixture=_fixture(),
                oracle=_oracle(
                    allowed=[],
                    required=[],
                    sequences=[[]],
                    facts=[marker],
                    forbidden=["check_cst_status", "run_solver", "execute_vba_script"],
                ),
                tags=["no_tool", "cancellation_or_clarification"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"MEM-{index}"
        lesson = "状态审计必须优先使用 check_cst_status，避免 VBA 和求解器。"
        tool_memory = [
            {
                "task_signature": "CST 状态审计",
                "selected_tools": ["check_cst_status"],
                "success": True,
                "confidence": 0.95,
                "corrective_hint": "Use the read-only status tool first.",
            },
            {
                "task_signature": "CST 状态审计",
                "selected_tools": ["execute_vba_script"],
                "success": False,
                "confidence": 0.9,
                "failure_reason": "VBA is unnecessary for status checks.",
                "corrective_hint": "Avoid mutating tools for status checks.",
            },
        ]
        cases.append(
            _case(
                case_id=f"memory_guided_{index:02d}",
                category="tool_selection",
                scenario="memory_guided_status",
                design="no_design",
                failure="none",
                seed=seed,
                turns=[
                    _turn(
                        f"{marker}：结合以往经验做一次只读 CST 状态审计。",
                        tool_name="check_cst_status",
                    )
                ],
                fixture=_fixture(lessons=[lesson], tool_memory=tool_memory),
                oracle=_oracle(
                    allowed=["check_cst_status"],
                    sequences=[["check_cst_status"]],
                    facts=[marker, ONLINE_FACT],
                    forbidden=["run_solver", "execute_vba_script"],
                ),
                tags=["structured_memory", "tool_use_memory", "safe_rerank"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"CTX-{index}"
        cases.append(
            _case(
                case_id=f"context_followup_{index:02d}",
                category="context",
                scenario="multi_turn_context",
                design="no_design",
                failure="none",
                seed=seed,
                turns=[
                    _turn(
                        f"记住本次审计代号 {marker}，全程不得运行求解器。",
                        constraints=["不得运行求解器"],
                    ),
                    _turn(
                        "按刚才的要求检查 CST 状态，并在回答中带上代号。",
                        tool_name="check_cst_status",
                        constraints=["不得运行求解器"],
                    ),
                ],
                fixture=_fixture(),
                oracle=_oracle(
                    allowed=["check_cst_status"],
                    sequences=[["check_cst_status"]],
                    facts=[marker, ONLINE_FACT],
                    forbidden=["run_solver", "execute_vba_script"],
                ),
                tags=["multi_turn", "conversation_memory", "constraint_retention"],
            )
        )

    for index in range(1, 5):
        seed += 1
        old = f"OLD-{index}"
        current = f"CURRENT-{index}"
        cases.append(
            _case(
                case_id=f"goal_revision_{index:02d}",
                category="context",
                scenario="multi_turn_goal_revision",
                design="no_design",
                failure="none",
                seed=seed,
                turns=[
                    _turn(f"记录审计代号 {old}，不得运行求解器。", constraints=["不得运行求解器"]),
                    _turn(f"把代号更正为 {current}，旧代号作废。", constraints=["不得运行求解器"]),
                    _turn(
                        "按当前有效代号检查 CST 状态。",
                        tool_name="check_cst_status",
                        constraints=["不得运行求解器"],
                    ),
                ],
                fixture=_fixture(),
                oracle=_oracle(
                    allowed=["check_cst_status"],
                    sequences=[["check_cst_status"]],
                    facts=[current, ONLINE_FACT],
                    forbidden=["run_solver", "execute_vba_script"],
                ),
                tags=["multi_turn", "goal_revision", "stale_context"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"MATERIAL-{index}"
        calls = [_tool_call("check_cst_status"), _tool_call("list_project_materials")]
        cases.append(
            _case(
                case_id=f"multi_tool_materials_{index:02d}",
                category="tool_selection",
                scenario="multi_tool_read_probe",
                design="material_inventory",
                failure="none",
                seed=seed,
                turns=[
                    _turn(
                        f"{marker}：先检查 CST 状态，再列出工程可用材料；禁止 VBA 和求解。",
                        tool_calls=calls,
                        constraints=["禁止 VBA", "禁止求解"],
                    )
                ],
                fixture=_fixture(),
                oracle=_oracle(
                    allowed=["check_cst_status", "list_project_materials"],
                    sequences=[["check_cst_status", "list_project_materials"]],
                    facts=[marker, "种可用材料"],
                    forbidden=["run_solver", "execute_vba_script"],
                ),
                tags=["multi_tool", "material_inventory", "read_only"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"RECOVER-{index}"
        arguments = {
            "vba_code": f"' {marker} connectivity probe",
            "description": f"{marker} 无副作用连通性探针",
        }
        failures = [
            {
                "method": "execute_vba",
                "responses": [
                    {"success": False, "message": "CST is not connected"},
                    {"success": True, "executed": True, "message": f"fake VBA {marker} after reconnect"},
                ],
            }
        ]
        cases.append(
            _case(
                case_id=f"disconnect_recovery_{index:02d}",
                category="recovery",
                scenario="disconnect_recovery",
                design="generic_vba",
                failure="disconnect",
                seed=seed,
                turns=[
                    _turn(
                        f"执行 {marker} 无副作用 VBA 探针；断连时重连并重试。",
                        tool_name="execute_vba_script",
                        arguments=arguments,
                    )
                ],
                fixture=_fixture(connected=False, failures=failures),
                oracle=_oracle(
                    allowed=["check_cst_status", "execute_vba_script"],
                    required=["execute_vba_script"],
                    sequences=[["execute_vba_script"], ["check_cst_status", "execute_vba_script"]],
                    facts=[marker, ["重连", "恢复连接", "reconnect"]],
                    forbidden=["run_solver"],
                    recovery="reconnect_cst",
                    expected_arguments=[
                        {"tool_name": "execute_vba_script", "arguments": arguments}
                    ],
                ),
                tags=["failure_recovery", "retry", "argument_oracle"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"REC-CTX-{index}"
        arguments = {
            "vba_code": f"' {marker} contextual probe",
            "description": f"{marker} 上下文恢复探针",
        }
        failures = [
            {
                "method": "execute_vba",
                "responses": [
                    {"success": False, "message": "CST is not connected"},
                    {"success": True, "executed": True, "message": f"fake VBA {marker} after reconnect"},
                ],
            }
        ]
        cases.append(
            _case(
                case_id=f"recovery_context_{index:02d}",
                category="recovery",
                scenario="recovery_with_context",
                design="generic_vba",
                failure="disconnect",
                seed=seed,
                turns=[
                    _turn(f"记住恢复演练号 {marker}，禁止求解。", constraints=["禁止求解"]),
                    _turn(
                        "执行无副作用 VBA 探针；断连时恢复后重试，并带出演练号。",
                        tool_name="execute_vba_script",
                        arguments=arguments,
                        constraints=["禁止求解"],
                    ),
                ],
                fixture=_fixture(connected=False, failures=failures),
                oracle=_oracle(
                    allowed=["check_cst_status", "execute_vba_script"],
                    required=["execute_vba_script"],
                    sequences=[["execute_vba_script"], ["check_cst_status", "execute_vba_script"]],
                    facts=[marker, ["重连", "恢复连接", "reconnect"]],
                    forbidden=["run_solver"],
                    recovery="reconnect_cst",
                    expected_arguments=[
                        {"tool_name": "execute_vba_script", "arguments": arguments}
                    ],
                ),
                tags=["multi_turn", "failure_recovery", "context_retention"],
            )
        )

    for index in range(1, 5):
        seed += 1
        marker = f"SOLVER-{index}"
        frequency = 8.0 + index * 0.5
        brick = {
            "name": f"block_{index}",
            "component": "eval",
            "material": "PEC",
            "xmin": "-5",
            "xmax": "5",
            "ymin": "-5",
            "ymax": "5",
            "zmin": "0",
            "zmax": "1",
        }
        port = {
            "port_number": index,
            "p1_x": "0",
            "p1_y": "0",
            "p1_z": "0",
            "p2_x": "0",
            "p2_y": "0",
            "p2_z": "1",
            "impedance": "50",
        }
        freq = {"fmin": str(frequency - 0.5), "fmax": str(frequency + 0.5)}
        monitor = {"name": f"ff_{index}", "frequency": str(frequency), "use_subvolume": False}
        calls = [
            _tool_call("create_brick", brick),
            _tool_call("create_discrete_port", port),
            _tool_call("set_frequency_range", freq),
            _tool_call("create_farfield_monitor", monitor),
            _tool_call("run_solver"),
        ]
        cases.append(
            _case(
                case_id=f"solver_workflow_{index:02d}",
                category="tool_selection",
                scenario="multi_tool_solver_workflow",
                design="existing_project_solver",
                failure="none",
                seed=seed,
                turns=[
                    _turn(
                        f"{marker}：在 fake 工程建立最小求解前提并运行求解器，报告是否完成。",
                        tool_calls=calls,
                    )
                ],
                fixture=_fixture(),
                oracle=_oracle(
                    allowed=[
                        "create_brick",
                        "create_discrete_port",
                        "set_frequency_range",
                        "create_farfield_monitor",
                        "run_solver",
                    ],
                    sequences=[[
                        "create_brick",
                        "create_discrete_port",
                        "set_frequency_range",
                        "create_farfield_monitor",
                        "run_solver",
                    ]],
                    facts=[marker, "fake solver completed"],
                    expected_arguments=[
                        {"tool_name": "create_brick", "arguments": brick},
                        {"tool_name": "create_discrete_port", "arguments": port},
                        {"tool_name": "set_frequency_range", "arguments": freq},
                        {"tool_name": "create_farfield_monitor", "arguments": monitor},
                    ],
                ),
                tags=["multi_tool", "solver_preflight", "argument_oracle"],
            )
        )

    if len(cases) != 40:
        raise AssertionError(f"expected 40 cases, got {len(cases)}")
    return {
        "schema_version": "agent-e2e-case-v1",
        "dataset_id": "cst-agent-e2e-frozen-dev-v1",
        "split": "frozen_evaluation",
        "dataset_role": "developer_visible_frozen_evaluation",
        "frozen_at": "2026-08-10T12:00:00+08:00",
        "annotation_policy": (
            "Developer-authored machine-checkable oracles. Tool names, exact sequences, "
            "arguments, failure consumption and recovery are deterministic checks; response "
            "facts remain lexical and require separate semantic claim review."
        ),
        "generation_provenance": {
            "authoring_method": "developer_authored_before_canonical_model_run",
            "generator": "benchmarks/build_agent_e2e_frozen_dev_v1.py",
            "model_outputs_used_to_edit_oracles": False,
            "blinded": False,
        },
        "description": (
            "Forty developer-visible Agent engineering cases covering status grounding, hard "
            "constraints, no-tool behavior, structured/tool-use memory, multi-turn context and "
            "goal revision, multi-tool reads, disconnect recovery and solver preflight. This is "
            "a frozen evaluation set, not a blinded held-out benchmark."
        ),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        raise FileExistsError(f"dataset already exists: {args.output}; pass --force to replace")
    dataset = build_dataset()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.output} ({len(dataset['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
