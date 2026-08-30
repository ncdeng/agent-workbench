"""Legacy v1 single-review compatibility for claim-level Agent E2E diagnostics.

Do not use this entry point for current double-review calibration. The canonical
workflow is ``benchmarks.agent_e2e_reviewer_calibration`` (semantic review v2).
Historical v1 artifacts remain readable so their byte-bound provenance is not
rewritten or misrepresented as independent human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _response_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def export_review_template(report: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for group_name, group in report["groups"].items():
        cases = []
        for case in group["cases"]:
            cases.append(
                {
                    "case_id": case["case_id"],
                    "sample_id": case.get("sample_id", case["case_id"]),
                    "response_sha256": _response_sha256(str(case["final_response"])),
                    "final_response": case["final_response"],
                    "required_fact_judgments": [
                        {
                            "fact": check["fact"],
                            "supported": None,
                            "evidence": "",
                            "notes": "",
                        }
                        for check in case.get("grounding_checks") or []
                    ],
                    "response_claims": [
                        {
                            "claim_text": "",
                            "supported_by_tool_evidence": None,
                            "evidence": "",
                            "notes": "",
                        }
                    ],
                    "review_notes": "",
                }
            )
        groups[group_name] = {"cases": cases}
    return {
        "schema_version": "agent-e2e-claim-review-v1",
        "source_run_id": report["run"]["run_id"],
        "dataset_id": report["run"]["dataset_id"],
        "dataset_sha256": report["run"]["dataset_sha256"],
        "review_protocol": {
            "reviewer_id": "",
            "reviewer_type": "human",
            "blinded_to_group_and_model": False,
            "instructions": (
                "Judge each required fact semantically, then enumerate every externally verifiable "
                "claim in the response and mark whether tool evidence supports it. Remove the blank "
                "placeholder claim when the response contains no verifiable claims."
            ),
        },
        "groups": groups,
    }


def _review_cases_by_id(review_group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = list(review_group.get("cases") or [])
    indexed = {
        str(case.get("sample_id") or case.get("case_id") or ""): case
        for case in cases
    }
    if len(indexed) != len(cases):
        raise ValueError("claim review contains duplicate sample identifiers")
    return indexed


def apply_claim_review(report: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if review.get("schema_version") != "agent-e2e-claim-review-v1":
        raise ValueError("unsupported claim review schema")
    for field in ("source_run_id", "dataset_id", "dataset_sha256"):
        report_field = {
            "source_run_id": "run_id",
            "dataset_id": "dataset_id",
            "dataset_sha256": "dataset_sha256",
        }[field]
        if review.get(field) != report["run"].get(report_field):
            raise ValueError(f"claim review {field} does not match report")

    reviewed = deepcopy(report)
    total_facts = supported_facts = total_claims = supported_claims = 0
    semantic_case_count = semantic_case_successes = 0
    for group_name, group in reviewed["groups"].items():
        if group_name not in review.get("groups", {}):
            raise ValueError(f"claim review is missing group: {group_name}")
        review_cases = _review_cases_by_id(review["groups"][group_name])
        group_facts = group_supported_facts = group_claims = group_supported_claims = 0
        group_semantic_successes = 0
        for case in group["cases"]:
            case_id = str(case["case_id"])
            sample_id = str(case.get("sample_id") or case_id)
            if sample_id not in review_cases:
                raise ValueError(f"claim review is missing sample: {group_name}/{sample_id}")
            judgment = review_cases[sample_id]
            if judgment.get("response_sha256") != _response_sha256(str(case["final_response"])):
                raise ValueError(f"response SHA mismatch for {group_name}/{sample_id}")
            facts = list(judgment.get("required_fact_judgments") or [])
            claims = list(judgment.get("response_claims") or [])
            if any(not isinstance(item.get("supported"), bool) for item in facts):
                raise ValueError(f"required fact judgments are incomplete for {group_name}/{sample_id}")
            if any(not item.get("claim_text") or not isinstance(item.get("supported_by_tool_evidence"), bool) for item in claims):
                raise ValueError(f"response claim judgments are incomplete for {group_name}/{sample_id}")

            fact_supported = sum(bool(item["supported"]) for item in facts)
            claim_supported = sum(bool(item["supported_by_tool_evidence"]) for item in claims)
            semantic_grounding_ok = fact_supported == len(facts) and claim_supported == len(claims)
            semantic_success = bool(case["execution_success"] and semantic_grounding_ok)
            case["semantic_grounding"] = {
                "required_fact_count": len(facts),
                "supported_required_fact_count": fact_supported,
                "response_claim_count": len(claims),
                "supported_response_claim_count": claim_supported,
                "unsupported_claim_count": len(claims) - claim_supported,
                "semantic_grounding_ok": semantic_grounding_ok,
                "semantic_task_success": semantic_success,
                "review_notes": judgment.get("review_notes", ""),
            }
            group_facts += len(facts)
            group_supported_facts += fact_supported
            group_claims += len(claims)
            group_supported_claims += claim_supported
            group_semantic_successes += int(semantic_success)

        case_count = len(group["cases"])
        group["metrics"]["groundedness"].update(
            {
                "claim_precision": (
                    group_supported_claims / group_claims if group_claims else None
                ),
                "semantic_required_fact_recall": (
                    group_supported_facts / group_facts if group_facts else None
                ),
                "unsupported_claim_rate": (
                    (group_claims - group_supported_claims) / group_claims
                    if group_claims else None
                ),
                "semantic_task_success_rate": group_semantic_successes / case_count,
                "semantic_review_method": "response_hash_bound_claim_level_review",
            }
        )
        total_facts += group_facts
        supported_facts += group_supported_facts
        total_claims += group_claims
        supported_claims += group_supported_claims
        semantic_case_count += case_count
        semantic_case_successes += group_semantic_successes

    reviewed["claim_review"] = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "protocol": review.get("review_protocol") or {},
        "required_fact_recall": supported_facts / total_facts if total_facts else None,
        "claim_precision": supported_claims / total_claims if total_claims else None,
        "unsupported_claim_rate": (
            (total_claims - supported_claims) / total_claims if total_claims else None
        ),
        "semantic_task_success_rate": (
            semantic_case_successes / semantic_case_count if semantic_case_count else None
        ),
    }
    return reviewed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--export-template", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))

    if args.export_template:
        template = export_review_template(report)
        args.export_template.parent.mkdir(parents=True, exist_ok=True)
        args.export_template.write_text(
            json.dumps(template, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    if not args.review or not args.output:
        parser.error("apply mode requires --review and --output")
    review = json.loads(args.review.read_text(encoding="utf-8"))
    reviewed = apply_claim_review(report, review)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
