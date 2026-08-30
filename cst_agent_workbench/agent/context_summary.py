from __future__ import annotations

from typing import Any, Dict, List


def _short(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    return text[:limit]


def build_context_summary(session: Any) -> str:
    lines: List[str] = ["[历史摘要]"]
    memory = getattr(session, "memory", None)
    if memory is not None and hasattr(memory, "conversation") and hasattr(memory, "workspace") and hasattr(memory, "decisions"):
        conversation = memory.conversation
        workspace = memory.workspace
        decisions = memory.decisions

        user_goal = _short(getattr(conversation, "user_goal", ""))
        constraints = list(getattr(conversation, "constraints", []) or [])
        recent_summary = _short(getattr(conversation, "recent_summary", ""), 300)
        if user_goal:
            lines.extend(["", "[用户目标]", user_goal])
        if constraints:
            lines.extend(["", "[关键约束]"])
            lines.extend(f"- {_short(item, 120)}" for item in constraints[-8:])
        if recent_summary:
            lines.extend(["", "[最近摘要]", recent_summary])

        project_path = _short(getattr(workspace, "project_path", ""), 260)
        parameter_summary = getattr(workspace, "parameter_summary", {}) or {}
        last_results_summary = getattr(workspace, "last_results_summary", {}) or {}
        workspace_lines = []
        if project_path:
            workspace_lines.append(f"project_path={project_path}")
        if parameter_summary:
            workspace_lines.append(f"parameters={_short(parameter_summary, 260)}")
        if last_results_summary:
            workspace_lines.append(f"last_results={_short(last_results_summary, 260)}")
        if workspace_lines:
            lines.extend(["", "[当前工程状态]"])
            lines.extend(f"- {item}" for item in workspace_lines)

        best_so_far = getattr(decisions, "best_so_far", {}) or {}
        recent_strategies = list(getattr(decisions, "recent_strategies", []) or [])
        failure_reasons = list(getattr(decisions, "failure_reasons", []) or [])
        if best_so_far or recent_strategies:
            lines.extend(["", "[已尝试策略]"])
            if best_so_far:
                lines.append(f"- best_so_far={_short(best_so_far, 220)}")
            for item in recent_strategies[-5:]:
                lines.append(f"- {_short(item, 180)}")
        if failure_reasons:
            lines.extend(["", "[失败经验]"])
            lines.extend(f"- {_short(item, 160)}" for item in failure_reasons[-5:])
    return "\n".join(lines)


def make_context_summary_message(session: Any) -> Dict[str, str]:
    return {"role": "system", "content": build_context_summary(session)}
