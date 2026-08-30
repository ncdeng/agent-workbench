"""Tests for StructuredMemory JSON serialization / deserialization."""
from __future__ import annotations


from cst_agent_workbench.agent.memory import (
    StructuredMemory,
    load_memory,
    save_memory,
    structured_memory_from_dict,
)


def _make_memory() -> StructuredMemory:
    m = StructuredMemory()
    m.conversation.user_goal = "design a 9.4 GHz patch antenna"
    m.conversation.constraints = ["S11 < -10 dB"]
    m.conversation.recent_summary = "last run: S11=-14 dB"
    m.workspace.project_path = "/tmp/test.cst"
    m.workspace.parameter_summary = {"L": 12.5, "W": 14.0}
    m.decisions.best_so_far = {"round": 3, "metric_value": -14.2}
    m.decisions.failure_reasons = ["solver timeout"]
    return m


def test_save_and_load_roundtrip(tmp_path):
    """save_memory writes a file that load_memory reads back with identical fields."""
    path = str(tmp_path / "mem.json")
    mem = _make_memory()
    assert save_memory(mem, path) is True
    loaded = load_memory(path)
    assert loaded is not None
    assert loaded.conversation.user_goal == mem.conversation.user_goal
    assert loaded.conversation.constraints == mem.conversation.constraints
    assert loaded.workspace.project_path == mem.workspace.project_path
    assert loaded.workspace.parameter_summary == mem.workspace.parameter_summary
    assert loaded.decisions.best_so_far == mem.decisions.best_so_far
    assert loaded.decisions.failure_reasons == mem.decisions.failure_reasons


def test_load_missing_file_returns_none(tmp_path):
    """load_memory returns None when the path does not exist."""
    result = load_memory(str(tmp_path / "nonexistent.json"))
    assert result is None


def test_from_dict_empty_input_returns_defaults():
    """structured_memory_from_dict({}) must not raise and returns blank StructuredMemory."""
    mem = structured_memory_from_dict({})
    assert isinstance(mem, StructuredMemory)
    assert mem.conversation.user_goal == ""
    assert mem.workspace.project_path == ""
    assert mem.decisions.best_so_far == {}
    assert mem.decisions.failure_reasons == []


def test_to_dict_from_dict_to_dict_stable():
    """Round-trip: to_dict -> from_dict -> to_dict produces identical dicts."""
    mem = _make_memory()
    d1 = mem.to_dict()
    mem2 = structured_memory_from_dict(d1)
    d2 = mem2.to_dict()
    assert d1 == d2
