"""Deterministic adapters for full ``CSTAgent.chat`` E2E evaluation.

These fakes preserve the production agent/planner/tool-runtime control flow.
They replace only the external OpenAI provider and CST COM boundary.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from types import SimpleNamespace
from typing import Any, Deque


class FakeCSTAgentAdapter:
    """Small stateful CSTController substitute with fault injection."""

    def __init__(
        self,
        *,
        project_path: str = "D:/cst-agent-e2e/fake_project.cst",
        connected: bool = True,
        offline_mode: bool = False,
    ) -> None:
        self.project_path = project_path
        self.connected = connected
        self.offline_mode = offline_mode
        self.cst_exe = "FAKE"
        self.last_message = ""
        self.calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._responses: dict[str, Deque[dict[str, Any]]] = defaultdict(deque)

    def queue_response(self, method: str, response: dict[str, Any]) -> None:
        self._responses[method].append(dict(response))

    def pending_injected_responses(self) -> dict[str, int]:
        return {
            method: len(responses)
            for method, responses in sorted(self._responses.items())
            if responses
        }

    def _consume(self, method: str, default: dict[str, Any]) -> dict[str, Any]:
        queue = self._responses[method]
        return dict(queue.popleft()) if queue else dict(default)

    def is_connected(self) -> bool:
        return bool(self.connected and not self.offline_mode)

    def get_status(self) -> str:
        self.calls["get_status"].append({})
        return "已连接 CST（fake 在线）" if self.is_connected() else "CST fake 离线"

    def connect(self) -> dict[str, Any]:
        self.calls["connect"].append({})
        result = self._consume(
            "connect",
            {"success": True, "message": "fake reconnected", "project_file": self.project_path},
        )
        if result.get("success"):
            self.connected = True
            self.offline_mode = False
        return result

    def execute_vba(self, vba_code: str, label: str = "cst_agent", timeout: int = 60) -> dict[str, Any]:
        self.calls["execute_vba"].append(
            {"vba_code": vba_code, "label": label, "timeout": timeout}
        )
        return self._consume(
            "execute_vba",
            {
                "success": True,
                "executed": True,
                "message": "fake VBA executed",
                "project_file": self.project_path,
            },
        )

    def run_solver(self, timeout: int = 300) -> dict[str, Any]:
        self.calls["run_solver"].append({"timeout": timeout})
        return self._consume(
            "run_solver",
            {"success": True, "message": "fake solver completed", "project_file": self.project_path},
        )

    def run_solver_with_templates(self, timeout: int = 300) -> dict[str, Any]:
        self.calls["run_solver_with_templates"].append({"timeout": timeout})
        return self._consume(
            "run_solver_with_templates",
            {"success": True, "message": "fake solver/templates completed", "project_file": self.project_path},
        )

    def save_project(self, include_results: bool = True, timeout: int = 60) -> dict[str, Any]:
        self.calls["save_project"].append(
            {"include_results": include_results, "timeout": timeout}
        )
        return self._consume(
            "save_project",
            {
                "success": True,
                "message": "fake project saved",
                "project_file": self.project_path,
                "project_saved": True,
            },
        )

    def new_project(self, project_path: str = "", timeout: int = 60) -> dict[str, Any]:
        self.calls["new_project"].append({"project_path": project_path, "timeout": timeout})
        if project_path:
            self.project_path = project_path
        return {"success": True, "message": "fake project created", "project_file": self.project_path}

    def close_project(self, project_path: str, timeout: int = 30) -> dict[str, Any]:
        self.calls["close_project"].append({"project_path": project_path, "timeout": timeout})
        return {"success": True, "message": "fake project closed", "project_file": project_path}


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False))

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


def _response(*, content: str = "", tool_calls: list[_ToolCall] | None = None, prompt: int = 80, completion: int = 20):
    message = SimpleNamespace(content=content, tool_calls=list(tool_calls or []))
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class DeterministicAgentClient:
    """Script the provider boundary while leaving the production loop intact."""

    provider_name = "deterministic_proxy"

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.calls: list[dict[str, Any]] = []
        self.current_turn: dict[str, Any] = {}
        self.seen_user_turns: list[str] = []
        self._tool_call_counter = 0

    def set_turn(self, turn: dict[str, Any]) -> None:
        self.current_turn = dict(turn)
        self.seen_user_turns.append(str(turn.get("content") or ""))

    def _scripted_tools(self) -> list[dict[str, Any]]:
        scripted = list(self.current_turn.get("tool_calls") or [])
        if scripted:
            return [
                {
                    "name": str(item.get("name") or ""),
                    "arguments": dict(item.get("arguments") or {}),
                }
                for item in scripted
                if str(item.get("name") or "")
            ]
        tool_name = str(self.current_turn.get("tool_name") or "")
        if not tool_name:
            return []
        return [
            {
                "name": tool_name,
                "arguments": dict(self.current_turn.get("tool_arguments") or {}),
            }
        ]

    @staticmethod
    def _message_payload(message: Any) -> dict[str, Any]:
        if isinstance(message, dict):
            return {
                "role": message.get("role", ""),
                "content": message.get("content", ""),
                "tool_call_id": message.get("tool_call_id", ""),
            }
        return {"role": getattr(message, "role", ""), "content": getattr(message, "content", "")}

    def create(self, **kwargs):
        messages = [self._message_payload(item) for item in kwargs.get("messages") or []]
        self.calls.append(
            {
                "is_planner": "tools" not in kwargs,
                "messages": messages,
                "tool_names": [
                    str((tool.get("function") or {}).get("name") or "")
                    for tool in (kwargs.get("tools") or [])
                ],
            }
        )
        if "tools" not in kwargs:
            return self._planner_response()

        last_user_index = max(
            (index for index, item in enumerate(messages) if item.get("role") == "user"),
            default=-1,
        )
        scripted_tools = self._scripted_tools()
        completed_tool_calls = sum(
            item.get("role") == "tool" for item in messages[last_user_index + 1 :]
        )
        if completed_tool_calls < len(scripted_tools):
            pending_calls: list[_ToolCall] = []
            for scripted in scripted_tools[completed_tool_calls:]:
                self._tool_call_counter += 1
                pending_calls.append(
                    _ToolCall(
                        f"e2e-call-{self._tool_call_counter}",
                        scripted["name"],
                        scripted["arguments"],
                    )
                )
            return _response(
                tool_calls=pending_calls
            )

        tool_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "tool"]
        facts = "；".join(
            str(item.get("content") or "") for item in messages if item.get("role") == "user"
        )
        observation = tool_messages[-1] if tool_messages else "未调用工具"
        return _response(content=f"已按要求完成。上下文：{facts}。工具依据：{observation}")

    def _planner_response(self):
        scripted_tools = self._scripted_tools()
        tool_names = [item["name"] for item in scripted_tools]
        read_only_tools = {
            "check_cst_status",
            "list_project_materials",
            "open_results",
            "list_results",
            "read_result",
            "get_s_parameter",
            "recall_tool_result",
        }
        step_kind = "analyze" if set(tool_names).issubset(read_only_tools) else "tool"
        allowed_tools = tool_names or ["check_cst_status"]
        plan = {
            "intent_kind": "chat_task",
            "user_goal": str(self.current_turn.get("content") or "完成当前任务"),
            "constraints": list(self.current_turn.get("planner_constraints") or []),
            "steps": [
                {
                    "step_id": "act",
                    "kind": step_kind,
                    "title": "执行必要检查",
                    "expected_output": "获得可验证结果",
                    "allowed_tools": allowed_tools,
                },
                {
                    "step_id": "respond",
                    "kind": "respond",
                    "title": "回答用户",
                    "expected_output": "基于工具结果回答",
                    "allowed_tools": [],
                },
            ],
            "stop_conditions": ["task_complete"],
        }
        return _response(content=json.dumps(plan, ensure_ascii=False), prompt=60, completion=40)


class RecordingClient:
    """Record requests made to a real OpenAI-compatible client."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        messages = [
            DeterministicAgentClient._message_payload(item)
            for item in (kwargs.get("messages") or [])
        ]
        system_text = "\n".join(
            str(item.get("content") or "")
            for item in messages
            if item.get("role") == "system"
        )
        if "tools" in kwargs:
            call_kind = "executor"
        elif "CST 仿真 Agent 的 Planner" in system_text:
            call_kind = "planner"
        else:
            call_kind = "auxiliary"
        record = {
            "call_kind": call_kind,
            "is_planner": call_kind == "planner",
            "messages": messages,
            "tool_names": [
                str((tool.get("function") or {}).get("name") or "")
                for tool in (kwargs.get("tools") or [])
            ],
            "response_content": "",
            "response_tool_names": [],
            "usage": {},
            "error": "",
        }
        self.calls.append(record)
        try:
            response = self.delegate.chat.completions.create(**kwargs)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        message = response.choices[0].message
        record["response_content"] = str(getattr(message, "content", "") or "")[:4000]
        record["response_tool_names"] = [
            str(getattr(getattr(item, "function", None), "name", "") or "")
            for item in (getattr(message, "tool_calls", None) or [])
        ]
        usage = getattr(response, "usage", None)
        record["usage"] = {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
        return response
