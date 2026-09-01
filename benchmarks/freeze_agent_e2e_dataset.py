"""Freeze or verify an Agent E2E dataset manifest.

The manifest proves which exact bytes were evaluated and records family/provenance
metadata.  It does not by itself make a developer-visible dataset blinded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.agent_e2e_ablation_runner import validate_dataset_schema  # noqa: E402


def sha256_aliases(raw: bytes) -> set[str]:
    """Return SHA-256 hex digests for raw, LF, and CRLF encodings of the same text.

    Windows working trees often expand LF blobs to CRLF. Dataset identity is the
    JSON text, not the checkout's newline bytes, so Linux CI and Windows checkout
    must accept one another.
    """

    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return {
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(lf).hexdigest(),
        hashlib.sha256(crlf).hexdigest(),
    }


def recorded_sha_matches(recorded: str, raw: bytes) -> bool:
    return bool(recorded) and recorded in sha256_aliases(raw)


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_manifest(dataset_path: Path) -> dict[str, Any]:
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw.decode("utf-8"))
    validate_dataset_schema(dataset)
    cases = list(dataset["cases"])
    family_fields = ("scenario_family", "design_family", "failure_family")
    return {
        "schema_version": "agent-e2e-manifest-v1",
        "dataset_id": dataset["dataset_id"],
        "dataset_split": dataset["split"],
        "dataset_role": dataset.get("dataset_role", "unspecified"),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": len(cases),
        "case_ids": [str(case["case_id"]) for case in cases],
        "category_counts": dict(sorted(Counter(str(case["category"]) for case in cases).items())),
        "family_counts": {
            field: dict(
                sorted(Counter(str(case.get(field) or "unspecified") for case in cases).items())
            )
            for field in family_fields
        },
        "dataset_frozen_at": dataset.get("frozen_at"),
        "annotation_policy": dataset.get("annotation_policy"),
        "generation_provenance": dataset.get("generation_provenance"),
        "manifest_created_at": datetime.now(timezone.utc).isoformat(),
        "code_revision_at_freeze": _git_revision(),
        "source_path": str(dataset_path.resolve()),
        "honest_boundary": (
            "A matching SHA proves dataset immutability only. Blinded status depends on "
            "the recorded authoring and access-control process."
        ),
    }


def verify_manifest(dataset_path: Path, manifest_path: Path) -> list[str]:
    expected = build_manifest(dataset_path)
    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    stable_fields = (
        "schema_version",
        "dataset_id",
        "dataset_split",
        "dataset_role",
        "dataset_sha256",
        "case_count",
        "case_ids",
        "category_counts",
        "family_counts",
        "dataset_frozen_at",
        "annotation_policy",
        "generation_provenance",
    )
    mismatches = []
    dataset_bytes = dataset_path.read_bytes()
    for field in stable_fields:
        if field == "dataset_sha256":
            if not recorded_sha_matches(str(actual.get(field) or ""), dataset_bytes):
                mismatches.append(field)
            continue
        if actual.get(field) != expected.get(field):
            mismatches.append(field)
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.verify:
        mismatches = verify_manifest(args.dataset, args.manifest)
        if mismatches:
            print(f"manifest mismatch: {', '.join(mismatches)}", file=sys.stderr)
            return 1
        print("manifest verified")
        return 0

    if args.manifest.exists() and not args.force:
        raise FileExistsError(f"manifest already exists: {args.manifest}; pass --force to replace")
    manifest = build_manifest(args.dataset)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.manifest} ({manifest['dataset_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
