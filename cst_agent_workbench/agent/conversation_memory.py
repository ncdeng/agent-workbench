"""Production rules for user goals, constraints and pending questions."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


_CLAUSE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_EXPLICIT_CONSTRAINT = re.compile(
    r"(?:必须|务必|不要|不得|禁止|不能|只允许|仅允许|只能|保持|不可以|"
    r"must\b|must not\b|do not\b|don't\b|never\b|only\b|keep\b)",
    re.IGNORECASE,
)
_CLEAR_ALL = re.compile(
    r"(?:取消|清除|撤销|忘掉|忽略|不再遵守).{0,8}(?:全部|所有)?(?:约束|限制)|"
    r"(?:clear|remove|forget|ignore)\s+(?:all\s+)?constraints",
    re.IGNORECASE,
)
_REMOVE_ONE = re.compile(
    r"(?:取消|解除|撤销|移除|不再遵守)\s*(.+?)(?:这个|这条)?(?:约束|限制)$|"
    r"(?:remove|drop|forget)\s+(?:the\s+)?(.+?)\s+constraint$",
    re.IGNORECASE,
)
_GOAL_ACTION = re.compile(
    r"(?:帮我|请|创建|建立|运行|执行|读取|导出|优化|停止|暂停|继续|修改|修复|"
    r"分析|评测|测试|生成|删除|清理|实现|完成|目标(?:是|改为)|"
    r"create|build|run|execute|read|export|optimi[sz]e|stop|pause|continue|"
    r"change|modify|fix|analy[sz]e|evaluate|test|generate|implement)",
    re.IGNORECASE,
)
_PENDING_REQUEST = re.compile(
    r"(?:请提供|请上传|请确认|需要你(?:提供|确认|选择)|能否提供|告诉我|"
    r"please provide|please upload|please confirm|could you provide|which do you prefer)",
    re.IGNORECASE,
)


@dataclass
class ConstraintDelta:
    additions: list[str] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)
    clear_all: bool = False


def _normalized_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def constraint_id(text: str, project_scope: str) -> str:
    payload = f"{project_scope}\x1f{_normalized_text(text).lower()}"
    return "constraint:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def extract_constraint_delta(user_message: str) -> ConstraintDelta:
    text = _normalized_text(user_message, 2000)
    if not text:
        return ConstraintDelta()
    if _CLEAR_ALL.search(text):
        return ConstraintDelta(clear_all=True)

    additions: list[str] = []
    removals: list[str] = []
    for raw_clause in _CLAUSE_SPLIT.split(text):
        clause = _normalized_text(raw_clause)
        if not clause:
            continue
        remove_match = _REMOVE_ONE.search(clause)
        if remove_match:
            target = _normalized_text(remove_match.group(1) or remove_match.group(2))
            if target:
                removals.append(target)
            continue
        if _EXPLICIT_CONSTRAINT.search(clause):
            additions.append(clause)
    return ConstraintDelta(
        additions=list(dict.fromkeys(additions)),
        removals=list(dict.fromkeys(removals)),
    )


def should_replace_user_goal(user_message: str, existing_goal: str) -> bool:
    text = _normalized_text(user_message)
    if not text:
        return False
    if not _normalized_text(existing_goal):
        return True
    return bool(_GOAL_ACTION.search(text))


def apply_constraints(
    memory: Any,
    *,
    user_message: str,
    project_scope: str,
    planner_constraints: Iterable[str] = (),
) -> dict[str, Any]:
    conversation = memory.conversation
    records = [dict(item) for item in getattr(conversation, "constraint_records", []) or []]
    if not records:
        records = [
            {
                "entry_id": constraint_id(str(text), project_scope),
                "text": str(text),
                "project_scope": project_scope,
                "source": "legacy",
            }
            for text in getattr(conversation, "constraints", []) or []
        ]
    delta = extract_constraint_delta(user_message)
    removed: list[str] = []
    if delta.clear_all:
        removed = [str(item.get("text") or "") for item in records if item.get("project_scope") == project_scope]
        records = [item for item in records if item.get("project_scope") != project_scope]
    else:
        for target in delta.removals:
            kept = []
            target_lower = target.lower()
            for item in records:
                item_text = str(item.get("text") or "")
                if item.get("project_scope") == project_scope and (
                    target_lower in item_text.lower() or item_text.lower() in target_lower
                ):
                    removed.append(item_text)
                else:
                    kept.append(item)
            records = kept

    additions = list(delta.additions)
    # Deterministic extraction preserves the user's exact wording. Planner
    # constraints are a fallback only; otherwise a shortened paraphrase of the
    # same clause would be stored as a second, conflicting record.
    if not additions:
        additions.extend(
            _normalized_text(item)
            for item in planner_constraints
            if _normalized_text(item) and _EXPLICIT_CONSTRAINT.search(_normalized_text(item))
        )
    added: list[str] = []
    known_ids = {str(item.get("entry_id") or "") for item in records}
    for text in dict.fromkeys(additions):
        entry_id = constraint_id(text, project_scope)
        if entry_id in known_ids:
            continue
        records.append(
            {
                "entry_id": entry_id,
                "text": text,
                "project_scope": project_scope,
                "source": "planner" if text in planner_constraints else "user_explicit",
            }
        )
        known_ids.add(entry_id)
        added.append(text)

    records = records[-50:]
    conversation.constraint_records = records
    conversation.constraints = [
        str(item.get("text") or "")
        for item in records
        if item.get("project_scope") == project_scope and str(item.get("text") or "")
    ]
    return {"added": added, "removed": removed, "clear_all": delta.clear_all}


def extract_pending_questions(assistant_message: str) -> list[str]:
    pending: list[str] = []
    for clause in re.split(r"(?<=[。！？!?])", str(assistant_message or "")):
        text = _normalized_text(clause)
        if text and (text.endswith(("?", "？")) or _PENDING_REQUEST.search(text)) and _PENDING_REQUEST.search(text):
            pending.append(text)
    return list(dict.fromkeys(pending))[-5:]
