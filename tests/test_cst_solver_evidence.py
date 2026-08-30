from __future__ import annotations

from pathlib import Path

from benchmarks.cst_solver_evidence import (
    bound_sources_match_commit,
    build_source_bindings,
    repository_commit,
    verify_source_bindings,
)


def test_source_bindings_match_current_commit_for_tracked_file():
    repo_root = Path(__file__).resolve().parents[1]
    commit = repository_commit(repo_root)
    bindings = build_source_bindings(repo_root, {"config": "cst_agent_workbench/config.py"})

    assert bound_sources_match_commit(repo_root, commit, bindings) is True
    verify_source_bindings(repo_root, commit=commit, bindings=bindings)


def test_source_bindings_reject_uncommitted_bytes():
    repo_root = Path(__file__).resolve().parents[1]
    commit = repository_commit(repo_root)
    bindings = {
        "controller": {
            "path": "cst_agent_workbench/cst/controller.py",
            "sha256_lf": "0" * 64,
        }
    }

    assert bound_sources_match_commit(repo_root, commit, bindings) is False


def test_source_snapshot_verifies_bytes_outside_commit(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    commit = repository_commit(repo_root)
    bindings = build_source_bindings(
        repo_root,
        {"controller": "cst_agent_workbench/cst/controller.py"},
        snapshot_root=tmp_path / "snapshot",
    )
    bindings["controller"]["commit_blob_match"] = False

    verify_source_bindings(repo_root, commit=commit, bindings=bindings)
