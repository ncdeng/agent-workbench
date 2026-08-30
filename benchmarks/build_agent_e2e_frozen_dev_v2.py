"""Build v2 after preserving and auditing the immutable v1 model run."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from benchmarks.build_agent_e2e_frozen_dev_v1 import (
    DEFAULT_OUTPUT as V1_OUTPUT,
    OFFLINE_FACT,
    ONLINE_FACT,
    build_dataset as build_v1_dataset,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v2.json"

ONLINE_FACT_V2 = ONLINE_FACT + [
    "CST 当前在线",
    "当前在线",
    "已成功连接 CST",
    "已连接 CST",
    "连接正常",
]
OFFLINE_FACT_V2 = OFFLINE_FACT + ["当前离线", "not connected", "offline"]
RECOVERY_FACT = {
    "label": "response reports successful reconnect and retry",
    "all_of": [
        ["重连", "恢复连接", "连接恢复", "断连恢复", "reconnect"],
        ["成功执行", "重试成功", "恢复后完成", "after reconnect"],
    ],
}


def _semantic_vba_argument(marker: str) -> dict:
    return {
        "tool_name": "execute_vba_script",
        "arguments": {
            "vba_code": {
                "$contains": [marker],
                "$not_contains": ["Delete", "Solver.Start", "Rebuild"],
            },
            "description": {"$contains": [marker]},
        },
    }


def build_dataset() -> dict:
    dataset = deepcopy(build_v1_dataset())
    dataset.update(
        {
            "dataset_id": "cst-agent-e2e-frozen-dev-v2",
            "frozen_at": "2026-08-10T13:30:00+08:00",
            "description": (
                "Developer-visible 40-case regression v2. It preserves the v1 family matrix but "
                "repairs lexical false negatives, uses semantic VBA argument constraints, and "
                "makes solver parameters explicit after auditing the immutable Terra v1 run. "
                "It is not a blinded held-out benchmark."
            ),
            "generation_provenance": {
                "authoring_method": "developer_visible_oracle_revision_after_v1_model_audit",
                "source_dataset": str(V1_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
                "source_dataset_id": "cst-agent-e2e-frozen-dev-v1",
                "model_outputs_used_to_edit_oracles": True,
                "audited_model": "gpt-5.6-terra",
                "blinded": False,
                "boundary": (
                    "v2 is a post-audit regression set. Its scores must not be presented as "
                    "unseen-model generalization."
                ),
            },
        }
    )

    for case in dataset["cases"]:
        scenario = case["scenario_family"]
        oracle = case["oracle"]
        if scenario == "status_read":
            oracle["required_grounding_facts"] = [
                ONLINE_FACT_V2 if case["fixture"]["connected"] else OFFLINE_FACT_V2
            ]
        elif scenario in {"status_read_with_constraints", "memory_guided_status"}:
            oracle["required_grounding_facts"] = [ONLINE_FACT_V2]
        elif scenario == "multi_tool_read_probe":
            oracle["required_grounding_facts"] = [
                ONLINE_FACT_V2,
                ["种可用材料", "可用材料共", "available materials", "material inventory"],
            ]
        elif scenario == "disconnect_recovery":
            index = int(case["case_id"].rsplit("_", 1)[1])
            marker = f"RECOVER-{index}"
            oracle["required_grounding_facts"] = [RECOVERY_FACT]
            oracle["expected_tool_arguments"] = [_semantic_vba_argument(marker)]
        elif scenario == "recovery_with_context":
            index = int(case["case_id"].rsplit("_", 1)[1])
            marker = f"REC-CTX-{index}"
            oracle["required_grounding_facts"] = [marker, RECOVERY_FACT]
            oracle["expected_tool_arguments"] = [_semantic_vba_argument(marker)]
        elif scenario == "multi_tool_solver_workflow":
            index = int(case["case_id"].rsplit("_", 1)[1])
            frequency = 8.0 + index * 0.5
            oracle["required_grounding_facts"] = [
                ["求解完成", "求解器已完成", "fake solver completed", "solver completed"]
            ]
            case["turns"][0]["content"] = (
                f"在当前工程执行完整且明确的求解流程：创建 PEC 长方体 block_{index}，"
                "component=eval，范围 x/y=-5..5 mm、z=0..1 mm；创建离散端口 "
                f"port_number={index}，P1=(0,0,0)、P2=(0,0,1)、50 Ohm；设置频率 "
                f"{frequency - 0.5:.1f}..{frequency + 0.5:.1f} GHz；创建 farfield monitor "
                f"ff_{index} @ {frequency:.1f} GHz；最后运行求解器并报告真实工具结果。"
            )
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
