from dataclasses import dataclass
from typing import Optional

from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.results.reader import ResultsReader


@dataclass
class AppState:
    cst: CSTController
    agent: CSTAgent
    results_reader: ResultsReader
    session: object
    opt_settings: dict
    farfield_settings: dict
    farfield_export_settings: dict

    def close(self) -> None:
        brain = getattr(self.agent, "_pi_brain", None)
        close = getattr(brain, "close", None)
        if callable(close):
            close()


_APP_STATE: Optional[AppState] = None


def _build_app_state(dry_run: bool = False) -> AppState:
    cst = CSTController()
    if dry_run:
        cst.offline_mode = True
    agent = CSTAgent(cst)
    results_reader = ResultsReader()
    return AppState(
        cst=cst,
        agent=agent,
        results_reader=results_reader,
        session=agent.session,
        opt_settings={
            "mode": config.OPT_TARGET_MODE,
            "target_freq": 0.0,
            "target_db": config.OPT_TARGET_S11_DB,
            "max_rounds": config.OPT_MAX_ROUNDS,
            "stagnation": config.OPT_STAGNATION_LIMIT,
        },
        farfield_settings={
            "item": "",
            "cut_type": "phi",
            "cut_value_deg": 0.0,
        },
        farfield_export_settings={
            "theta_step_deg": 5.0,
            "phi_step_deg": 5.0,
            "plot_mode": "gain",
            "use_db": True,
        },
    )


def get_app_state(dry_run: bool = False) -> AppState:
    global _APP_STATE
    if _APP_STATE is None:
        _APP_STATE = _build_app_state(dry_run=dry_run)
    return _APP_STATE


def reset_app_state() -> None:
    global _APP_STATE
    if _APP_STATE is not None:
        _APP_STATE.close()
    _APP_STATE = None
