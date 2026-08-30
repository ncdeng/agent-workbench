from cst_agent_workbench.agent.memory import StructuredMemory


class _FakeAgent:
    def __init__(self):
        self.session = type("Session", (), {"memory": StructuredMemory()})()
        self.cst = type("CST", (), {"project_path": "D:/demo/project.cst"})()
        self.last_results = {}
        self.last_farfield_results = {}
        self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False}
        self.last_execution_mode = ""
        self.opt_state = type(
            "Opt",
            (),
            {
                "target_mode": "at_f0",
                "target_freq": 9.4,
                "target_db": -10.0,
                "history": [
                    {
                        "round": 1,
                        "strategy": "patch_L down",
                        "proposal_reason": "resonance too low",
                        "changed_params": "patch_L: 10 -> 9.8",
                        "metric_value": -12.3,
                        "min_s11": -13.0,
                        "min_freq": 9.35,
                        "at_f0_s11": -12.3,
                        "met": True,
                    }
                ],
                "round": 1,
                "baseline_metric_value": -8.0,
                "best_metric_value": -12.3,
                "best_round": 1,
                "stagnation_count": 0,
            },
        )()

    def _summarize_last_results(self):
        return {
            "available": True,
            "type": "s11",
            "item": "S1,1",
            "target_freq_ghz": 9.4,
            "target_s11_db": -12.3,
            "min_s11_db": -13.0,
            "min_freq_ghz": 9.35,
            "message": "ok",
        }

    def _refresh_session_memory_from_runtime(self, persist: bool = False):
        from cst_agent_workbench.agent.agent import CSTAgent

        CSTAgent._refresh_session_memory_from_runtime(self, persist=persist)

    def _remember_user_goal(self, user_message: str):
        from cst_agent_workbench.agent.agent import CSTAgent

        CSTAgent._remember_user_goal(self, user_message)

    def _remember_recent_exchange(self, user_message: str, assistant_message: str):
        from cst_agent_workbench.agent.agent import CSTAgent

        CSTAgent._remember_recent_exchange(self, user_message, assistant_message)

    def _remember_failure(self, reason: str):
        from cst_agent_workbench.agent.agent import CSTAgent

        CSTAgent._remember_failure(self, reason)

    def _remember_strategy(self, **kwargs):
        from cst_agent_workbench.agent.agent import CSTAgent

        CSTAgent._remember_strategy(self, **kwargs)

    def _remember_rollback(self, **kwargs):
        from cst_agent_workbench.agent.agent import CSTAgent

        CSTAgent._remember_rollback(self, **kwargs)


def test_memory_helpers_update_structured_memory():
    agent = _FakeAgent()

    agent._remember_user_goal("把 9.4 GHz 的 S11 优化到 -10 dB 以下")
    agent._remember_recent_exchange("继续优化", "第 1 轮已改善到 -12.3 dB")
    agent._remember_failure("read_result: no data")
    agent._remember_strategy(
        round_num=1,
        strategy="patch_L down",
        proposal_reason="resonance too low",
        changed_params="patch_L: 10 -> 9.8",
        metric_value=-12.3,
        improved=True,
        met=True,
    )
    agent._remember_rollback(round_num=1, reason="stagnation", snapshot={"patch_L": 9.8})
    agent._refresh_session_memory_from_runtime()

    memory = agent.session.memory
    assert memory.conversation.user_goal == "把 9.4 GHz 的 S11 优化到 -10 dB 以下"
    assert "继续优化" in memory.conversation.recent_summary
    assert memory.decisions.failure_reasons[-1] == "read_result: no data"
    assert memory.decisions.recent_strategies[-1]["strategy"] == "patch_L down"
    assert memory.decisions.rollback_points[-1]["reason"] == "stagnation"
    assert memory.workspace.project_path == "D:/demo/project.cst"
    assert memory.workspace.last_results_summary["target_s11_db"] == -12.3
    assert memory.decisions.best_so_far["round"] == 1
