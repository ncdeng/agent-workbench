from types import SimpleNamespace

from cst_agent_workbench.bootstrap import AppState


def test_app_state_close_releases_optional_pi_harness():
    closed = []
    brain = SimpleNamespace(close=lambda: closed.append(True))
    state = AppState(
        cst=SimpleNamespace(),
        agent=SimpleNamespace(_pi_brain=brain),
        results_reader=SimpleNamespace(),
        session=SimpleNamespace(),
        opt_settings={},
        farfield_settings={},
        farfield_export_settings={},
    )

    state.close()

    assert closed == [True]
