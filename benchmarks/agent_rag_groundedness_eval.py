"""Run the real CST Agent against frozen RAG cases and score grounded answers.

CST side effects are disabled with the repository's FakeCSTController. Chat,
Planner, RAG translation/retrieval/rerank, Executor, and Trace remain real.
All session-memory artifacts are redirected to the requested D-drive directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.cst.controller import CSTController


DEFAULT_CASES = Path(__file__).with_name("rag_official_docs_heldout_v1.json")
DEFAULT_ARTIFACT_DIR = Path(r"D:\cst_agent_rag_data\agent_groundedness_eval")
ROOT = Path(__file__).resolve().parents[1]
JUDGE_PROMPT_VERSION = "rag-claim-judge-v2"
JUDGE_SYSTEM_PROMPT = "Return strict JSON only."
_CITATION_RE = re.compile(
    r"\[source=(?P<source>[^,\]]+)(?:,\s*chunk=(?P<chunk>[^\]]+))?\]",
    re.IGNORECASE,
)


class JudgeResponseValidationError(ValueError):
    """Preserve judge output provenance when strict claim validation fails."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: str,
        prompt_sha256: str,
        parsed_response: dict[str, Any] | None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.prompt_sha256 = prompt_sha256
        self.parsed_response = parsed_response


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(payload: Any) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(rendered.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _git_worktree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip())


