import copy
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchmarks.sealed_eval_handoff import (
    HANDOFF_V2_SIGNATURE_DOMAIN,
    PROMOTION_V1_SIGNATURE_DOMAIN,
    TrustPolicy,
    _canonical_signature_payload,
    _canonical_v2_signature_payload,
    _sha256_json,
    load_trust_policy,
    verify_private_oracles,
    verify_promotion,
    verify_public_handoff,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _identity(path: Path) -> dict:
    payload = path.read_bytes()
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sign_v2(document: dict, key: bytes, *, domain: bytes) -> None:
    document["signature"]["value"] = hmac.new(
        key,
        _canonical_v2_signature_payload(document, domain=domain),
        hashlib.sha256,
    ).hexdigest()


def _v1_manifest(tmp_path: Path, *, signed: bool = True) -> tuple[Path, Path, bytes]:
    public = tmp_path / "public_cases.json"
    fixtures = tmp_path / "fixtures.json"
    private = tmp_path / "private_oracles.json"
    public.write_text('{"cases":[{"case_id":"sealed-1","query":"hidden-oracle task"}]}', encoding="utf-8")
    fixtures.write_text('{"fixtures":[]}', encoding="utf-8")
    private.write_text('{"oracles":[{"case_id":"sealed-1","success":true}]}', encoding="utf-8")
    manifest = {
        "schema_version": "sealed-eval-handoff-v1",
        "handoff_id": "handoff-test-001",
        "issuer": "independent-evaluator",
        "recipient": "cst-agent-executor",
        "created_at": NOW.isoformat(),
        "dataset_role": "blinded_held_out",
        "developer_access": False,
        "custody": {
            "independent_issuer": True,
            "model_outputs_used_to_edit_oracles": False,
            "oracle_visible_to_executor": False,
        },
        "execution_contract": {
            "provider": "openai_compatible",
            "model": "gpt-5.6-terra",
            "arms": ["full", "no_memory"],
            "repeat_count": 3,
            "order_seed": 20260810,
            "runner_revision": "abcdef1",
            "runner_sha256": "a" * 64,
            "prompt_sha256": "b" * 64,
            "tool_catalog_sha256": "c" * 64,
        },
        "files": {
            "public_cases": {"path": public.name, **_identity(public)},
            "fixture_pack": {"path": fixtures.name, **_identity(fixtures)},
            "private_oracles": _identity(private),
        },
    }
    key = b"legacy-integrity-key"
    if signed:
        manifest["signature"] = {
            "algorithm": "hmac-sha256",
            "key_id": "legacy-key",
            "value": hmac.new(key, _canonical_signature_payload(manifest), hashlib.sha256).hexdigest(),
        }
    manifest_path = tmp_path / "sealed_eval_handoff.json"
    _write_json(manifest_path, manifest)
    return manifest_path, private, key


def _v2_bundle(
    tmp_path: Path,
    *,
    key: bytes = b"independent-v2-key",
    key_id: str = "issuer-key-1",
    handoff_id: str = "handoff-v2-test-001",
    not_before: datetime | None = None,
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    files = {
        "public_cases": tmp_path / "public_cases.json",
        "fixture_pack": tmp_path / "fixtures.json",
        "private_oracles": tmp_path / "private_oracles.json",
        "runner": tmp_path / "runner.py",
        "prompt": tmp_path / "prompt.txt",
        "tool_catalog": tmp_path / "tools.json",
    }
    files["public_cases"].write_text(
        '{"cases":[{"case_id":"sealed-1","query":"hidden-oracle task"}]}',
        encoding="utf-8",
    )
    files["fixture_pack"].write_text('{"fixtures":[]}', encoding="utf-8")
    files["private_oracles"].write_text(
        '{"oracles":[{"case_id":"sealed-1","success":true}]}',
        encoding="utf-8",
    )
    files["runner"].write_text("def run():\n    return True\n", encoding="utf-8")
    files["prompt"].write_text("Use only the supplied evidence.\n", encoding="utf-8")
    files["tool_catalog"].write_text('{"tools":["get_project_info"]}', encoding="utf-8")
    manifest = {
        "schema_version": "sealed-eval-handoff-v2",
        "handoff_id": handoff_id,
        "issuer": "independent-evaluator",
        "recipient": "cst-agent-executor",
        "created_at": (NOW - timedelta(hours=3)).isoformat(),
        "not_before": (not_before or NOW - timedelta(hours=2)).isoformat(),
        "dataset_role": "blinded_held_out",
        "developer_access": False,
        "custody": {
            "independent_issuer": True,
            "model_outputs_used_to_edit_oracles": False,
            "oracle_visible_to_executor": False,
        },
        "execution_contract": {
            "provider": "openai_compatible",
            "model": "gpt-5.6-terra",
            "arms": ["full", "no_memory"],
            "repeat_count": 3,
            "order_seed": 20260810,
            "runner_revision": "abcdef1",
            "artifacts": {
                name: {"path": files[name].name, **_identity(files[name])}
                for name in ("runner", "prompt", "tool_catalog")
            },
        },
        "files": {
            "public_cases": {"path": files["public_cases"].name, **_identity(files["public_cases"])},
            "fixture_pack": {"path": files["fixture_pack"].name, **_identity(files["fixture_pack"])},
            "private_oracles": _identity(files["private_oracles"]),
        },
        "signature": {
            "algorithm": "hmac-sha256-v2",
            "key_id": key_id,
        },
    }
    _sign_v2(manifest, key, domain=HANDOFF_V2_SIGNATURE_DOMAIN)
    manifest_path = tmp_path / "sealed_eval_handoff.json"
    _write_json(manifest_path, manifest)
    policy = TrustPolicy(
        recipient="cst-agent-executor",
        keys={
            ("independent-evaluator", key_id, "hmac-sha256-v2", "handoff"): key,
            ("independent-evaluator", key_id, "hmac-sha256-v2", "promotion"): key,
        },
    )
    return {
        "root": tmp_path,
        "files": files,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "key": key,
        "key_id": key_id,
        "policy": policy,
    }


def _promotion_bundle(tmp_path: Path) -> dict:
    bundle = _v2_bundle(tmp_path / "handoff")
    public_receipt = verify_public_handoff(
        bundle["manifest_path"], trust_policy=bundle["policy"], now=NOW
    )
    private_receipt = verify_private_oracles(
        bundle["manifest_path"],
        bundle["files"]["private_oracles"],
        trust_policy=bundle["policy"],
        public_verification=public_receipt,
        now=NOW,
    )
    public_receipt_path = tmp_path / "public_receipt.json"
    private_receipt_path = tmp_path / "private_receipt.json"
    _write_json(public_receipt_path, public_receipt)
    _write_json(private_receipt_path, private_receipt)
    artifacts = {
        "public_report": tmp_path / "public_report.json",
        "private_scores": tmp_path / "private_scores.json",
        "response_trace": tmp_path / "response_trace.jsonl",
    }
    response_count = 6
    provider_request_ids = [f"req-{index}" for index in range(response_count)]
    run = {
        "fingerprint": "d" * 64,
        "provider_request_ids": provider_request_ids,
        "code_revision": "abcdef1",
        "case_count": 1,
        "response_count": response_count,
        "complete_case_ids_sha256": _sha256_json(["sealed-1"]),
        "full_manifest_order": True,
        "filters_applied": False,
        "checkpoint_fingerprint_verified": True,
    }
    trace_rows = []
    for sequence_index, (repeat_index, arm) in enumerate(
        (repeat_index, arm)
        for repeat_index in range(1, 4)
        for arm in ("full", "no_memory")
    ):
        trace_rows.append(
            {
                "schema_version": "sealed-eval-response-trace-v1",
                "sequence_index": sequence_index,
                "case_id": "sealed-1",
                "arm": arm,
                "repeat_index": repeat_index,
                "provider_request_ids": [provider_request_ids[sequence_index]],
                "response_sha256": hashlib.sha256(
                    f"response-{sequence_index}".encode()
                ).hexdigest(),
                "trace_sha256": hashlib.sha256(f"trace-{sequence_index}".encode()).hexdigest(),
            }
        )
    artifacts["response_trace"].write_text(
        "".join(json.dumps(row) + "\n" for row in trace_rows),
        encoding="utf-8",
    )
    private_scores = {
        "schema_version": "sealed-eval-private-scores-v1",
        "handoff_id": bundle["manifest"]["handoff_id"],
        "manifest_sha256": _identity(bundle["manifest_path"])["sha256"],
        "response_trace_sha256": _identity(artifacts["response_trace"])["sha256"],
        "case_scores": [{"case_id": "sealed-1", "success": True}],
    }
    _write_json(artifacts["private_scores"], private_scores)
    public_report = {
        "schema_version": "sealed-eval-public-report-v1",
        "handoff_id": bundle["manifest"]["handoff_id"],
        "manifest_sha256": _identity(bundle["manifest_path"])["sha256"],
        "run": run,
        "response_trace_sha256": _identity(artifacts["response_trace"])["sha256"],
        "private_scores_sha256": _identity(artifacts["private_scores"])["sha256"],
    }
    _write_json(artifacts["public_report"], public_report)
    promotion = {
        "schema_version": "sealed-eval-promotion-v1",
        "handoff_id": bundle["manifest"]["handoff_id"],
        "issuer": bundle["manifest"]["issuer"],
        "manifest_sha256": _identity(bundle["manifest_path"])["sha256"],
        "public_verification_sha256": public_receipt["verification_receipt_sha256"],
        "private_verification_sha256": private_receipt["verification_receipt_sha256"],
        "executed_at": (NOW - timedelta(hours=1)).isoformat(),
        "adjudicated_at": (NOW - timedelta(minutes=30)).isoformat(),
        "run": run,
        "artifacts": {name: _identity(path) for name, path in artifacts.items()},
        "status": "promoted",
        "signature": {
            "algorithm": "hmac-sha256-v2",
            "key_id": bundle["key_id"],
        },
    }
    _sign_v2(promotion, bundle["key"], domain=PROMOTION_V1_SIGNATURE_DOMAIN)
    promotion_path = tmp_path / "promotion.json"
    _write_json(promotion_path, promotion)
    return {
        **bundle,
        "public_receipt": public_receipt,
        "private_receipt": private_receipt,
        "public_receipt_path": public_receipt_path,
        "private_receipt_path": private_receipt_path,
        "artifacts": artifacts,
        "promotion": promotion,
        "promotion_path": promotion_path,
    }


def _verify_complete_promotion(bundle: dict) -> dict:
    return verify_promotion(
        bundle["promotion_path"],
        manifest_path=bundle["manifest_path"],
        public_verification_path=bundle["public_receipt_path"],
        private_verification_path=bundle["private_receipt_path"],
        public_report_path=bundle["artifacts"]["public_report"],
        private_scores_path=bundle["artifacts"]["private_scores"],
        response_trace_path=bundle["artifacts"]["response_trace"],
        trust_policy=bundle["policy"],
        now=NOW,
    )


def test_v1_signature_is_integrity_only_and_never_release_eligible(tmp_path: Path):
    manifest_path, private, key = _v1_manifest(tmp_path)

    public_result = verify_public_handoff(manifest_path, signature_key=key)
    private_result = verify_private_oracles(manifest_path, private)

    assert public_result["legacy_v1"] is True
    assert public_result["manifest_integrity_verified"] is True
    assert public_result["execution_eligible"] is False
    assert public_result["release_eligible_handoff"] is False
    assert private_result["private_oracles_verified"] is True
    assert private_result["release_eligible_handoff"] is False


def test_v1_unsigned_debug_never_becomes_release_eligible(tmp_path: Path):
    manifest_path, _, _ = _v1_manifest(tmp_path, signed=False)

    result = verify_public_handoff(manifest_path, require_signature=False)

    assert result["manifest_integrity_verified"] is False
    assert result["release_eligible_handoff"] is False


def test_v1_public_tamper_wrong_key_and_path_escape_fail_closed(tmp_path: Path):
    manifest_path, _, key = _v1_manifest(tmp_path)
    (tmp_path / "public_cases.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        verify_public_handoff(manifest_path, signature_key=key)

    manifest_path, _, _ = _v1_manifest(tmp_path)
    with pytest.raises(ValueError, match="signature mismatch"):
        verify_public_handoff(manifest_path, signature_key=b"wrong-key")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["public_cases"]["path"] = "../outside.json"
    manifest["signature"]["value"] = hmac.new(
        key, _canonical_signature_payload(manifest), hashlib.sha256
    ).hexdigest()
    _write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="escapes bundle root"):
        verify_public_handoff(manifest_path, signature_key=key)


def test_v2_trusted_public_and_private_receipts_are_stable_and_path_free(tmp_path: Path):
    bundle = _v2_bundle(tmp_path)

    public_one = verify_public_handoff(
        bundle["manifest_path"], trust_policy=bundle["policy"], now=NOW
    )
    public_two = verify_public_handoff(
        bundle["manifest_path"], trust_policy=bundle["policy"], now=NOW
    )
    private = verify_private_oracles(
        bundle["manifest_path"],
        bundle["files"]["private_oracles"],
        trust_policy=bundle["policy"],
        public_verification=public_one,
        now=NOW,
    )

    assert public_one["execution_eligible"] is True
    assert public_one["release_eligible_handoff"] is False
    assert public_one["verification_receipt_sha256"] == public_two["verification_receipt_sha256"]
    assert private["private_oracles_verified"] is True
    assert private["public_verification_sha256"] == public_one["verification_receipt_sha256"]
    assert private["release_eligible_handoff"] is False
    assert str(tmp_path.resolve()) not in json.dumps(public_one)
    assert str(tmp_path.resolve()) not in json.dumps(private)


def test_v2_rejects_arbitrary_key_unknown_issuer_and_signed_envelope_tamper(tmp_path: Path):
    bundle = _v2_bundle(tmp_path)
    with pytest.raises(ValueError, match="trust policy"):
        verify_public_handoff(bundle["manifest_path"], signature_key=bundle["key"], now=NOW)

    untrusted = TrustPolicy(recipient="cst-agent-executor", keys={})
    with pytest.raises(ValueError, match="untrusted"):
        verify_public_handoff(bundle["manifest_path"], trust_policy=untrusted, now=NOW)

    manifest = copy.deepcopy(bundle["manifest"])
    manifest["signature"]["key_id"] = "rotated-key"
    _write_json(bundle["manifest_path"], manifest)
    both_keys = TrustPolicy(
        recipient="cst-agent-executor",
        keys={
            (
                "independent-evaluator",
                bundle["key_id"],
                "hmac-sha256-v2",
                "handoff",
            ): bundle["key"],
            (
                "independent-evaluator",
                "rotated-key",
                "hmac-sha256-v2",
                "handoff",
            ): bundle["key"],
        },
    )
    with pytest.raises(ValueError, match="signature mismatch"):
        verify_public_handoff(bundle["manifest_path"], trust_policy=both_keys, now=NOW)

    manifest = copy.deepcopy(bundle["manifest"])
    manifest["signature"]["algorithm"] = "hmac-sha256"
    _write_json(bundle["manifest_path"], manifest)
    with pytest.raises(ValueError, match="invalid sealed evaluation handoff"):
        verify_public_handoff(bundle["manifest_path"], trust_policy=bundle["policy"], now=NOW)


@pytest.mark.parametrize("artifact", ["runner", "prompt", "tool_catalog"])
def test_v2_rejects_execution_artifact_tamper(tmp_path: Path, artifact: str):
    bundle = _v2_bundle(tmp_path)
    bundle["files"][artifact].write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match=f"identity mismatch: {artifact}"):
        verify_public_handoff(bundle["manifest_path"], trust_policy=bundle["policy"], now=NOW)


def test_v2_rejects_future_not_before(tmp_path: Path):
    bundle = _v2_bundle(tmp_path, not_before=NOW + timedelta(minutes=1))

    with pytest.raises(ValueError, match="not_before"):
        verify_public_handoff(bundle["manifest_path"], trust_policy=bundle["policy"], now=NOW)


def test_v2_private_verifier_rejects_a_different_trusted_manifest(tmp_path: Path):
    first = _v2_bundle(tmp_path / "first", handoff_id="handoff-v2-first")
    second = _v2_bundle(tmp_path / "second", handoff_id="handoff-v2-second")
    public_receipt = verify_public_handoff(
        first["manifest_path"], trust_policy=first["policy"], now=NOW
    )

    with pytest.raises(ValueError, match="manifest identity mismatch"):
        verify_private_oracles(
            second["manifest_path"],
            second["files"]["private_oracles"],
            trust_policy=second["policy"],
            public_verification=public_receipt,
            now=NOW,
        )


def test_only_complete_trusted_promotion_grants_release_eligibility(tmp_path: Path):
    bundle = _promotion_bundle(tmp_path)

    result = _verify_complete_promotion(bundle)

    assert bundle["public_receipt"]["release_eligible_handoff"] is False
    assert bundle["private_receipt"]["release_eligible_handoff"] is False
    assert result["trusted_promotion_signature_verified"] is True
    assert result["full_manifest_order_verified"] is True
    assert result["release_eligible_handoff"] is True


@pytest.mark.parametrize(
    "tamper",
    ["public_report", "public_receipt", "private_receipt", "manifest_bytes"],
)
def test_promotion_artifact_receipt_or_manifest_tamper_fails_closed(tmp_path: Path, tamper: str):
    bundle = _promotion_bundle(tmp_path)
    if tamper == "public_report":
        bundle["artifacts"]["public_report"].write_text("tampered", encoding="utf-8")
    elif tamper == "public_receipt":
        receipt = copy.deepcopy(bundle["public_receipt"])
        receipt["execution_eligible"] = False
        _write_json(bundle["public_receipt_path"], receipt)
    elif tamper == "private_receipt":
        receipt = copy.deepcopy(bundle["private_receipt"])
        receipt["private_oracles_verified"] = False
        _write_json(bundle["private_receipt_path"], receipt)
    else:
        bundle["manifest_path"].write_text(
            bundle["manifest_path"].read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError):
        _verify_complete_promotion(bundle)


def test_promotion_rejects_incomplete_run_and_untrusted_signature(tmp_path: Path):
    bundle = _promotion_bundle(tmp_path)
    promotion = copy.deepcopy(bundle["promotion"])
    promotion["run"]["response_count"] = 5
    _sign_v2(promotion, bundle["key"], domain=PROMOTION_V1_SIGNATURE_DOMAIN)
    _write_json(bundle["promotion_path"], promotion)
    with pytest.raises(ValueError, match="response count"):
        _verify_complete_promotion(bundle)

    promotion = copy.deepcopy(bundle["promotion"])
    promotion["signature"]["key_id"] = "unknown-key"
    _write_json(bundle["promotion_path"], promotion)
    with pytest.raises(ValueError, match="untrusted"):
        _verify_complete_promotion(bundle)


def test_promotion_rejects_semantically_forged_private_receipt_even_when_resigned(
    tmp_path: Path,
):
    bundle = _promotion_bundle(tmp_path)
    forged_receipt = copy.deepcopy(bundle["private_receipt"])
    forged_receipt["trusted_manifest_verified"] = False
    forged_receipt["private_oracles_verified"] = False
    forged_receipt.pop("verification_receipt_sha256")
    forged_receipt["verification_receipt_sha256"] = _sha256_json(forged_receipt)
    _write_json(bundle["private_receipt_path"], forged_receipt)
    promotion = copy.deepcopy(bundle["promotion"])
    promotion["private_verification_sha256"] = forged_receipt["verification_receipt_sha256"]
    _sign_v2(promotion, bundle["key"], domain=PROMOTION_V1_SIGNATURE_DOMAIN)
    _write_json(bundle["promotion_path"], promotion)

    with pytest.raises(ValueError, match="trusted_manifest_verified"):
        _verify_complete_promotion(bundle)


def test_handoff_only_key_capability_cannot_authorize_promotion(tmp_path: Path):
    bundle = _promotion_bundle(tmp_path)
    handoff_only = TrustPolicy(
        recipient="cst-agent-executor",
        keys={
            (
                "independent-evaluator",
                bundle["key_id"],
                "hmac-sha256-v2",
                "handoff",
            ): bundle["key"]
        },
    )

    with pytest.raises(ValueError, match="promotion"):
        verify_promotion(
            bundle["promotion_path"],
            manifest_path=bundle["manifest_path"],
            public_verification_path=bundle["public_receipt_path"],
            private_verification_path=bundle["private_receipt_path"],
            public_report_path=bundle["artifacts"]["public_report"],
            private_scores_path=bundle["artifacts"]["private_scores"],
            response_trace_path=bundle["artifacts"]["response_trace"],
            trust_policy=handoff_only,
            now=NOW,
        )


def test_external_trust_policy_loads_relative_key_with_explicit_capabilities(tmp_path: Path):
    key_path = tmp_path / "issuer-key.bin"
    key_path.write_bytes(b"external-key-material")
    policy_path = tmp_path / "trust-policy.json"
    _write_json(
        policy_path,
        {
            "schema_version": "sealed-eval-trust-policy-v1",
            "recipient": "cst-agent-executor",
            "trusted_keys": [
                {
                    "issuer": "independent-evaluator",
                    "key_id": "issuer-key-1",
                    "algorithm": "hmac-sha256-v2",
                    "key_file": key_path.name,
                    "usages": ["handoff", "promotion"],
                }
            ],
        },
    )

    policy = load_trust_policy(policy_path)

    assert policy.recipient == "cst-agent-executor"
    assert policy.keys[
        ("independent-evaluator", "issuer-key-1", "hmac-sha256-v2", "handoff")
    ] == b"external-key-material"
    assert policy.keys[
        ("independent-evaluator", "issuer-key-1", "hmac-sha256-v2", "promotion")
    ] == b"external-key-material"


@pytest.mark.parametrize(
    "trace_mutation",
    ["missing_sample", "duplicate_sample", "wrong_request_id", "wrong_manifest_order"],
)
def test_promotion_recomputes_trace_coverage_instead_of_trusting_metadata(
    tmp_path: Path,
    trace_mutation: str,
):
    bundle = _promotion_bundle(tmp_path)
    trace_path = bundle["artifacts"]["response_trace"]
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    if trace_mutation == "missing_sample":
        rows.pop()
    elif trace_mutation == "duplicate_sample":
        rows[-1]["arm"] = rows[0]["arm"]
        rows[-1]["repeat_index"] = rows[0]["repeat_index"]
    elif trace_mutation == "wrong_request_id":
        rows[-1]["provider_request_ids"] = ["not-the-promoted-request"]
    else:
        rows[0]["case_id"] = "not-in-manifest"
    trace_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    promotion = copy.deepcopy(bundle["promotion"])
    promotion["artifacts"]["response_trace"] = _identity(trace_path)
    private_scores = json.loads(
        bundle["artifacts"]["private_scores"].read_text(encoding="utf-8")
    )
    private_scores["response_trace_sha256"] = _identity(trace_path)["sha256"]
    _write_json(bundle["artifacts"]["private_scores"], private_scores)
    promotion["artifacts"]["private_scores"] = _identity(bundle["artifacts"]["private_scores"])
    public_report = json.loads(
        bundle["artifacts"]["public_report"].read_text(encoding="utf-8")
    )
    public_report["response_trace_sha256"] = _identity(trace_path)["sha256"]
    public_report["private_scores_sha256"] = _identity(
        bundle["artifacts"]["private_scores"]
    )["sha256"]
    _write_json(bundle["artifacts"]["public_report"], public_report)
    promotion["artifacts"]["public_report"] = _identity(bundle["artifacts"]["public_report"])
    _sign_v2(promotion, bundle["key"], domain=PROMOTION_V1_SIGNATURE_DOMAIN)
    _write_json(bundle["promotion_path"], promotion)

    with pytest.raises(ValueError, match="response trace"):
        _verify_complete_promotion(bundle)
