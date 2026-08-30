"""Fail-closed verification helpers for independently authored sealed eval packs."""
from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


HANDOFF_SCHEMA_PATHS = {
    "sealed-eval-handoff-v1": Path(__file__).with_name("sealed_eval_handoff.schema.json"),
    "sealed-eval-handoff-v2": Path(__file__).with_name("sealed_eval_handoff_v2.schema.json"),
}
PROMOTION_SCHEMA_PATH = Path(__file__).with_name("sealed_eval_promotion.schema.json")
HANDOFF_V2_SIGNATURE_DOMAIN = b"cst-agent-sealed-eval\x00handoff-v2\x00"
PROMOTION_V1_SIGNATURE_DOMAIN = b"cst-agent-sealed-eval\x00promotion-v1\x00"
PUBLIC_RECEIPT_VERSION = "sealed-eval-public-verification-v2"
PRIVATE_RECEIPT_VERSION = "sealed-eval-private-verification-v2"


@dataclass(frozen=True)
class TrustPolicy:
    """Runtime-only issuer keyring; key material is never copied into receipts."""

    recipient: str
    keys: Mapping[tuple[str, str, str, str], bytes]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return _payload_identity(payload)


def _payload_identity(payload: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


def _read_json_snapshot(path: Path, *, label: str) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    return document, _payload_identity(payload), payload


def _canonical_signature_payload(manifest: dict[str, Any]) -> bytes:
    """Legacy v1 payload; kept byte-for-byte compatible for integrity checks only."""

    unsigned = copy.deepcopy(manifest)
    unsigned.pop("signature", None)
    return _canonical_json_bytes(unsigned)


def _canonical_v2_signature_payload(document: dict[str, Any], *, domain: bytes) -> bytes:
    """Bind the signed envelope while excluding only the signature value."""

    unsigned = copy.deepcopy(document)
    signature = unsigned.get("signature")
    if not isinstance(signature, dict):
        raise ValueError("v2 signed document requires a signature envelope")
    signature.pop("value", None)
    return domain + _canonical_json_bytes(unsigned)


def _schema_validator(schema_path: Path):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:  # pragma: no cover - declared project dependency
        raise RuntimeError("jsonschema is required to validate sealed handoffs") from exc
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate(document: dict[str, Any], schema_path: Path, *, label: str) -> None:
    errors = sorted(_schema_validator(schema_path).iter_errors(document), key=lambda item: list(item.path))
    if errors:
        detail = "; ".join(f"{list(item.path)}: {item.message}" for item in errors[:8])
        raise ValueError(f"invalid {label}: {detail}")


def validate_handoff_schema(
    manifest: dict[str, Any],
    schema_path: Path | None = None,
) -> None:
    if schema_path is None:
        version = manifest.get("schema_version")
        schema_path = HANDOFF_SCHEMA_PATHS.get(str(version))
        if schema_path is None:
            raise ValueError(f"unsupported sealed handoff schema_version: {version!r}")
    _validate(manifest, schema_path, label="sealed evaluation handoff")


def validate_promotion_schema(promotion: dict[str, Any]) -> None:
    _validate(promotion, PROMOTION_SCHEMA_PATH, label="sealed evaluation promotion")


def load_trust_policy(path: Path) -> TrustPolicy:
    """Load key references from an external policy; raw HMAC keys stay outside Git."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"schema_version", "recipient", "trusted_keys"}:
        raise ValueError("trust policy must contain only schema_version, recipient, and trusted_keys")
    if data["schema_version"] != "sealed-eval-trust-policy-v1":
        raise ValueError("unsupported sealed evaluation trust policy")
    recipient = data["recipient"]
    records = data["trusted_keys"]
    if not isinstance(recipient, str) or not recipient or not isinstance(records, list) or not records:
        raise ValueError("trust policy requires a recipient and at least one trusted key")
    keys: dict[tuple[str, str, str, str], bytes] = {}
    for record in records:
        required = {"issuer", "key_id", "algorithm", "key_file", "usages"}
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError(
                "each trusted key requires issuer, key_id, algorithm, key_file, and usages"
            )
        algorithm = str(record["algorithm"])
        if algorithm != "hmac-sha256-v2":
            raise ValueError(f"unsupported trusted-key algorithm: {algorithm}")
        base_identity = (str(record["issuer"]), str(record["key_id"]), algorithm)
        usages = record["usages"]
        if (
            not all(base_identity[:2])
            or not isinstance(usages, list)
            or not usages
            or len(set(usages)) != len(usages)
            or any(usage not in {"handoff", "promotion"} for usage in usages)
        ):
            raise ValueError("trusted key identity and usages must be non-empty, valid, and unique")
        key_path = _resolve_public_file(path.parent, str(record["key_file"]))
        if not key_path.is_file():
            raise ValueError(
                f"trusted key file does not exist for {base_identity[0]}/{base_identity[1]}"
            )
        key = key_path.read_bytes()
        if not key:
            raise ValueError(f"trusted key file is empty for {base_identity[0]}/{base_identity[1]}")
        for usage in usages:
            identity = (*base_identity, usage)
            if identity in keys:
                raise ValueError("trusted key identities and usages must be unique")
            keys[identity] = key
    return TrustPolicy(recipient=recipient, keys=keys)


def _resolve_public_file(root: Path, relative_path: str) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError(f"handoff file path must be relative: {relative_path}")
    root = root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"handoff file escapes bundle root: {relative_path}")
    return candidate


def _verify_declared_file(root: Path, declared: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    path = _resolve_public_file(root, str(declared["path"]))
    if not path.is_file():
        raise ValueError(f"sealed handoff file does not exist: {label}")
    actual = _file_identity(path)
    expected = {"sha256": declared["sha256"], "size_bytes": declared["size_bytes"]}
    if actual != expected:
        raise ValueError(f"sealed handoff file identity mismatch: {label}")
    return actual


def _parse_datetime(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _enforce_not_before(manifest: Mapping[str, Any], *, now: datetime | None = None) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    not_before = _parse_datetime(str(manifest["not_before"]), label="not_before")
    if current < not_before:
        raise ValueError("sealed handoff not_before time has not been reached")


def _trusted_signature(
    document: dict[str, Any],
    *,
    trust_policy: TrustPolicy,
    domain: bytes,
    require_recipient: bool,
    usage: str,
) -> dict[str, str]:
    if require_recipient and document.get("recipient") != trust_policy.recipient:
        raise ValueError("sealed handoff recipient is not authorized by the trust policy")
    signature = document["signature"]
    identity = (
        str(document["issuer"]),
        str(signature["key_id"]),
        str(signature["algorithm"]),
        usage,
    )
    key = trust_policy.keys.get(identity)
    if key is None:
        raise ValueError(
            f"untrusted sealed evaluation issuer/key usage: {identity[0]}/{identity[1]}/{usage}"
        )
    expected = hmac.new(
        key,
        _canonical_v2_signature_payload(document, domain=domain),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, str(signature["value"])):
        raise ValueError("sealed evaluation trusted signature mismatch")
    return {
        "algorithm": identity[2],
        "key_id": identity[1],
        "issuer": identity[0],
        "usage": usage,
    }


def _finalize_receipt(core: dict[str, Any]) -> dict[str, Any]:
    return {**core, "verification_receipt_sha256": _sha256_json(core)}


def _validate_receipt(receipt: dict[str, Any], *, expected_version: str) -> None:
    receipt_hash = receipt.get("verification_receipt_sha256")
    if not isinstance(receipt_hash, str):
        raise ValueError("verification receipt is missing its SHA")
    core = dict(receipt)
    core.pop("verification_receipt_sha256", None)
    if not hmac.compare_digest(receipt_hash, _sha256_json(core)):
        raise ValueError("verification receipt SHA mismatch")
    if receipt.get("schema_version") != expected_version:
        raise ValueError(f"unexpected verification receipt version: {receipt.get('schema_version')!r}")
    if receipt.get("release_eligible_handoff") is not False:
        raise ValueError("verification receipts cannot grant release eligibility")


def _load_json_document(value: Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Path):
        loaded = json.loads(value.read_text(encoding="utf-8"))
    else:
        loaded = dict(value)
    if not isinstance(loaded, dict):
        raise ValueError("expected a JSON object")
    return loaded


def _v1_public_verification(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    manifest_identity: Mapping[str, Any],
    signature_key: bytes | None,
    require_signature: bool,
) -> dict[str, Any]:
    root = manifest_path.parent
    verified_files = {
        name: _verify_declared_file(root, manifest["files"][name], label=name)
        for name in ("public_cases", "fixture_pack")
    }
    signature = manifest.get("signature")
    if require_signature and signature is None:
        raise ValueError("legacy sealed handoff requires a signature for integrity verification")
    integrity_verified = False
    if signature is not None and signature_key is not None:
        expected = hmac.new(signature_key, _canonical_signature_payload(manifest), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(signature["value"])):
            raise ValueError("legacy sealed handoff signature mismatch")
        integrity_verified = True
    elif signature is not None and require_signature:
        raise ValueError("legacy signature verification key is required")
    core = {
        "schema_version": "sealed-eval-public-verification-v1",
        "handoff_id": manifest["handoff_id"],
        "manifest_sha256": manifest_identity["sha256"],
        "legacy_v1": True,
        "schema_valid": True,
        "public_files_verified": verified_files,
        "private_oracle_identity": dict(manifest["files"]["private_oracles"]),
        "manifest_integrity_verified": integrity_verified,
        "signature_verified": integrity_verified,
        "execution_eligible": False,
        "release_eligible_handoff": False,
    }
    return _finalize_receipt(core)


def verify_public_handoff(
    manifest_path: Path,
    *,
    signature_key: bytes | None = None,
    require_signature: bool = True,
    trust_policy: TrustPolicy | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    manifest, manifest_identity, _ = _read_json_snapshot(
        manifest_path, label="sealed evaluation handoff"
    )
    validate_handoff_schema(manifest)
    if manifest["schema_version"] == "sealed-eval-handoff-v1":
        return _v1_public_verification(
            manifest_path,
            manifest,
            manifest_identity=manifest_identity,
            signature_key=signature_key,
            require_signature=require_signature,
        )
    if trust_policy is None:
        raise ValueError("v2 sealed handoff requires an explicit trust policy")
    if signature_key is not None:
        raise ValueError("an arbitrary signature key cannot authorize a v2 sealed handoff")

    signature_identity = _trusted_signature(
        manifest,
        trust_policy=trust_policy,
        domain=HANDOFF_V2_SIGNATURE_DOMAIN,
        require_recipient=True,
        usage="handoff",
    )
    _enforce_not_before(manifest, now=now)
    root = manifest_path.parent
    public_artifacts = {
        "public_cases": _verify_declared_file(
            root, manifest["files"]["public_cases"], label="public_cases"
        ),
        "fixture_pack": _verify_declared_file(
            root, manifest["files"]["fixture_pack"], label="fixture_pack"
        ),
    }
    for name, declared in manifest["execution_contract"]["artifacts"].items():
        public_artifacts[name] = _verify_declared_file(root, declared, label=name)

    execution_contract = manifest["execution_contract"]
    core = {
        "schema_version": PUBLIC_RECEIPT_VERSION,
        "handoff_id": manifest["handoff_id"],
        "manifest_sha256": manifest_identity["sha256"],
        "issuer": manifest["issuer"],
        "recipient": manifest["recipient"],
        "schema_valid": True,
        "legacy_v1": False,
        "trusted_signature_verified": True,
        "signature": signature_identity,
        "not_before": manifest["not_before"],
        "not_before_satisfied": True,
        "public_artifacts_verified": public_artifacts,
        "private_oracle_identity": dict(manifest["files"]["private_oracles"]),
        "execution_contract": {
            name: copy.deepcopy(execution_contract[name])
            for name in (
                "provider",
                "model",
                "arms",
                "repeat_count",
                "order_seed",
                "runner_revision",
            )
        },
        "execution_contract_sha256": _sha256_json(execution_contract),
        "execution_eligible": True,
        "release_eligible_handoff": False,
    }
    return _finalize_receipt(core)


def verify_private_oracles(
    manifest_path: Path,
    private_oracles_path: Path,
    *,
    trust_policy: TrustPolicy | None = None,
    public_verification: Path | Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    manifest, manifest_identity, _ = _read_json_snapshot(
        manifest_path, label="sealed evaluation handoff"
    )
    validate_handoff_schema(manifest)
    expected = dict(manifest["files"]["private_oracles"])
    actual = _file_identity(private_oracles_path)
    if actual != expected:
        raise ValueError("private oracle identity mismatch")
    if manifest["schema_version"] == "sealed-eval-handoff-v1":
        return {
            "schema_version": "sealed-eval-private-verification-v1",
            "handoff_id": manifest["handoff_id"],
            "manifest_sha256": manifest_identity["sha256"],
            "legacy_v1": True,
            "private_oracles_verified": True,
            **actual,
            "release_eligible_handoff": False,
        }
    if trust_policy is None or public_verification is None:
        raise ValueError("v2 private verification requires a trust policy and public receipt")
    _trusted_signature(
        manifest,
        trust_policy=trust_policy,
        domain=HANDOFF_V2_SIGNATURE_DOMAIN,
        require_recipient=True,
        usage="handoff",
    )
    _enforce_not_before(manifest, now=now)
    public_receipt = _load_json_document(public_verification)
    _validate_receipt(public_receipt, expected_version=PUBLIC_RECEIPT_VERSION)
    manifest_sha256 = str(manifest_identity["sha256"])
    if public_receipt.get("manifest_sha256") != manifest_sha256:
        raise ValueError("private verification manifest identity mismatch")
    if public_receipt.get("handoff_id") != manifest["handoff_id"]:
        raise ValueError("private verification handoff identity mismatch")
    if public_receipt.get("execution_eligible") is not True:
        raise ValueError("public verification receipt is not execution eligible")
    core = {
        "schema_version": PRIVATE_RECEIPT_VERSION,
        "handoff_id": manifest["handoff_id"],
        "manifest_sha256": manifest_sha256,
        "issuer": manifest["issuer"],
        "public_verification_sha256": public_receipt["verification_receipt_sha256"],
        "trusted_manifest_verified": True,
        "private_oracles_verified": True,
        "private_oracle_identity": actual,
        "release_eligible_handoff": False,
    }
    return _finalize_receipt(core)


def _case_ids_from_payload(payload_bytes: bytes) -> list[str]:
    payload = json.loads(payload_bytes.decode("utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list) or not cases:
        raise ValueError("sealed public cases must contain a non-empty cases list")
    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(cases) or any(not isinstance(item, str) or not item for item in case_ids):
        raise ValueError("each sealed public case requires a non-empty case_id")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("sealed public case IDs must be unique")
    return case_ids


def _read_verified_payload(
    path: Path,
    expected: Mapping[str, Any],
    *,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"promotion artifact does not exist: {label}") from exc
    actual = _payload_identity(payload)
    expected_identity = {
        "sha256": expected["sha256"],
        "size_bytes": expected["size_bytes"],
    }
    if actual != expected_identity:
        raise ValueError(f"promotion artifact identity mismatch: {label}")
    return actual, payload


def _json_object_from_payload(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"promotion {label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"promotion {label} must be a JSON object")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _verify_private_receipt_semantics(
    receipt: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    public_receipt_sha256: str,
) -> None:
    required = {
        "handoff_id": manifest["handoff_id"],
        "manifest_sha256": manifest_sha256,
        "issuer": manifest["issuer"],
        "public_verification_sha256": public_receipt_sha256,
        "trusted_manifest_verified": True,
        "private_oracles_verified": True,
        "private_oracle_identity": dict(manifest["files"]["private_oracles"]),
        "release_eligible_handoff": False,
    }
    for field, expected in required.items():
        if receipt.get(field) != expected:
            raise ValueError(f"promotion private receipt has invalid {field}")


def _verify_response_trace(
    payload: bytes,
    *,
    case_ids: list[str],
    contract: Mapping[str, Any],
    run: Mapping[str, Any],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(payload.decode("utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"response trace line {line_number} is not valid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"response trace line {line_number} must be a JSON object")
        records.append(record)
    if len(records) != run["response_count"]:
        raise ValueError("response trace count differs from promotion response_count")

    expected_tuples = {
        (case_id, arm, repeat_index)
        for case_id in case_ids
        for arm in contract["arms"]
        for repeat_index in range(1, contract["repeat_count"] + 1)
    }
    observed_tuples: set[tuple[str, str, int]] = set()
    first_case_order: list[str] = []
    provider_request_ids: list[str] = []
    for sequence_index, record in enumerate(records):
        if record.get("schema_version") != "sealed-eval-response-trace-v1":
            raise ValueError("response trace has an unsupported schema_version")
        if record.get("sequence_index") != sequence_index:
            raise ValueError("response trace sequence_index is not contiguous")
        case_id = record.get("case_id")
        arm = record.get("arm")
        repeat_index = record.get("repeat_index")
        if not isinstance(case_id, str) or not isinstance(arm, str) or not isinstance(repeat_index, int):
            raise ValueError("response trace case_id, arm, and repeat_index are required")
        sample_identity = (case_id, arm, repeat_index)
        if sample_identity not in expected_tuples or sample_identity in observed_tuples:
            raise ValueError("response trace sample coverage is invalid or duplicated")
        observed_tuples.add(sample_identity)
        if case_id not in first_case_order:
            first_case_order.append(case_id)
        request_ids = record.get("provider_request_ids")
        if (
            not isinstance(request_ids, list)
            or not request_ids
            or len(set(request_ids)) != len(request_ids)
            or any(not isinstance(item, str) or not item for item in request_ids)
        ):
            raise ValueError("each response trace row requires unique provider request IDs")
        provider_request_ids.extend(request_ids)
        if not _is_sha256(record.get("response_sha256")) or not _is_sha256(
            record.get("trace_sha256")
        ):
            raise ValueError("each response trace row requires response and trace SHA256 values")
    if observed_tuples != expected_tuples:
        raise ValueError("response trace does not cover every case, arm, and repeat")
    if first_case_order != case_ids:
        raise ValueError("response trace does not preserve full manifest case order")
    if len(set(provider_request_ids)) != len(provider_request_ids):
        raise ValueError("provider request IDs must be unique across the response trace")
    if provider_request_ids != run["provider_request_ids"]:
        raise ValueError("promotion provider request IDs do not match the response trace")
    return {
        "response_count": len(records),
        "sample_coverage_sha256": _sha256_json(sorted(expected_tuples)),
        "provider_request_ids_sha256": _sha256_json(provider_request_ids),
    }


def _verify_result_artifacts(
    *,
    public_report_payload: bytes,
    private_scores_payload: bytes,
    response_trace_payload: bytes,
    artifact_identities: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    promotion: Mapping[str, Any],
    case_ids: list[str],
) -> dict[str, Any]:
    run = promotion["run"]
    contract = manifest["execution_contract"]
    trace_summary = _verify_response_trace(
        response_trace_payload,
        case_ids=case_ids,
        contract=contract,
        run=run,
    )
    private_scores = _json_object_from_payload(private_scores_payload, label="private scores")
    if private_scores.get("schema_version") != "sealed-eval-private-scores-v1":
        raise ValueError("private scores have an unsupported schema_version")
    if private_scores.get("handoff_id") != manifest["handoff_id"]:
        raise ValueError("private scores handoff identity mismatch")
    if private_scores.get("manifest_sha256") != manifest_sha256:
        raise ValueError("private scores manifest identity mismatch")
    if private_scores.get("response_trace_sha256") != artifact_identities["response_trace"]["sha256"]:
        raise ValueError("private scores do not bind the response trace")
    case_scores = private_scores.get("case_scores")
    if (
        not isinstance(case_scores, list)
        or any(not isinstance(item, dict) for item in case_scores)
        or [item.get("case_id") for item in case_scores] != case_ids
    ):
        raise ValueError("private scores do not cover every case in manifest order")
    if any(not isinstance(item.get("success"), bool) for item in case_scores):
        raise ValueError("private case scores require a boolean success endpoint")

    public_report = _json_object_from_payload(public_report_payload, label="public report")
    expected_report_fields = {
        "schema_version": "sealed-eval-public-report-v1",
        "handoff_id": manifest["handoff_id"],
        "manifest_sha256": manifest_sha256,
        "run": dict(run),
        "response_trace_sha256": artifact_identities["response_trace"]["sha256"],
        "private_scores_sha256": artifact_identities["private_scores"]["sha256"],
    }
    for field, expected in expected_report_fields.items():
        if public_report.get(field) != expected:
            raise ValueError(f"public report has invalid {field}")
    return trace_summary


def verify_promotion(
    promotion_path: Path,
    *,
    manifest_path: Path,
    public_verification_path: Path,
    private_verification_path: Path,
    public_report_path: Path,
    private_scores_path: Path,
    response_trace_path: Path,
    trust_policy: TrustPolicy,
    now: datetime | None = None,
) -> dict[str, Any]:
    promotion, promotion_identity, _ = _read_json_snapshot(
        promotion_path, label="sealed evaluation promotion"
    )
    validate_promotion_schema(promotion)
    manifest, manifest_identity, _ = _read_json_snapshot(
        manifest_path, label="sealed evaluation handoff"
    )
    validate_handoff_schema(manifest)
    if manifest["schema_version"] != "sealed-eval-handoff-v2":
        raise ValueError("only a v2 handoff can be promoted")
    if promotion["handoff_id"] != manifest["handoff_id"] or promotion["issuer"] != manifest["issuer"]:
        raise ValueError("promotion and handoff identity mismatch")
    _trusted_signature(
        promotion,
        trust_policy=trust_policy,
        domain=PROMOTION_V1_SIGNATURE_DOMAIN,
        require_recipient=False,
        usage="promotion",
    )
    manifest_sha256 = str(manifest_identity["sha256"])
    if promotion["manifest_sha256"] != manifest_sha256:
        raise ValueError("promotion manifest SHA mismatch")

    expected_public = verify_public_handoff(manifest_path, trust_policy=trust_policy, now=now)
    if expected_public["manifest_sha256"] != manifest_sha256:
        raise ValueError("sealed handoff changed during promotion verification")
    public_receipt = _load_json_document(public_verification_path)
    _validate_receipt(public_receipt, expected_version=PUBLIC_RECEIPT_VERSION)
    if public_receipt["verification_receipt_sha256"] != expected_public["verification_receipt_sha256"]:
        raise ValueError("promotion public verification receipt mismatch")
    if promotion["public_verification_sha256"] != public_receipt["verification_receipt_sha256"]:
        raise ValueError("promotion public verification SHA mismatch")

    private_receipt = _load_json_document(private_verification_path)
    _validate_receipt(private_receipt, expected_version=PRIVATE_RECEIPT_VERSION)
    if private_receipt.get("manifest_sha256") != manifest_sha256:
        raise ValueError("promotion private receipt manifest mismatch")
    if private_receipt.get("handoff_id") != manifest["handoff_id"]:
        raise ValueError("promotion private receipt handoff mismatch")
    if private_receipt.get("public_verification_sha256") != public_receipt["verification_receipt_sha256"]:
        raise ValueError("promotion private receipt does not bind the public receipt")
    if promotion["private_verification_sha256"] != private_receipt["verification_receipt_sha256"]:
        raise ValueError("promotion private verification SHA mismatch")
    _verify_private_receipt_semantics(
        private_receipt,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        public_receipt_sha256=public_receipt["verification_receipt_sha256"],
    )

    artifact_paths = {
        "public_report": public_report_path,
        "private_scores": private_scores_path,
        "response_trace": response_trace_path,
    }
    artifact_identities: dict[str, dict[str, Any]] = {}
    artifact_payloads: dict[str, bytes] = {}
    for name, path in artifact_paths.items():
        identity, payload = _read_verified_payload(
            path,
            promotion["artifacts"][name],
            label=name,
        )
        artifact_identities[name] = identity
        artifact_payloads[name] = payload

    not_before = _parse_datetime(str(manifest["not_before"]), label="not_before")
    executed_at = _parse_datetime(str(promotion["executed_at"]), label="executed_at")
    adjudicated_at = _parse_datetime(str(promotion["adjudicated_at"]), label="adjudicated_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if executed_at < not_before:
        raise ValueError("promotion execution predates the handoff not_before time")
    if adjudicated_at < executed_at:
        raise ValueError("promotion adjudication predates execution")
    if adjudicated_at > current:
        raise ValueError("promotion adjudication time is in the future")

    public_cases_path = _resolve_public_file(
        manifest_path.parent,
        str(manifest["files"]["public_cases"]["path"]),
    )
    _, public_cases_payload = _read_verified_payload(
        public_cases_path,
        manifest["files"]["public_cases"],
        label="public_cases",
    )
    case_ids = _case_ids_from_payload(public_cases_payload)
    run = promotion["run"]
    contract = manifest["execution_contract"]
    expected_responses = len(case_ids) * len(contract["arms"]) * contract["repeat_count"]
    if run["case_count"] != len(case_ids):
        raise ValueError("promotion case count does not cover the full manifest")
    if run["response_count"] != expected_responses:
        raise ValueError("promotion response count does not cover all arms and repeats")
    if run["complete_case_ids_sha256"] != _sha256_json(case_ids):
        raise ValueError("promotion complete case ID order mismatch")
    if len(run["provider_request_ids"]) < run["response_count"]:
        raise ValueError("promotion provider request IDs do not cover every response")
    if run["code_revision"] != contract["runner_revision"]:
        raise ValueError("promotion code revision differs from the frozen runner revision")
    trace_summary = _verify_result_artifacts(
        public_report_payload=artifact_payloads["public_report"],
        private_scores_payload=artifact_payloads["private_scores"],
        response_trace_payload=artifact_payloads["response_trace"],
        artifact_identities=artifact_identities,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        promotion=promotion,
        case_ids=case_ids,
    )

    return {
        "handoff_id": manifest["handoff_id"],
        "manifest_sha256": manifest_sha256,
        "promotion_sha256": promotion_identity["sha256"],
        "trusted_promotion_signature_verified": True,
        "public_verification_sha256": public_receipt["verification_receipt_sha256"],
        "private_verification_sha256": private_receipt["verification_receipt_sha256"],
        "artifacts_verified": artifact_identities,
        "response_trace_verified": trace_summary,
        "full_manifest_order_verified": True,
        "release_eligible_handoff": True,
    }


def _write_receipt(path: Path | None, result: Mapping[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    public = subparsers.add_parser("verify-public")
    public.add_argument("--manifest", type=Path, required=True)
    public.add_argument("--trust-policy", type=Path)
    public.add_argument("--signature-key-file", type=Path)
    public.add_argument("--allow-unsigned-debug", action="store_true")
    public.add_argument("--receipt-out", type=Path)
    private = subparsers.add_parser("verify-private")
    private.add_argument("--manifest", type=Path, required=True)
    private.add_argument("--private-oracles", type=Path, required=True)
    private.add_argument("--trust-policy", type=Path)
    private.add_argument("--public-receipt", type=Path)
    private.add_argument("--receipt-out", type=Path)
    promotion = subparsers.add_parser("verify-promotion")
    promotion.add_argument("--promotion", type=Path, required=True)
    promotion.add_argument("--manifest", type=Path, required=True)
    promotion.add_argument("--public-receipt", type=Path, required=True)
    promotion.add_argument("--private-receipt", type=Path, required=True)
    promotion.add_argument("--public-report", type=Path, required=True)
    promotion.add_argument("--private-scores", type=Path, required=True)
    promotion.add_argument("--response-trace", type=Path, required=True)
    promotion.add_argument("--trust-policy", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "verify-public":
        key = args.signature_key_file.read_bytes() if args.signature_key_file else None
        policy = load_trust_policy(args.trust_policy) if args.trust_policy else None
        result = verify_public_handoff(
            args.manifest,
            signature_key=key,
            require_signature=not args.allow_unsigned_debug,
            trust_policy=policy,
        )
        _write_receipt(args.receipt_out, result)
    elif args.command == "verify-private":
        policy = load_trust_policy(args.trust_policy) if args.trust_policy else None
        result = verify_private_oracles(
            args.manifest,
            args.private_oracles,
            trust_policy=policy,
            public_verification=args.public_receipt,
        )
        _write_receipt(args.receipt_out, result)
    else:
        policy = load_trust_policy(args.trust_policy)
        result = verify_promotion(
            args.promotion,
            manifest_path=args.manifest,
            public_verification_path=args.public_receipt,
            private_verification_path=args.private_receipt,
            public_report_path=args.public_report,
            private_scores_path=args.private_scores,
            response_trace_path=args.response_trace,
            trust_policy=policy,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