def _normalize_source(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _source_matches(actual: str, expected: str) -> bool:
    normalized_actual = _normalize_source(actual)
    normalized_expected = _normalize_source(expected)
    return bool(normalized_actual and normalized_expected) and (
        normalized_actual.endswith(normalized_expected)
        or normalized_expected.endswith(normalized_actual)
    )


def _source_relevance(actual: str, case: dict[str, Any]) -> int:
    qrels = case.get("qrels") or []
    if qrels:
        return max(
            (
                int(qrel.get("relevance", 0))
                for qrel in qrels
                if _source_matches(actual, str(qrel.get("source_path") or ""))
            ),
            default=0,
        )
    return 3 if any(
        _source_matches(actual, str(expected))
        for expected in case.get("expected_sources") or []
    ) else 0


def _validate_cases(payload: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(payload, dict):
        cases = payload.get("cases")
        role = str(payload.get("dataset_role") or "held_out")
    else:
        cases = payload
        role = "development_pilot"
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise TypeError("evaluation cases must be a list of JSON objects")
    return cases, role


def _select_stratified_cases(
    cases: list[dict[str, Any]],
    *,
    per_language: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for case in cases:
        language = str(case.get("language") or "unknown")
        if counts.get(language, 0) >= per_language:
            continue
        selected.append(case)
        counts[language] = counts.get(language, 0) + 1
    return selected


def _select_case_ids(
    cases: list[dict[str, Any]],
    case_ids: list[str],
) -> list[dict[str, Any]]:
    requested = list(dict.fromkeys(str(value) for value in case_ids))
    by_id = {str(case.get("id") or ""): case for case in cases}
    missing = [case_id for case_id in requested if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown RAG groundedness case IDs: {', '.join(missing)}")
    return [by_id[case_id] for case_id in requested]


def _offline_controller() -> CSTController:
    """Construct the production controller interface without probing CST/COM."""
    controller = CSTController.__new__(CSTController)
    controller.connected = False
    controller.offline_mode = True
    controller.cst_exe = None
    controller.last_message = "Agent groundedness evaluation: CST side effects disabled"
    controller.project_path = ""
    controller.cst_python_command = []
    return controller


def _extract_citations(answer: str) -> list[dict[str, str]]:
    return [
        {
            "source_path": match.group("source").strip(),
            "chunk_idx": str(match.group("chunk") or "").strip(),
        }
        for match in _CITATION_RE.finditer(str(answer or ""))
    ]


def _parse_json_object(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(clean[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("judge response must be a JSON object")
    return value


def _canonical_evidence(documents: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "source_path": str(hit.get("source_path") or ""),
            "chunk_idx": (
                "" if hit.get("chunk_idx") is None else str(hit.get("chunk_idx"))
            ),
            "text": str(hit.get("text") or "")[:1800],
        }
        for hit in documents[:3]
    ]


def _build_judge_prompt(query: str, answer: str, evidence_items: list[dict[str, str]]) -> str:
    evidence = "\n\n".join(
        f"SOURCE: {item['source_path']}\nCHUNK: {item['chunk_idx']}\nTEXT: {item['text']}"
        for item in evidence_items
    )
    return f"""You are evaluating a retrieval-augmented answer. Use ONLY the supplied evidence.
Do not reward facts that are merely plausible from outside knowledge. Ignore bracketed runtime/status notices such as
offline-mode warnings; they are operational metadata, not domain claims. Split the answer into material factual claims,
then decide whether each claim is directly supported by the evidence. Citation syntax is
[source=path, chunk=number], and a citation is entailed only when its cited evidence supports the nearby claim.

Return one JSON object with exactly these fields:
{{
  "claim_count": integer,
  "supported_claim_count": integer,
  "unsupported_claim_count": integer,
  "groundedness_score": number from 0 to 1,
  "answer_relevance_score": number from 0 to 1,
  "citation_entailment": boolean,
  "claims": [
    {{
      "text": "one atomic material claim",
      "supported": boolean,
      "evidence_refs": [{{"source_path": "exact supplied path", "chunk_idx": "exact supplied chunk"}}],
      "reason": "short support decision"
    }}
  ],
  "reason": "short explanation"
}}

The claim counts must exactly match the claims array. groundedness_score must equal
supported_claim_count / claim_count (or 1.0 when there are no material claims). Every supported claim must cite at
least one supplied evidence item in evidence_refs. Unsupported claims must use an empty evidence_refs array.

QUESTION:
{query}

ANSWER:
{answer}

EVIDENCE:
{evidence}
"""


def _validate_judge_result(
    result: dict[str, Any],
    *,
    evidence_items: list[dict[str, str]],
) -> dict[str, Any]:
    required = {
        "claim_count",
        "supported_claim_count",
        "unsupported_claim_count",
        "groundedness_score",
        "answer_relevance_score",
        "citation_entailment",
        "claims",
        "reason",
    }
    missing = sorted(required - set(result))
    if missing:
        raise ValueError(f"judge response missing fields: {', '.join(missing)}")
    claims = result["claims"]
    if not isinstance(claims, list):
        raise TypeError("judge claims must be a list")
    claim_count = int(result["claim_count"])
    supported_count = int(result["supported_claim_count"])
    unsupported_count = int(result["unsupported_claim_count"])
    if claim_count != len(claims):
        raise ValueError("judge claim_count does not match claims array")
    if supported_count + unsupported_count != claim_count:
        raise ValueError("judge supported and unsupported counts do not sum to claim_count")

    normalized_claims: list[dict[str, Any]] = []
    observed_supported = 0
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            raise TypeError(f"judge claim {index} must be an object")
        text = str(claim.get("text") or "").strip()
        if not text:
            raise ValueError(f"judge claim {index} has empty text")
        if not isinstance(claim.get("supported"), bool):
            raise TypeError(f"judge claim {index} supported must be boolean")
        supported = bool(claim["supported"])
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list):
            raise TypeError(f"judge claim {index} evidence_refs must be a list")
        normalized_refs: list[dict[str, str]] = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise TypeError(f"judge claim {index} evidence ref must be an object")
            source_path = str(ref.get("source_path") or "").strip()
            raw_chunk_idx = ref.get("chunk_idx")
            chunk_idx = "" if raw_chunk_idx is None else str(raw_chunk_idx).strip()
            matches = [
                item
                for item in evidence_items
                if _source_matches(source_path, item["source_path"])
                and chunk_idx == str(item["chunk_idx"])
            ]
            matched_identities = {
                (str(item["source_path"]), str(item["chunk_idx"])) for item in matches
            }
            if not matched_identities:
                raise ValueError(f"judge claim {index} references evidence outside the supplied Top-3")
            if len(matched_identities) != 1:
                raise ValueError(f"judge claim {index} evidence reference is ambiguous")
            matched_source, matched_chunk = next(iter(matched_identities))
            normalized_refs.append({"source_path": matched_source, "chunk_idx": matched_chunk})
        if supported and not normalized_refs:
            raise ValueError(f"judge supported claim {index} has no evidence reference")
        if not supported and normalized_refs:
            raise ValueError(f"judge unsupported claim {index} must not cite supporting evidence")
        observed_supported += int(supported)
        normalized_claims.append(
            {
                "claim_id": f"claim-{index:03d}",
                "text": text,
                "claim_sha256": _sha256_bytes(text.encode("utf-8")),
                "supported": supported,
                "evidence_refs": normalized_refs,
                "reason": str(claim.get("reason") or "").strip(),
            }
        )
    if observed_supported != supported_count:
        raise ValueError("judge supported_claim_count does not match claim verdicts")
    if claim_count - observed_supported != unsupported_count:
        raise ValueError("judge unsupported_claim_count does not match claim verdicts")

    groundedness = float(result["groundedness_score"])
    relevance = float(result["answer_relevance_score"])
    if not 0.0 <= groundedness <= 1.0 or not 0.0 <= relevance <= 1.0:
        raise ValueError("judge scores must be within [0, 1]")
    expected_groundedness = observed_supported / claim_count if claim_count else 1.0
    if abs(groundedness - expected_groundedness) > 0.02:
        raise ValueError("judge groundedness_score is inconsistent with claim verdicts")
    if not isinstance(result["citation_entailment"], bool):
        raise TypeError("judge citation_entailment must be boolean")

    return {
        "claim_count": claim_count,
        "supported_claim_count": observed_supported,
        "unsupported_claim_count": claim_count - observed_supported,
        "groundedness_score": expected_groundedness,
        "answer_relevance_score": relevance,
        "citation_entailment": result["citation_entailment"],
        "claims": normalized_claims,
        "unsupported_claims": [claim["text"] for claim in normalized_claims if not claim["supported"]],
        "reason": str(result["reason"]),
    }


def _judge_answer(client, model: str, query: str, answer: str, documents: list[dict]) -> dict[str, Any]:
    evidence_items = _canonical_evidence(documents)
    prompt = _build_judge_prompt(query, answer, evidence_items)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        timeout=60,
    )
    content = response.choices[0].message.content or ""
    prompt_sha256 = _sha256_bytes(prompt.encode("utf-8"))
    parsed: dict[str, Any] | None = None
    try:
        parsed = _parse_json_object(content)
        result = _validate_judge_result(parsed, evidence_items=evidence_items)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JudgeResponseValidationError(
            str(exc),
            raw_response=content,
            prompt_sha256=prompt_sha256,
            parsed_response=parsed,
        ) from exc
    usage = getattr(response, "usage", None)
    result["judge_tokens"] = int(getattr(usage, "total_tokens", 0) or 0)
    result["prompt_version"] = JUDGE_PROMPT_VERSION
    result["prompt_sha256"] = prompt_sha256
    result["raw_response_sha256"] = _sha256_bytes(content.encode("utf-8"))
    return result


def _trace_rag_documents(trace: dict[str, Any]) -> list[dict[str, Any]]:
    rag_context = trace.get("rag_context") or {}
    documents = rag_context.get("document") or rag_context.get("documents") or []
    return [dict(item) for item in documents if isinstance(item, dict)]


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered) + 0.5)) - 1))
    return ordered[index]


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    agent_successful = [row for row in rows if row.get("agent_ok")]
    evaluated = [row for row in rows if row.get("agent_ok") and row.get("judge")]
    claim_count = sum(int(row["judge"].get("claim_count", 0)) for row in evaluated)
    unsupported_claim_count = sum(
        int(row["judge"].get("unsupported_claim_count", 0)) for row in evaluated
    )
    latencies = [float(row["latency_ms"]) for row in rows]
    count = len(rows)

    def boolean_metric(field: str) -> tuple[float, int]:
        values = [
            bool(row[field])
            for row in agent_successful
            if field in row and row[field] is not None
        ]
        return (sum(values) / len(values) if values else 0.0, len(values))

    def numeric_metric(field: str) -> tuple[float, int]:
        values = [
            float(row[field])
            for row in agent_successful
            if field in row and row[field] is not None
        ]
        return (statistics.fmean(values) if values else 0.0, len(values))

    retrieval_rate, retrieval_count = boolean_metric("retrieval_relevant")
    citation_presence, citation_presence_count = boolean_metric("citation_present")
    citation_precision, citation_precision_count = numeric_metric("citation_precision")
    provenance_rate, provenance_count = boolean_metric("provenance_complete")
    trace_rate, trace_count = boolean_metric("trace_rag_complete")
    return {
        "success_rate": len(evaluated) / count if count else 0.0,
        "agent_completion_rate": len(agent_successful) / count if count else 0.0,
        "judge_valid_rate": (
            len(evaluated) / len(agent_successful) if agent_successful else 0.0
        ),
        "evaluation_success_rate": len(evaluated) / count if count else 0.0,
        "judge_invalid_count": len(agent_successful) - len(evaluated),
        "groundedness_evaluated_responses": len(evaluated),
        "groundedness_evaluated_claims": claim_count,
        "retrieval_recall_at_3": retrieval_rate,
        "retrieval_evaluated_responses": retrieval_count,
        "citation_presence_rate": citation_presence,
        "citation_presence_evaluated_responses": citation_presence_count,
        "citation_precision": citation_precision,
        "citation_precision_evaluated_responses": citation_precision_count,
        "provenance_completeness": provenance_rate,
        "provenance_evaluated_responses": provenance_count,
        "trace_rag_completeness": trace_rate,
        "trace_evaluated_responses": trace_count,
        "groundedness_score": (
            statistics.fmean(
                float(row["judge"].get("groundedness_score", 0.0)) for row in evaluated
            )
            if evaluated
            else 0.0
        ),
        "answer_relevance_score": (
            statistics.fmean(
                float(row["judge"].get("answer_relevance_score", 0.0))
                for row in evaluated
            )
            if evaluated
            else 0.0
        ),
        "unsupported_claim_rate": (
            unsupported_claim_count / claim_count if claim_count else 0.0
        ),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p95": round(_percentile_95(latencies), 2),
        },
    }


