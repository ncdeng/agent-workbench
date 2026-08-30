from __future__ import annotations

import json
from pathlib import Path
from cst_agent_workbench.agent.memory import StructuredMemory
from cst_agent_workbench.agent.project_lifecycle import validate_cst_project_path
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.agent.tool_runtime import execute_tool


class _LifecycleCST:
    def __init__(self):
        self.project_path = "D:\\old\\old.cst"
        self.calls = []

    def new_project(self, path):
        self.calls.append(("new", path))
        self.project_path = path
        return {"success": True, "message": "created", "project_file": path}

    def open_project(self, path):
        self.calls.append(("open", path))
        self.project_path = path
        return {"success": True, "message": "opened", "project_file": path}

    def save_project(self, include_results=True):
        self.calls.append(("save", include_results))
        return {"success": True, "message": "saved", "project_file": self.project_path}

    def save_project_as(self, path, include_results=True):
        self.calls.append(("save_as", path, include_results))
        self.project_path = path
        return {"success": True, "message": "saved as", "project_file": path}

    def close_project(self, path):
        self.calls.append(("close", path))
        self.project_path = ""
        return {"success": True, "message": "closed", "project_file": path}


class _LifecycleAgent:
    def __init__(self):
        self.cst = _LifecycleCST()
        self.session = AgentSession(session_id="lifecycle")
        self.session.memory = StructuredMemory()
        self.session.memory.workspace.project_path = self.cst.project_path
        self.session.artifacts.last_results = {"success": True}
        self.session.artifacts.tool_results = {"old": {"success": True}}
        self.last_tool_message = ""
        self.tool_events = []
        self.last_results = {}
        self.current_trace = {"turns": [{"tool_calls": []}]}
        self.trace_enabled = True
        self._active_tool_call_id = "lifecycle-call"

    def _get_phase(self, name):
        return f"phase:{name}"

    def _remember_failure(self, reason):
        pass

    def _refresh_session_memory_from_runtime(self, persist=False):
        self.session.memory.workspace.project_path = self.cst.project_path


def test_output_path_policy_rejects_relative_non_cst_and_c_drive(tmp_path):
    for value in ("relative.cst", "D:\\tmp\\project.txt", "C:\\tmp\\project.cst"):
        try:
            validate_cst_project_path(
                value,
                must_exist=False,
                output_must_be_on_d_drive=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected path rejection: {value}")


def test_create_project_switches_project_scoped_state(monkeypatch):
    target = "D:\\cst_agent_rag_data\\projects\\new_agent_project.cst"
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr("os.path.exists", lambda path: False)
    agent = _LifecycleAgent()
    agent.session.tool_approvals.issue(
        "execute_vba_script",
        {"vba_code": "Sub Main()\nEnd Sub", "description": "old project"},
        actor="local-desktop-user",
    )

    result = json.loads(execute_tool(agent, "create_cst_project", {"project_path": target}))

    assert result["success"] is True
    assert agent.cst.calls == [("new", target)]
    assert agent.session.memory.workspace.project_path == target
    assert agent.session.artifacts.last_results == {}
    assert agent.session.artifacts.results_invalidated is True
    assert agent.session.metadata["project_transition"]["reason"] == "created_new_cst_project"
    assert agent.session.tool_approvals.projection()["active_grant_count"] == 0


def test_save_and_close_is_save_first_and_clears_project_memory():
    agent = _LifecycleAgent()

    result = json.loads(execute_tool(agent, "save_and_close_cst_project", {}))

    assert result["success"] is True
    assert agent.cst.calls == [("save", True), ("close", "D:\\old\\old.cst")]
    assert agent.cst.project_path == ""
    assert agent.session.memory.workspace.project_path == ""
    assert agent.session.metadata["project_transition"]["reason"] == "saved_and_closed_cst_project"


def test_save_failure_prevents_close():
    agent = _LifecycleAgent()
    agent.cst.save_project = lambda include_results=True: {
        "success": False,
        "message": "disk full",
    }

    result = json.loads(execute_tool(agent, "save_and_close_cst_project", {}))

    assert result["success"] is False
    assert result["stage"] == "save"
    assert all(call[0] != "close" for call in agent.cst.calls)
    assert agent.cst.project_path == "D:\\old\\old.cst"
    assert agent.session.memory.workspace.project_path == "D:\\old\\old.cst"
