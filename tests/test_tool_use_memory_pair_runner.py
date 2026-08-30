from benchmarks.tool_use_memory_pair_runner import run_memory_pair


def test_tool_use_memory_pair_runs_production_write_persist_recall_and_rerank(tmp_path):
    report = run_memory_pair(artifact_root=tmp_path)

    assert report["all_checks_passed"] is True
    assert all(report["checks"].values())
    assert report["arms"]["no_memory"]["top1"] == "execute_vba_script"
    assert report["arms"]["learned"]["top1"] == "check_cst_status"
    assert set(report["arms"]["learned"]["safe_tool_order"]) == set(
        report["arms"]["no_memory"]["safe_tool_order"]
    )