def evaluate(
    cases: list[dict[str, Any]],
    *,
    artifact_dir: Path,
    judge_model: str,
    dataset_identity: dict[str, Any] | None = None,
    selection_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    generated_at = datetime.now(timezone.utc).isoformat()
    code_revision = _git_revision()
    runner_sha256 = _sha256_file(Path(__file__).resolve())
    judge_template_sha256 = _sha256_bytes(
        _build_judge_prompt("", "", []).encode("utf-8")
    )
    execution_contract = {
        "selected_case_ids": [str(case.get("id") or "") for case in cases],
        "agent_model": config.OPENAI_MODEL,
        "judge_model": judge_model,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "embedding_model": config.EMBEDDING_LOCAL_MODEL,
        "reranker_model": config.RAG_RERANK_MODEL,
        "candidate_k": config.RAG_RERANK_CANDIDATE_K,
        "final_k": 3,
        "code_revision": code_revision,
        "worktree_dirty": _git_worktree_dirty(),
        "runner_sha256": runner_sha256,
        "judge_template_sha256": judge_template_sha256,
    }
    execution_contract_sha256 = _sha256_json(execution_contract)
    run_id = f"rag-groundedness-{int(time.time())}-{execution_contract_sha256[:10]}"

    for case in cases:
        case_id = str(case.get("id") or f"case-{len(rows) + 1}")
        query = str(case.get("query") or "")
        case_home = artifact_dir / "sessions" / case_id
        case_home.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        answer = ""
        agent_ok = False
        documents: list[dict[str, Any]] = []
        evidence_items: list[dict[str, str]] = []
        citations: list[dict[str, str]] = []
        valid_citations: list[dict[str, str]] = []
        trace: dict[str, Any] = {}
        retrieval_relevant = False
        rerank_applied = False
        provenance_complete = False
        trace_complete = False
        try:
            with patch("os.path.expanduser", lambda path: str(case_home) if path == "~" else path):
                agent = CSTAgent(_offline_controller())
            answer = agent.chat(query)
            agent_ok = bool(agent.last_chat_status.get("ok"))
            latency_ms = (time.perf_counter() - started) * 1000.0
            rag_context = dict(agent.session.metadata.get("last_rag_context") or {})
            documents = [
                dict(hit) for hit in rag_context.get("documents") or [] if isinstance(hit, dict)
            ]
            trace = dict(agent.trace_history[-1]) if agent.trace_history else {}
            trace_documents = _trace_rag_documents(trace)
            citations = _extract_citations(answer)
            valid_citations = [
                citation
                for citation in citations
                if any(
                    _source_matches(citation["source_path"], hit.get("source_path", ""))
                    and (
                        not citation["chunk_idx"]
                        or str(citation["chunk_idx"]) == str(hit.get("chunk_idx"))
                    )
                    for hit in documents
                )
            ]
            required_fields = (
                "source_path",
                "source_type",
                "chunk_idx",
                "dense_score",
                "rerank_score",
                "rank_before",
                "rank_after",
                "reranker_model",
            )
            provenance_complete = bool(documents) and all(
                all(hit.get(field) not in (None, "") for field in required_fields)
                for hit in documents
            )
            trace_complete = bool(trace_documents) and {
                (hit.get("source_path"), hit.get("chunk_idx")) for hit in trace_documents
            } == {
                (hit.get("source_path"), hit.get("chunk_idx")) for hit in documents
            }
            evidence_items = _canonical_evidence(documents)
            retrieval_relevant = any(
                _source_relevance(str(hit.get("source_path") or ""), case) >= 2
                for hit in documents
            )
            rerank_applied = bool(documents) and all(
                hit.get("rerank_applied") for hit in documents
            )
            judge = _judge_answer(agent.client, judge_model, query, answer, documents)
            for claim in judge["claims"]:
                claim["claim_id"] = f"{case_id}:{claim['claim_id']}"
            rows.append(
                {
                    "id": case_id,
                    "language": case.get("language"),
                    "category": case.get("category"),
                    "query": query,
                    "answer": answer,
                    "answer_sha256": _sha256_bytes(answer.encode("utf-8")),
                    "evidence": evidence_items,
                    "evidence_sha256": _sha256_json(evidence_items),
                    "ok": agent_ok,
                    "agent_ok": agent_ok,
                    "evaluation_ok": True,
                    "latency_ms": round(latency_ms, 2),
                    "retrieved_sources": [hit.get("source_path") for hit in documents],
                    "retrieved_chunks": [hit.get("chunk_idx") for hit in documents],
                    "retrieval_relevant": retrieval_relevant,
                    "rerank_applied": rerank_applied,
                    "citations": citations,
                    "valid_citations": valid_citations,
                    "citation_present": bool(citations),
                    "citation_precision": len(valid_citations) / len(citations) if citations else 0.0,
                    "provenance_complete": provenance_complete,
                    "trace_rag_complete": trace_complete,
                    "trace_run_id": trace.get("run_id"),
                    "agent_tokens": dict(agent.get_token_stats()),
                    "judge": judge,
                }
            )
        except Exception as exc:
            failure_row: dict[str, Any] = {
                "id": case_id,
                "language": case.get("language"),
                "category": case.get("category"),
                "query": query,
                "answer": answer,
                "answer_sha256": _sha256_bytes(answer.encode("utf-8")) if answer else None,
                "evidence": evidence_items,
                "evidence_sha256": _sha256_json(evidence_items) if evidence_items else None,
                "retrieved_sources": [hit.get("source_path") for hit in documents],
                "retrieved_chunks": [hit.get("chunk_idx") for hit in documents],
                "citations": citations,
                "trace_run_id": trace.get("run_id"),
                "ok": False,
                "agent_ok": agent_ok,
                "evaluation_ok": False,
                "retrieval_relevant": retrieval_relevant,
                "rerank_applied": rerank_applied,
                "valid_citations": valid_citations,
                "citation_present": bool(citations),
                "citation_precision": (
                    len(valid_citations) / len(citations) if citations else 0.0
                ),
                "provenance_complete": provenance_complete,
                "trace_rag_complete": trace_complete,
                "error_stage": (
                    "judge_validation"
                    if isinstance(exc, JudgeResponseValidationError)
                    else "agent_or_retrieval_pipeline"
                ),
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            }
            if isinstance(exc, JudgeResponseValidationError):
                failure_row["judge_validation_failure"] = {
                    "prompt_version": JUDGE_PROMPT_VERSION,
                    "prompt_sha256": exc.prompt_sha256,
                    "raw_response": exc.raw_response,
                    "raw_response_sha256": _sha256_bytes(exc.raw_response.encode("utf-8")),
                    "parsed_response": exc.parsed_response,
                }
            rows.append(failure_row)

    count = len(rows)
    return {
        "schema_version": "agent-rag-groundedness-report-v2",
        "run_id": run_id,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "execution_contract": execution_contract,
        "execution_contract_sha256": execution_contract_sha256,
        "dataset": dict(dataset_identity or {}),
        "selection_policy": dict(selection_policy or {}),
        "selected_cases_sha256": _sha256_json(
            [
                {
                    "id": str(case.get("id") or ""),
                    "query": str(case.get("query") or ""),
                    "language": case.get("language"),
                    "category": case.get("category"),
                }
                for case in cases
            ]
        ),
        "dataset_role": "held_out_agent_subset",
        "cases": count,
        "case_ids": [row["id"] for row in rows],
        "agent_model": config.OPENAI_MODEL,
        "judge_model": judge_model,
        "rag": {
            "embedding_model": config.EMBEDDING_LOCAL_MODEL,
            "reranker_model": config.RAG_RERANK_MODEL,
            "candidate_k": config.RAG_RERANK_CANDIDATE_K,
            "final_k": 3,
        },
        **_summarize_rows(rows),
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--per-language", type=int, default=2)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run explicit case IDs as a developer debug filter instead of the fixed stratified subset.",
    )
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--judge-model", default=config.OPENAI_MODEL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases, dataset_role = _validate_cases(payload)
    if args.case_id:
        selected = _select_case_ids(cases, args.case_id)
        selection_policy = {
            "method": "explicit_case_id_debug_filter",
            "case_ids": list(args.case_id),
            "developer_visible": True,
            "release_eligible": False,
        }
    else:
        selected = _select_stratified_cases(cases, per_language=max(1, args.per_language))
        selection_policy = {
            "method": "fixed_prefix_per_language",
            "per_language": max(1, args.per_language),
            "developer_visible": True,
            "release_eligible": False,
        }
    report = evaluate(
        selected,
        artifact_dir=args.artifact_dir,
        judge_model=args.judge_model,
        dataset_identity={
            "path": str(args.cases.resolve()),
            "sha256": _sha256_file(args.cases),
            "role": dataset_role,
            "case_count": len(cases),
            "blinded": bool(payload.get("blinded")) if isinstance(payload, dict) else False,
            "identity_scope": "byte_identity_only_not_independent_blinding",
        },
        selection_policy=selection_policy,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "results"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
