"""Shared immutable-source evidence helpers for real CST solver benchmarks."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Mapping


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_sha256(data: bytes) -> str:
    """Hash text source canonically so Git LF and Windows CRLF stay equivalent."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def repository_commit(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_source_bindings(
    repo_root: Path,
    paths: Mapping[str, str],
    *,
    snapshot_root: Path | None = None,
) -> dict[str, dict[str, str | bool]]:
    bindings: dict[str, dict[str, str | bool]] = {}
    commit = repository_commit(repo_root)
    for name, relative in paths.items():
        source_path = repo_root / relative
        binding: dict[str, str | bool] = {
            "path": relative,
            "sha256_lf": _source_sha256(source_path.read_bytes()),
        }
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repo_root,
            check=False,
            capture_output=True,
        )
        binding["commit_blob_match"] = (
            result.returncode == 0 and _source_sha256(result.stdout) == binding["sha256_lf"]
        )
        if snapshot_root is not None:
            snapshot_path = snapshot_root / relative
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_bytes(source_path.read_bytes())
            binding["snapshot_file"] = str(snapshot_path.resolve())
            binding["snapshot_sha256"] = sha256_path(snapshot_path)
        bindings[name] = binding
    return bindings


def bound_sources_match_commit(
    repo_root: Path,
    commit: str,
    bindings: Mapping[str, Mapping[str, str]],
) -> bool:
    for binding in bindings.values():
        relative = str(binding["path"])
        expected = str(binding["sha256_lf"])
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0 or _source_sha256(result.stdout) != expected:
            return False
    return True


def verify_source_bindings(
    repo_root: Path,
    *,
    commit: str,
    bindings: Mapping[str, Mapping[str, str]],
) -> None:
    if not commit:
        raise ValueError("source report 缺少 repository_commit")
    if not bindings:
        raise ValueError("source report 缺少 source_bindings")
    for binding in bindings.values():
        snapshot_file = str(binding.get("snapshot_file") or "")
        snapshot_sha = str(binding.get("snapshot_sha256") or "")
        if snapshot_file:
            snapshot_path = Path(snapshot_file)
            if (
                not snapshot_path.is_file()
                or sha256_path(snapshot_path) != snapshot_sha
                or _source_sha256(snapshot_path.read_bytes()) != str(binding["sha256_lf"])
            ):
                raise ValueError(f"source snapshot mismatch: {snapshot_path}")
            continue
        relative = str(binding["path"])
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0 or _source_sha256(result.stdout) != str(binding["sha256_lf"]):
            raise ValueError(f"source binding is neither commit-bound nor snapshot-bound: {relative}")
