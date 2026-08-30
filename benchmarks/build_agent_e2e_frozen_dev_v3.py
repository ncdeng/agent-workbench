"""Build the post-approval developer-visible Agent E2E regression v3."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from benchmarks.build_agent_e2e_frozen_dev_v2 import build_dataset as build_v2_dataset


ROOT = Path(__file__).resolve().parents[1]
V2_OUTPUT = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v2.json"
DEFAULT_OUTPUT = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v3.json"

SAVE_ARGUMENTS = {"include_results": False}
RECOVERY_SAVE_FACT = {
    "label": "response reports successful reconnect and project save",
    "all_of": [
        ["重连", "恢复连接", "连接恢复", "断连恢复", "reconnect"],
        [
            "保存成功",
            "工程已保存",
            "成功保存",
            "project saved",
            "saved after reconnect",
        ],
    ],
}


def _typed_save_failure(marker: str) -> list[dict]:
    return [
        {
            "method": "save_project",
            "responses": [
                {"success": False, "message": "CST is not connected"},
                {
                    "success": True,
                    "project_saved": True,
                    "message": f"fake project saved after reconnect ({marker})",
                },
            ],
        }
    ]


def _migrate_recovery_case(case: dict, *, marker: str, contextual: bool) -> None:
    action_turn = case["turns"][-1]
    if contextual:
        action_turn["content"] = (
            "保存当前 CST 工程（不包含结果）；断连时恢复后重试，并在回复中带出演练号。"
        )
    else:
        action_turn["content"] = (
            f"执行 {marker} 保存恢复演练：保存当前 CST 工程且不包含结果；"
            "断连时重连并重试。"
        )
    action_turn["tool_name"] = "save_cst_project"
    action_turn["tool_arguments"] = dict(SAVE_ARGUMENTS)

    case["design_family"] = "existing_project_save"
    case["fixture"]["injected_failures"] = _typed_save_failure(marker)

    oracle = case["oracle"]
    oracle["allowed_tools"] = ["check_cst_status", "save_cst_project"]
    oracle["required_tools_all"] = ["save_cst_project"]
    oracle["forbidden_tools"] = ["run_solver", "execute_vba_script"]
    oracle["valid_tool_sequences"] = [
        ["save_cst_project"],
        ["check_cst_status", "save_cst_project"],
    ]
    oracle["required_grounding_facts"] = (
        [marker, RECOVERY_SAVE_FACT] if contextual else [RECOVERY_SAVE_FACT]
    )
    oracle["expected_tool_arguments"] = [
        {"tool_name": "save_cst_project", "arguments": dict(SAVE_ARGUMENTS)}
    ]
    oracle["max_tool_calls"] = 2


def build_dataset() -> dict:
    dataset = deepcopy(build_v2_dataset())
    dataset.update(
        {
            "dataset_id": "cst-agent-e2e-frozen-dev-v3",
            "frozen_at": "2026-08-12T19:00:00+08:00",
            "description": (
                "Developer-visible 40-case post-approval regression v3. It preserves the v2 "
                "family matrix while migrating eight ordinary disconnect-recovery cases from "
                "approval-gated raw VBA to the typed save_cst_project contract. Raw VBA approval "
                "is evaluated separately. This set is not blinded or sealed."
            ),
            "generation_provenance": {
                "authoring_method": "developer_visible_post_approval_regression_revision",
                "source_dataset": str(V2_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
                "source_dataset_id": "cst-agent-e2e-frozen-dev-v2",
                "model_outputs_used_to_edit_oracles": True,
                "audited_model": "gpt-5.6-terra",
                "blinded": False,
                "sealed": False,
                "boundary": (
                    "v3 is a developer-visible post-approval regression set derived after v1/v2 "
                    "model audits. It must not be presented as unseen-model, blinded, or sealed "
                    "generalization evidence."
                ),
            },
        }
    )

    migrated = 0
    for case in dataset["cases"]:
        scenario = case["scenario_family"]
        if scenario == "disconnect_recovery":
            index = int(case["case_id"].rsplit("_", 1)[1])
            _migrate_recovery_case(case, marker=f"RECOVER-{index}", contextual=False)
            migrated += 1
        elif scenario == "recovery_with_context":
            index = int(case["case_id"].rsplit("_", 1)[1])
            _migrate_recovery_case(case, marker=f"REC-CTX-{index}", contextual=True)
            migrated += 1

    if migrated != 8:
        raise AssertionError(f"expected to migrate 8 recovery cases, got {migrated}")
    return dataset


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
