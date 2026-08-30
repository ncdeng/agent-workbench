"""Revalidate a SHA-bound groundedness report without new model calls.

This tool is for scoring-code fixes only. It consumes the answer, canonical
Top-3 evidence, parsed judge JSON and raw-response hashes already stored in a
source report. It never regenerates an answer or asks a judge again.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks import agent_rag_groundedness_eval as eval_module


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(payload: Any) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(rendered.encode("utf-8"))


def revalidate_report(
    report: dict[str, Any],
    *,
    source_report_sha256: str,
) -> dict[str, Any]:
    if len(source_report_sha256) != 64:
        raise ValueError("source report SHA256 must be a 64-character digest")
    if report.get("schema_version") != "agent-rag-groundedness-report-v2":
        raise ValueError("revalidation requires an agent-rag-groundedness-report-v2 source")
    source_run_id = str(report.get("run_id") or "")
    if not source_run_id:
        raise ValueError("source groundedness report is missing run_id")

    validator_path = Path(eval_module.__file__).resolve()
    revalidator_path = Path(__file__).resolve()
    contract = {
        "source_run_id": source_run_id,
        "source_report_sha256": source_report_sha256,
        "validator_sha256": _sha256_file(validator_path),
        "revalidator_sha256": _sha256_file(revalidator_path),
        "judge_prompt_version": eval_module.JUDGE_PROMPT_VERSION,
        "method": "offline_saved_output_revalidation_no_model_calls",
    }
    contract_sha256 = _sha256_json(contract)
    derived = copy.deepcopy(report)
    recovered_ids: list[str] = []
    invalid_ids: list[str] = []

    for row in derived.get("results") or []:
        if "agent_ok" not in row:
            row["agent_ok"] = bool(row.get("ok")) or row.get("error_stage") == "judge_validation"
        if "evaluation_ok" not in row:
            row["evaluation_ok"] = bool(row.get("judge"))
        if row.get("agent_ok") and "citation_present" not in row:
            citations = [item for item in row.get("citations") or [] if isinstance(item, dict)]
            evidence = [item for item in row.get("evidence") or [] if isinstance(item, dict)]
            valid_citations = [
                citation
                for citation in citations
                if any(
                    eval_module._source_matches(
                        str(citation.get("source_path") or ""),
                        str(item.get("source_path") or ""),
                    )
                    and (
                        not str(citation.get("chunk_idx") or "")
                        or str(citation.get("chunk_idx")) == str(item.get("chunk_idx"))
                    )
                    for item in evidence
                )
            ]
            row["valid_citations"] = valid_citations
            row["citation_present"] = bool(citations)
            row["citation_precision"] = (
                len(valid_citations) / len(citations) if citations else 0.0
            )
        if row.get("judge"):
            continue
        failure = row.get("judge_validation_failure")
        if row.get("error_stage") != "judge_validation" or not isinstance(failure, dict):
            continue
        parsed = failure.get("parsed_response")
        evidence = row.get("evidence")
        if not isinstance(parsed, dict) or not isinstance(evidence, list):
            invalid_ids.append(str(row.get("id") or ""))
            continue
        try:
            judge = eval_module._validate_judge_result(parsed, evidence_items=evidence)
        except (TypeError, ValueError) as exc:
            row["revalidation_error"] = f"{type(exc).__name__}: {exc}"
            invalid_ids.append(str(row.get("id") or ""))
            continue

        case_id = str(row.get("id") or "")
        for claim in judge["claims"]:
            claim["claim_id"] = f"{case_id}:{claim['claim_id']}"
        judge.update(
            {
                "judge_tokens": None,
                "prompt_version": failure.get("prompt_version"),
                "prompt_sha256": failure.get("prompt_sha256"),
                "raw_response_sha256": failure.get("raw_response_sha256"),
                "revalidated_from_saved_output": True,
            }
        )
        row["judge"] = judge
        row["evaluation_ok"] = True
        row["ok"] = bool(row.get("agent_ok"))
        row["original_validation_failure"] = {
            "error": row.pop("error", None),
            "error_stage": row.pop("error_stage", None),
        }
        row.pop("revalidation_error", None)
        recovered_ids.append(case_id)

    derived.update(eval_module._summarize_rows(list(derived.get("results") or [])))
    derived["schema_version"] = "agent-rag-groundedness-report-v2-offline-revalidated-v1"
    derived["run_id"] = f"{source_run_id}-revalidated-{contract_sha256[:10]}"
    derived["revalidation"] = {
        **contract,
        "contract_sha256": contract_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_schema_version": report["schema_version"],
        "source_execution_contract_sha256": report.get("execution_contract_sha256"),
        "recovered_case_ids": recovered_ids,
        "still_invalid_case_ids": invalid_ids,
        "new_model_calls": 0,
        "limitations": [
            "This artifact re-scores saved outputs; it is not a new Agent or judge run.",
            "The source report remains authoritative for original execution provenance.",
            "Groundedness aggregates exclude judge-invalid responses and report their denominator separately.",
        ],
    }
    return derived


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_bytes = args.report.read_bytes()
    report = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(report, dict):
        raise TypeError("source report must contain one JSON object")
    output = revalidate_report(report, source_report_sha256=_sha256_bytes(source_bytes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in output.items() if key not in {"results"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
