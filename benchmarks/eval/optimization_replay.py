from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cst_agent_workbench.optimization.models import OptimizationContext, OptimizationTarget, S11Summary
from cst_agent_workbench.optimization.strategy import HeuristicPatchOptimizationStrategy


CASES = [
    {
        "name": "resonance_high",
        "parameters": {"f0": 9.4, "patch_L": 10.0, "inset_depth": 2.0, "feed_W": 1.0, "substrate_h": 0.51, "copper_t": 0.035},
        "summary": {"min_freq_ghz": 9.8, "min_s11_db": -16.0, "target_s11_db": -8.0},
    },
    {
        "name": "match_tuning",
        "parameters": {"f0": 9.4, "patch_L": 10.0, "inset_depth": 2.0, "feed_W": 1.0, "substrate_h": 0.51, "copper_t": 0.035},
        "summary": {"min_freq_ghz": 9.41, "min_s11_db": -15.0, "target_s11_db": -7.0},
    },
    {
        "name": "already_met",
        "parameters": {"f0": 9.4, "patch_L": 10.0, "inset_depth": 2.0, "feed_W": 1.0, "substrate_h": 0.51, "copper_t": 0.035},
        "summary": {"min_freq_ghz": 9.4, "min_s11_db": -18.0, "target_s11_db": -12.0},
    },
]


def _direction_inferer(history, param_name, default_sign):
    return default_sign


def main() -> int:
    strategy = HeuristicPatchOptimizationStrategy(_direction_inferer)
    rows = []
    for case in CASES:
        proposal = strategy.propose_next_step(
            OptimizationContext(
                target=OptimizationTarget(mode="at_f0", target_freq_ghz=9.4, target_db=-10.0),
                parameters=case["parameters"],
                s11_summary=S11Summary.from_mapping(case["summary"]),
                history=[],
            )
        )
        rows.append(
            {
                "case": case["name"],
                "strategy": proposal.strategy,
                "reason": proposal.reason,
                "message": proposal.message,
                "updates": [
                    {"name": update.name, "old": update.old, "new": update.new}
                    for update in proposal.updates
                ],
            }
        )

    output_path = Path(__file__).resolve().parents[1] / "reports" / "optimization_replay.json"
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"\nSaved replay report to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
