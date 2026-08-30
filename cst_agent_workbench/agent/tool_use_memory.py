"""Scoped procedural memory used to rerank safe production tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
import json
import logging
import threading
from pathlib import Path


_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)

logger = logging.getLogger(__name__)
_PERSIST_LOCK = threading.Lock()


def tokenize(text: str) -> tuple[str, ...]:
    raw = str(text or "").replace("_", " ").replace("-", " ").lower()
    return tuple(dict.fromkeys(match.group(0) for match in _TOKEN_RE.finditer(raw)))


@dataclass(frozen=True)
class ToolUseMemoryRecord:
    task_signature: str
    selected_tools: tuple[str, ...]
    success: bool
    failure_reason: str = ""
    corrective_hint: str = ""
    timestamp: str = ""
    confidence: float = 0.5
    metric_delta: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_signature = str(self.task_signature or "").strip()
        if not task_signature:
            raise ValueError("task_signature is required")
        object.__setattr__(self, "task_signature", task_signature)
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        object.__setattr__(self, "confidence", max(0.0, min(1.0, confidence)))
        object.__setattr__(self, "selected_tools", tuple(str(tool) for tool in self.selected_tools if str(tool)))

    @property
    def failure(self) -> bool:
        return not self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_signature": self.task_signature,
            "selected_tools": list(self.selected_tools),
            "success": bool(self.success),
            "failure": self.failure,
            "failure_reason": self.failure_reason,
            "corrective_hint": self.corrective_hint,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "metric_delta": self.metric_delta,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolUseMemoryRecord":
        success = bool(data["success"]) if "success" in data else not bool(data.get("failure", False))
        return cls(
            task_signature=str(data.get("task_signature", "")),
            selected_tools=tuple(str(tool) for tool in data.get("selected_tools", []) if str(tool)),
            success=success,
            failure_reason=str(data.get("failure_reason", "") or ""),
            corrective_hint=str(data.get("corrective_hint", "") or ""),
            timestamp=str(data.get("timestamp", "") or ""),
            confidence=float(data.get("confidence", 0.5) or 0.5),
            metric_delta=data.get("metric_delta"),
            metadata=dict(data.get("metadata") or {}),
        )

    @property
    def searchable_text(self) -> str:
        return " ".join(
            item
            for item in [
                self.task_signature,
                " ".join(self.selected_tools),
                self.failure_reason,
                self.corrective_hint,
                " ".join(str(value) for value in self.metadata.values()),
            ]
            if item
        )


def should_write_tool_memory(
    *,
    task_signature: str = "",
    selected_tools: Iterable[str] = (),
    success: bool = True,
    failure_reason: str = "",
    corrective_hint: str = "",
    metric_delta: float | None = None,
    improved: bool = False,
    improvement_delta: float | None = None,
    is_chat_summary: bool = False,
) -> bool:
    """Gate procedural memory writes.

    Ordinary conversation summaries are intentionally excluded. A record is
    worth keeping only when it captures a failure, a concrete corrective hint,
    or measurable improvement from a tool sequence.
    """
    if is_chat_summary:
        return False
    if not str(task_signature or "").strip():
        return False
    if not tuple(str(tool) for tool in selected_tools if str(tool)):
        return False
    if failure_reason.strip():
        return True
    if corrective_hint.strip():
        return True
    if improved:
        return True
    delta = improvement_delta if improvement_delta is not None else metric_delta
    if delta is not None and delta > 0:
        return True
    return not success


class ToolUseMemoryStore:
    def __init__(self, records: Iterable[ToolUseMemoryRecord] | None = None, *, limit: int = 100):
        self.limit = max(1, int(limit))
        self.records: list[ToolUseMemoryRecord] = list(records or [])[-self.limit :]

    def add(
        self,
        record: ToolUseMemoryRecord,
        *,
        apply_policy: bool = True,
        is_chat_summary: bool = False,
        improved: bool = False,
        improvement_delta: float | None = None,
    ) -> bool:
        if apply_policy and not should_write_tool_memory(
            task_signature=record.task_signature,
            selected_tools=record.selected_tools,
            success=record.success,
            failure_reason=record.failure_reason,
            corrective_hint=record.corrective_hint,
            metric_delta=record.metric_delta,
            improved=improved,
            improvement_delta=improvement_delta,
            is_chat_summary=is_chat_summary,
        ):
            return False
        self.records.append(record)
        self.records = self.records[-self.limit :]
        return True

    def recall(
        self,
        query: str = "",
        *,
        task_signature: str = "",
        tools: Iterable[str] | None = None,
        text: str = "",
        k: int = 3,
        tool_names: Iterable[str] | None = None,
        project_scope: str = "",
        design_signature: str = "",
    ) -> list[ToolUseMemoryRecord]:
        query_tokens = tokenize(" ".join(item for item in [query, text] if item))
        requested_tools = {str(tool) for tool in [*(tool_names or []), *(tools or [])] if str(tool)}
        signature_query = str(task_signature or "").strip().lower()
        scored: list[tuple[float, ToolUseMemoryRecord]] = []
        for record in self.records:
            record_project = str(record.metadata.get("project_scope") or "")
            if project_scope and record_project != project_scope:
                continue
            record_design = str(record.metadata.get("design_signature") or "").strip().lower()
            current_design = str(design_signature or "").strip().lower()
            if record_design and record_design != current_design:
                continue
            if requested_tools and not requested_tools.intersection(record.selected_tools):
                continue
            record_tokens = tokenize(record.searchable_text)
            token_score = sum(1 for token in query_tokens if token in record_tokens)
            signature_score = 0.0
            if signature_query:
                record_signature = record.task_signature.lower()
                if signature_query == record_signature:
                    signature_score = 5.0
                elif signature_query in record_signature or record_signature in signature_query:
                    signature_score = 3.0
                else:
                    signature_score = sum(1 for token in tokenize(signature_query) if token in tokenize(record_signature))
            tool_score = len(requested_tools.intersection(record.selected_tools)) * 2 if requested_tools else 0
            failure_bonus = 0.5 if record.failure_reason else 0.0
            improvement_bonus = 0.5 if record.metric_delta is not None and record.metric_delta > 0 else 0.0
            score = signature_score + token_score + tool_score + failure_bonus + improvement_bonus
            if not (query_tokens or requested_tools or signature_query):
                score += record.confidence
            elif score > 0:
                score *= 0.5 + 0.5 * record.confidence
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].timestamp), reverse=True)
        return [record for _, record in scored[: max(0, k)]]

    def to_list(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.records]

    @classmethod
    def from_list(cls, data: Iterable[dict[str, Any]], *, limit: int = 100) -> "ToolUseMemoryStore":
        return cls((ToolUseMemoryRecord.from_dict(item) for item in data), limit=limit)


def save_tool_use_memory(store: ToolUseMemoryStore, path: str) -> bool:
    """Atomically persist the small procedural-memory store."""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with _PERSIST_LOCK:
            temporary.write_text(
                json.dumps(store.to_list(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(target)
        return True
    except Exception as exc:
        logger.warning("tool-use memory save failed: %s", exc)
        return False


def load_tool_use_memory(path: str, *, limit: int = 100) -> ToolUseMemoryStore:
    try:
        target = Path(path)
        if not target.exists():
            return ToolUseMemoryStore(limit=limit)
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("tool-use memory root must be a list")
        return ToolUseMemoryStore.from_list(data, limit=limit)
    except Exception as exc:
        logger.warning("tool-use memory load failed: %s", exc)
        return ToolUseMemoryStore(limit=limit)


def persist_session_tool_use_memory(session: Any) -> bool:
    metadata = getattr(session, "metadata", None)
    path = metadata.get("tool_use_memory_path", "") if isinstance(metadata, dict) else ""
    store = getattr(session, "tool_use_memory", None)
    if not path or not isinstance(store, ToolUseMemoryStore):
        return False
    return save_tool_use_memory(store, path)


def build_tool_use_memory_guidance(
    session: Any,
    query: str,
    *,
    allowed_tool_names: Iterable[str] = (),
    k: int = 3,
) -> tuple[str, list[ToolUseMemoryRecord]]:
    store = getattr(session, "tool_use_memory", None)
    if not isinstance(store, ToolUseMemoryStore) or not store.records:
        return "", []
    metadata = getattr(session, "metadata", {}) or {}
    project_scope = str((metadata.get("memory_scope") or {}).get("project_scope") or "")
    design_signature = str(metadata.get("current_design_signature") or "")
    records = store.recall(
        query=query,
        tools=allowed_tool_names,
        project_scope=project_scope,
        design_signature=design_signature,
        k=k,
    )
    if not records:
        return "", []
    lines = ["[工具使用经验；仅用于安全白名单内的排序与参数提示]"]
    for record in records:
        tools_text = ", ".join(record.selected_tools)
        if record.success:
            lines.append(f"- successful tools={tools_text}; hint={record.corrective_hint or '-'}")
        else:
            lines.append(
                f"- failed tools={tools_text}; reason={record.failure_reason or '-'}; "
                f"correction={record.corrective_hint or '-'}"
            )
    return "\n".join(lines), records


def rerank_safe_tools(
    tools: list[Any],
    records: Iterable[ToolUseMemoryRecord],
) -> list[Any]:
    """Reorder an already-safe tool list; never add or remove capabilities."""
    score: dict[str, float] = {}
    for record in records:
        weight = max(0.0, min(1.0, record.confidence))
        delta = weight if record.success else -weight
        for tool_name in record.selected_tools:
            score[tool_name] = score.get(tool_name, 0.0) + delta

    def name(tool: Any) -> str:
        if isinstance(tool, dict):
            return str((tool.get("function") or {}).get("name", ""))
        return str(getattr(tool, "name", "") or "")

    indexed = list(enumerate(tools))
    indexed.sort(key=lambda pair: (-score.get(name(pair[1]), 0.0), pair[0]))
    return [tool for _, tool in indexed]
