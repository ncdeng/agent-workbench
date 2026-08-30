from benchmarks.raw_vba_approval_e2e import run_evaluation


def test_raw_vba_approval_e2e_control_plane():
    report = run_evaluation()

    assert report["summary"] == {
        "checks_passed": 9,
        "checks_total": 9,
        "all_passed": True,
    }
    assert report["raw"]["chat_response"]["hadToolFailure"] is False
    assert len(report["raw"]["controller_calls"]) == 1
