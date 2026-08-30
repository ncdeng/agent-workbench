from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List


RECENT_STRATEGIES_SESSION_CAP = 100
_MEMORY_EMBED_CACHE_LIMIT = 512
_MEMORY_EMBED_CACHE: Dict[tuple[str, str, str], Any] = {}
UNSCOPED_PROJECT = "project:unscoped"


def project_scope_from_path(project_path: str) -> str:
    """Return a stable, privacy-preserving scope id for one CST project."""
    normalized = str(project_path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return UNSCOPED_PROJECT
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"project:{digest}"


def _canonical_entry_id(
    entry_type: str,
    text: str,
    project_scope: str,
    design_signature: str = "",
) -> str:
    payload = "\x1f".join(
        (
            str(entry_type or ""),
            str(project_scope or UNSCOPED_PROJECT),
            str(design_signature or "").strip().lower(),
            " ".join(str(text or "").strip().lower().split()),
        )
    )
    return f"{entry_type}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _entry_scope_matches(
    item: Dict[str, Any],
    *,
    project_scope: str,
    design_signature: str,
) -> bool:
    item_project = str(item.get("project_scope") or UNSCOPED_PROJECT)
    if item_project != project_scope:
        return False
    item_design = str(item.get("design_signature") or "").strip().lower()
    current_design = str(design_signature or "").strip().lower()
    return not item_design or item_design == current_design


@dataclass
class MemoryEntry:
    id: str
    scope: str
    entry_type: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "entry_type": self.entry_type,
            "text": self.text,
            "metadata": dict(self.metadata),
        }


@dataclass
class ConversationMemory:
    user_goal: str = ""
    constraints: List[str] = field(default_factory=list)
    constraint_records: List[Dict[str, Any]] = field(default_factory=list)
    recent_summary: str = ""
    pending_questions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_goal": self.user_goal,
            "constraints": list(self.constraints),
            "constraint_records": [dict(item) for item in self.constraint_records],
            "recent_summary": self.recent_summary,
            "pending_questions": list(self.pending_questions),
        }


@dataclass
class WorkspaceMemory:
    project_path: str = ""
    model_summary: Dict[str, Any] = field(default_factory=dict)
    parameter_summary: Dict[str, Any] = field(default_factory=dict)
    last_results_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_path": self.project_path,
            "model_summary": dict(self.model_summary),
            "parameter_summary": dict(self.parameter_summary),
            "last_results_summary": dict(self.last_results_summary),
        }


@dataclass
class DecisionMemory:
    best_so_far: Dict[str, Any] = field(default_factory=dict)
    recent_strategies: List[Dict[str, Any]] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)
    failure_records: List[Dict[str, Any]] = field(default_factory=list)
    rollback_points: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_so_far": dict(self.best_so_far),
            "recent_strategies": [dict(item) for item in self.recent_strategies],
            "failure_reasons": list(self.failure_reasons),
            "failure_records": [dict(item) for item in self.failure_records],
            "rollback_points": [dict(item) for item in self.rollback_points],
        }


@dataclass
class StructuredMemory:
    conversation: ConversationMemory = field(default_factory=ConversationMemory)
    workspace: WorkspaceMemory = field(default_factory=WorkspaceMemory)
    decisions: DecisionMemory = field(default_factory=DecisionMemory)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation": self.conversation.to_dict(),
            "workspace": self.workspace.to_dict(),
            "decisions": self.decisions.to_dict(),
        }


def structured_memory_from_dict(data: Dict[str, Any]) -> "StructuredMemory":
    """从 to_dict() 输出反序列化为 StructuredMemory。"""
    conv = data.get("conversation") or {}
    ws = data.get("workspace") or {}
    dec = data.get("decisions") or {}
    return StructuredMemory(
        conversation=ConversationMemory(
            user_goal=conv.get("user_goal", ""),
            constraints=list(conv.get("constraints") or []),
            constraint_records=list(conv.get("constraint_records") or []),
            recent_summary=conv.get("recent_summary", ""),
            pending_questions=list(conv.get("pending_questions") or []),
        ),
        workspace=WorkspaceMemory(
            project_path=ws.get("project_path", ""),
            model_summary=dict(ws.get("model_summary") or {}),
            parameter_summary=dict(ws.get("parameter_summary") or {}),
            last_results_summary=dict(ws.get("last_results_summary") or {}),
        ),
        decisions=DecisionMemory(
            best_so_far=dict(dec.get("best_so_far") or {}),
            recent_strategies=list(dec.get("recent_strategies") or []),
            failure_reasons=list(dec.get("failure_reasons") or []),
            failure_records=list(dec.get("failure_records") or []),
            rollback_points=list(dec.get("rollback_points") or []),
        ),
    )


def _memory_confidence(item: Dict[str, Any], default: float = 1.0) -> float:
    try:
        return float(item.get("confidence", default) if item.get("confidence") is not None else default)
    except (TypeError, ValueError):
        return default


def _memory_confidence_floor(item: Dict[str, Any], min_confidence: float) -> float:
    """按实测校准档位返回写入/持久化门槛。

    反思校准把无改善经验压到 ≤0.5、回滚经验压到 ≤0.2；若仍按统一的
    min_confidence（默认 0.6）过滤，这两类经验在任何置信度下都过不了门槛，
    confidence_basis 的三档审计就成了死代码。打折只降低召回排序的信任，
    不应等于直接丢弃，因此对这两档使用更低的分档门槛。
    """
    basis = str(item.get("confidence_basis", "") or "")
    if basis == "no_improvement":
        return min(min_confidence, 0.3)
    if basis == "rolled_back":
        return min(min_confidence, 0.15)
    return min_confidence


def save_memory(memory: "StructuredMemory", path: str) -> bool:
    """将 StructuredMemory 序列化到 JSON 文件。返回是否成功。"""
    import json
    import logging
    from pathlib import Path
    _logger = logging.getLogger(__name__)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(memory.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _logger.debug("memory saved to %s", path)
        return True
    except Exception as exc:
        _logger.warning("memory save failed: %s", exc)
        return False


def prune_session_memory_files(directory: str, keep: int = 20) -> None:
    """按修改时间保留最近 keep 个 session 调试文件，防止 sessions/ 目录无限堆积。"""
    import logging
    import os
    _logger = logging.getLogger(__name__)
    try:
        if not os.path.isdir(directory):
            return
        entries = [
            os.path.join(directory, name)
            for name in os.listdir(directory)
            if name.endswith(".json")
        ]
        entries.sort(key=os.path.getmtime, reverse=True)
        for stale in entries[keep:]:
            os.remove(stale)
    except OSError as exc:
        _logger.debug("session memory prune skipped: %s", exc)


def load_memory(path: str) -> "StructuredMemory | None":
    """从 JSON 文件反序列化 StructuredMemory。文件不存在或解析失败返回 None。"""
    import json
    import logging
    from pathlib import Path
    _logger = logging.getLogger(__name__)
    try:
        p = Path(path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        mem = structured_memory_from_dict(data)
        _logger.debug("memory loaded from %s", path)
        return mem
    except Exception as exc:
        _logger.warning("memory load failed: %s", exc)
        return None


def extract_persistent_memory(
    memory: "StructuredMemory",
    *,
    min_confidence: float | None = None,
) -> "StructuredMemory":
    persistent = StructuredMemory()
    persistent.conversation.constraints = list(memory.conversation.constraints or [])
    persistent.conversation.constraint_records = [
        dict(item) for item in (memory.conversation.constraint_records or [])
    ][-50:]
    persistent.decisions.recent_strategies = [
        dict(item) for item in (memory.decisions.recent_strategies or [])
        if item.get("lesson") or item.get("failure_pattern") or item.get("effective_action")
        if min_confidence is None or _memory_confidence(item) >= _memory_confidence_floor(item, min_confidence)
    ][-20:]
    persistent.decisions.failure_reasons = list(memory.decisions.failure_reasons or [])[-20:]
    persistent.decisions.failure_records = [
        dict(item) for item in (memory.decisions.failure_records or [])
    ][-20:]
    return persistent


def merge_persistent_memory(target: "StructuredMemory", persistent: "StructuredMemory | None") -> None:
    if persistent is None:
        return

    incoming_constraint_records = list(persistent.conversation.constraint_records or [])
    if incoming_constraint_records:
        existing_constraint_ids = {
            str(item.get("entry_id") or "") for item in target.conversation.constraint_records
        }
        for record in incoming_constraint_records:
            candidate = dict(record)
            if candidate.get("entry_id") not in existing_constraint_ids:
                target.conversation.constraint_records.append(candidate)
                existing_constraint_ids.add(str(candidate.get("entry_id") or ""))
        current_scope = project_scope_from_path(target.workspace.project_path)
        target.conversation.constraints = [
            str(item.get("text") or "")
            for item in target.conversation.constraint_records
            if item.get("project_scope") == current_scope and str(item.get("text") or "")
        ]
    else:
        existing_constraints = set(target.conversation.constraints or [])
        for constraint in persistent.conversation.constraints or []:
            if constraint not in existing_constraints:
                target.conversation.constraints.append(constraint)
                existing_constraints.add(constraint)

    incoming_failure_records = list(persistent.decisions.failure_records or [])
    if not incoming_failure_records:
        incoming_failure_records = [
            {
                "reason": str(reason),
                "project_scope": UNSCOPED_PROJECT,
                "design_signature": "",
                "entry_id": _canonical_entry_id(
                    "failure", str(reason), UNSCOPED_PROJECT
                ),
            }
            for reason in persistent.decisions.failure_reasons or []
        ]
    existing_failure_ids = {
        str(item.get("entry_id") or "") for item in target.decisions.failure_records
    }
    for record in incoming_failure_records:
        candidate = dict(record)
        reason = str(candidate.get("reason") or "").strip()
        if not reason:
            continue
        candidate.setdefault("project_scope", UNSCOPED_PROJECT)
        candidate.setdefault("design_signature", "")
        candidate.setdefault(
            "entry_id",
            _canonical_entry_id(
                "failure",
                reason,
                candidate["project_scope"],
                candidate["design_signature"],
            ),
        )
        if candidate["entry_id"] not in existing_failure_ids:
            target.decisions.failure_records.append(candidate)
            existing_failure_ids.add(candidate["entry_id"])
    target.decisions.failure_records = target.decisions.failure_records[-20:]
    target.decisions.failure_reasons = [
        str(item.get("reason") or "") for item in target.decisions.failure_records
        if str(item.get("reason") or "")
    ]

    existing_strategy_keys = {str(sorted(item.items())) for item in target.decisions.recent_strategies or []}
    for strategy in persistent.decisions.recent_strategies or []:
        key = str(sorted(dict(strategy).items()))
        if key not in existing_strategy_keys:
            target.decisions.recent_strategies.append(dict(strategy))
            existing_strategy_keys.add(key)
    target.decisions.recent_strategies = target.decisions.recent_strategies[-20:]


def save_persistent_memory(
    memory: "StructuredMemory",
    path: str,
    *,
    min_confidence: float | None = None,
) -> bool:
    return save_memory(extract_persistent_memory(memory, min_confidence=min_confidence), path)


def _char_bigrams(value: str) -> set[str]:
    compact = "".join(str(value or "").lower().replace("/", " ").replace("\\", " ").split())
    if not compact:
        return set()
    if len(compact) == 1:
        return {compact}
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def _score_memory_entry(query: str, text: str) -> int:
    normalized_query = str(query or "").lower().replace("/", " ").replace("\\", " ")
    query_tokens = {token for token in normalized_query.split() if token}
    text_lower = str(text or "").lower()
    token_hits = sum(1 for token in query_tokens if token in text_lower)
    bigram_hits = len(_char_bigrams(normalized_query) & _char_bigrams(text_lower))
    return token_hits + bigram_hits


def _memory_embedding_identity(embedding_model: str) -> tuple[str, str]:
    try:
        from cst_agent_workbench import config

        provider = str(getattr(config, "EMBEDDING_PROVIDER", "") or "")
        if provider == "local":
            configured_model = str(getattr(config, "EMBEDDING_LOCAL_MODEL", "") or "")
        else:
            configured_model = str(getattr(config, "EMBEDDING_MODEL", "") or "")
        effective_model = configured_model or str(embedding_model or "")
        return provider, effective_model
    except Exception:
        return "", str(embedding_model or "")


def _memory_embedding_key(text: str, embedding_model: str) -> tuple[str, str, str]:
    provider, effective_model = _memory_embedding_identity(embedding_model)
    digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    return provider, effective_model, digest


def _trim_memory_embedding_cache() -> None:
    while len(_MEMORY_EMBED_CACHE) > _MEMORY_EMBED_CACHE_LIMIT:
        _MEMORY_EMBED_CACHE.pop(next(iter(_MEMORY_EMBED_CACHE)))


def _with_recall_metadata(entry: MemoryEntry, *, score: float, reason: str) -> MemoryEntry:
    metadata = dict(entry.metadata)
    metadata["recall_score"] = float(score)
    metadata["recall_reason"] = reason
    return replace(entry, metadata=metadata)


def _semantic_rank(query: str, entries: List[MemoryEntry], client, embedding_model: str) -> List[tuple[MemoryEntry, float]] | None:
    """对 entries 按 cosine 相似度排序。失败/降级时返回 None，让上游回退到 token-overlap。

    Memory 与 RAG 共用同一套 embed_texts 入口（local sentence-transformers / OpenAI API）。
    """
    if not entries or not query:
        return None
    try:
        import numpy as _np
        from cst_agent_workbench.rag.knowledge_base import embed_texts
        texts = [e.text for e in entries]
        keys = [_memory_embedding_key(text, embedding_model) for text in texts]
        missing_keys = []
        missing_texts = []
        for key, text in zip(keys, texts):
            if key not in _MEMORY_EMBED_CACHE:
                missing_keys.append(key)
                missing_texts.append(text)
        if missing_texts:
            missing_vecs = embed_texts(missing_texts, client, embedding_model)
            for key, vec in zip(missing_keys, missing_vecs):
                _MEMORY_EMBED_CACHE[key] = _np.asarray(vec, dtype=_np.float32)
            _trim_memory_embedding_cache()
        entry_vecs = _np.vstack([_MEMORY_EMBED_CACHE[key] for key in keys])
        q_vec = _np.asarray(embed_texts([query], client, embedding_model)[0], dtype=_np.float32)
        scores = entry_vecs @ q_vec  # cosine, vectors are L2-normalized
        order = _np.argsort(scores)[::-1]
        return [(entries[i], float(scores[i])) for i in order]
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).debug("semantic recall failed, falling back to token overlap: %s", exc)
        return None


def _stable_entry_id(prefix: str, payload: Any, index: int) -> str:
    """按内容生成稳定 id，回退到位置 id。

    此前 id 是 `lesson:{index}` —— 枚举下标。但底层列表会滚动淘汰
    （recent_strategies[-100:] / failure_reasons[-20:] / persist 时再 [-20:]），
    所以同一个 "lesson:3" 在两次 trace 里可能指向完全不同的内容，
    "哪条经验导致了这次调参" 的审计链无法复现。改成按文本内容哈希。
    """
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("lesson") or payload.get("failure_pattern") or payload.get("strategy") or "")
    if not text:
        text = str(payload or "")
    text = text.strip()
    if not text:
        return f"{prefix}:{index}"
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"


def recall_memory(
    memory: "StructuredMemory",
    query: str = "",
    scopes: List[str] | None = None,
    k: int = 3,
    entry_types: List[str] | None = None,
    client: Any = None,
    embedding_model: str = "text-embedding-3-small",
    min_score: float | None = None,
    min_overlap: int | None = None,
    with_scores: bool = False,
    project_scope: str = "",
    design_signature: str = "",
) -> List[MemoryEntry]:
    """Recall structured-memory entries (constraints/lessons/failures).

    client: 传入 chat/embedding 客户端时启用语义召回（embedding cosine）；
            为 None 时退化到关键词 token-overlap（向后兼容旧调用）。
    embedding_model: API embedding 模型名；local provider 模式下被忽略。
    """
    allowed_scopes = set(scopes or ["session", "project"])
    allowed_types = set(entry_types or ["lesson", "failure", "constraint"])
    current_project_scope = project_scope or project_scope_from_path(memory.workspace.project_path)
    entries: List[MemoryEntry] = []

    if "constraint" in allowed_types and ("project" in allowed_scopes or "session" in allowed_scopes):
        for index, constraint in enumerate(memory.conversation.constraints or []):
            entries.append(
                MemoryEntry(_stable_entry_id("constraint", constraint, index), "project", "constraint", str(constraint))
            )

    if "lesson" in allowed_types and ("project" in allowed_scopes or "session" in allowed_scopes):
        for index, strategy in enumerate(memory.decisions.recent_strategies or []):
            candidate = dict(strategy)
            candidate.setdefault("project_scope", UNSCOPED_PROJECT)
            candidate.setdefault("design_signature", "")
            if not _entry_scope_matches(
                candidate,
                project_scope=current_project_scope,
                design_signature=design_signature,
            ):
                continue
            text_parts = [strategy.get(key, "") for key in ("lesson", "failure_pattern", "effective_action", "avoid_next", "reuse_condition")]
            text = "; ".join(str(item) for item in text_parts if item)
            if text:
                entries.append(
                    MemoryEntry(_stable_entry_id("lesson", strategy, index), "project", "lesson", text, dict(strategy))
                )

    if "failure" in allowed_types and ("project" in allowed_scopes or "session" in allowed_scopes):
        failure_records = list(memory.decisions.failure_records or [])
        if not failure_records:
            failure_records = [
                {
                    "reason": str(reason),
                    "project_scope": UNSCOPED_PROJECT,
                    "design_signature": "",
                }
                for reason in memory.decisions.failure_reasons or []
            ]
        for index, record in enumerate(failure_records):
            candidate = dict(record)
            candidate.setdefault("project_scope", UNSCOPED_PROJECT)
            candidate.setdefault("design_signature", "")
            if not _entry_scope_matches(
                candidate,
                project_scope=current_project_scope,
                design_signature=design_signature,
            ):
                continue
            reason = str(candidate.get("reason") or "")
            entries.append(
                MemoryEntry(
                    str(candidate.get("entry_id") or _stable_entry_id("failure", reason, index)),
                    "project",
                    "failure",
                    reason,
                    candidate,
                )
            )

    if not query:
        recent = [_with_recall_metadata(entry, score=0.0, reason="recent") for entry in entries[-k:]]
        if with_scores:
            return [(entry, entry.metadata.get("recall_score", 0.0)) for entry in recent]  # type: ignore[return-value]
        return recent

    # 语义召回：客户端可用时优先，失败静默降级到 token-overlap
    if client is not None:
        semantic = _semantic_rank(query, entries, client, embedding_model)
        if semantic is not None:
            if min_score is not None:
                semantic = [(entry, score) for entry, score in semantic if score >= min_score]
            recalled = [
                _with_recall_metadata(entry, score=score, reason="semantic")
                for entry, score in semantic[:k]
            ]
            if with_scores:
                return [(entry, entry.metadata.get("recall_score", 0.0)) for entry in recalled]  # type: ignore[return-value]
            return recalled

    scored = [(_score_memory_entry(query, entry.text), entry) for entry in entries]
    scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
    threshold = min_overlap
    if threshold is None and min_score is not None:
        threshold = 1
    if threshold is not None:
        scored = [item for item in scored if item[0] >= threshold]
    positive = [item for item in scored if item[0] > 0]
    selected = positive[:k] if positive else ([] if threshold is not None else scored[:k])
    recalled = [
        _with_recall_metadata(entry, score=float(score), reason="token_overlap")
        for score, entry in selected
    ]
    if with_scores:
        return [(entry, entry.metadata.get("recall_score", 0.0)) for entry in recalled]  # type: ignore[return-value]
    return recalled


class MemoryManager:
    @staticmethod
    def update_workspace(memory: StructuredMemory, *, project_path: str | None = None, model_summary: Dict[str, Any] | None = None, parameter_summary: Dict[str, Any] | None = None, last_results_summary: Dict[str, Any] | None = None) -> None:
        if project_path is not None:
            memory.workspace.project_path = project_path
        if model_summary is not None:
            memory.workspace.model_summary = dict(model_summary)
        if parameter_summary is not None:
            memory.workspace.parameter_summary = dict(parameter_summary)
        if last_results_summary is not None:
            memory.workspace.last_results_summary = dict(last_results_summary)

    @staticmethod
    def update_conversation(memory: StructuredMemory, *, user_goal: str | None = None, recent_summary: str | None = None, constraints: List[str] | None = None, pending_questions: List[str] | None = None) -> None:
        if user_goal is not None:
            memory.conversation.user_goal = user_goal
        if recent_summary is not None:
            memory.conversation.recent_summary = recent_summary
        if constraints is not None:
            memory.conversation.constraints = list(constraints)
        if pending_questions is not None:
            memory.conversation.pending_questions = list(pending_questions)

    @staticmethod
    def update_decisions(memory: StructuredMemory, *, best_so_far: Dict[str, Any] | None = None, strategy_entry: Dict[str, Any] | None = None, failure_reason: str | None = None, rollback_point: Dict[str, Any] | None = None, project_scope: str = "", design_signature: str = "") -> None:
        current_project_scope = project_scope or project_scope_from_path(memory.workspace.project_path)
        if best_so_far is not None:
            memory.decisions.best_so_far = dict(best_so_far)
        if strategy_entry is not None:
            candidate = dict(strategy_entry)
            candidate.setdefault("project_scope", current_project_scope)
            candidate.setdefault("design_signature", str(design_signature or "").strip().lower())
            lesson_text = "; ".join(
                str(candidate.get(key) or "")
                for key in ("lesson", "failure_pattern", "effective_action", "strategy", "proposal_reason")
                if str(candidate.get(key) or "").strip()
            )
            candidate.setdefault(
                "entry_id",
                _canonical_entry_id(
                    "lesson",
                    lesson_text,
                    candidate["project_scope"],
                    candidate["design_signature"],
                ),
            )
            memory.decisions.recent_strategies = [
                item for item in memory.decisions.recent_strategies
                if str(item.get("entry_id") or "") != candidate["entry_id"]
            ]
            memory.decisions.recent_strategies.append(candidate)
            memory.decisions.recent_strategies = memory.decisions.recent_strategies[-RECENT_STRATEGIES_SESSION_CAP:]
        if failure_reason:
            record = {
                "reason": str(failure_reason),
                "project_scope": current_project_scope,
                "design_signature": str(design_signature or "").strip().lower(),
            }
            record["entry_id"] = _canonical_entry_id(
                "failure",
                record["reason"],
                record["project_scope"],
                record["design_signature"],
            )
            memory.decisions.failure_records = [
                item for item in memory.decisions.failure_records
                if str(item.get("entry_id") or "") != record["entry_id"]
            ]
            memory.decisions.failure_records.append(record)
            memory.decisions.failure_records = memory.decisions.failure_records[-20:]
            memory.decisions.failure_reasons = [
                str(item.get("reason") or "") for item in memory.decisions.failure_records
            ]
            # P6: 扩大 cap 到 20（原 5 太激进，跑 6 轮早期失败经验就丢了）。
            # failure_reasons 是纯字符串列表没有 confidence，按 recency 保留（最新优先）。
            memory.decisions.failure_reasons = memory.decisions.failure_reasons[-20:]
        if rollback_point is not None:
            memory.decisions.rollback_points.append(dict(rollback_point))
            memory.decisions.rollback_points = memory.decisions.rollback_points[-5:]


def unified_recall(
    memory: "StructuredMemory",
    query: str,
    client: Any = None,
    embedding_model: str = "text-embedding-3-small",
    design_signature: str = "",
    k: int = 5,
    min_score: float | None = None,
    project_scope: str = "",
    include_legacy_dynamic: bool = False,
) -> List[MemoryEntry]:
    """Canonical scoped-memory retrieval entry point.

    Production lessons and failures have one source of truth: StructuredMemory.
    The old RAG dynamic JSON channel can be queried only through the explicit
    ``include_legacy_dynamic`` migration flag; new reflections are never
    double-written there.
    """
    scored_results: list[tuple[float, MemoryEntry]] = []

    # 1. StructuredMemory 检索（lesson + failure）
    try:
        effective_min = min_score
        if effective_min is None:
            effective_min = 0.0  # recall_memory 内部有自己的语义/token 分数，不强制门槛
        memory_pairs = recall_memory(
            memory,
            query=query,
            k=k,
            entry_types=["lesson", "failure"],
            client=client,
            embedding_model=embedding_model,
            min_score=effective_min,
            with_scores=True,
            project_scope=project_scope,
            design_signature=design_signature,
        )
        for entry, score in memory_pairs:  # type: ignore[misc]
            entry.metadata["source"] = "structured"
            scored_results.append((float(score), entry))
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning("structured memory recall failed: %s", exc)

    # 2. RAG dynamic entries 检索
    if include_legacy_dynamic:
        try:
            from cst_agent_workbench import config as _config
            from cst_agent_workbench.rag.knowledge_base import retrieve_antenna_rules as _retrieve

            rag_pairs = _retrieve(
                query,
                client,
                filter_type="history",
                top_k=k,
                min_score=min_score if min_score is not None else _config.MEMORY_RECALL_MIN_SCORE,
                design_signature=design_signature,
                with_scores=True,
            )
            for text, score in rag_pairs:
                raw_text = str(text)
            # filter_type="history" 已把文档库排除在外（knowledge_base._doc_store_applies），
            # 这里仍按前缀判定来源：真出现文档 chunk 时如实标 source="doc"，
            # 不再抹掉文档来源前缀，把资料伪装成 agent 自己学到的经验。
            # 兼容早期 `[PDF参考]` 标签，但新写出统一使用准确的 CST 官方文档标签。
                doc_prefix = next(
                    (prefix for prefix in ("[CST官方文档] ", "[PDF参考] ") if raw_text.startswith(prefix)),
                    "",
                )
                is_doc = bool(doc_prefix)
                clean_text = raw_text[len(doc_prefix):] if is_doc else raw_text
                _digest = hashlib.md5(clean_text.encode("utf-8")).hexdigest()[:8]
                entry = MemoryEntry(
                    id=f"{'doc' if is_doc else 'rag'}:{_digest}",
                    scope="project",
                    entry_type="doc" if is_doc else "history",
                    text=clean_text,
                    metadata={"source": "doc" if is_doc else "rag"},
                )
                scored_results.append((float(score or 0.0), entry))
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("legacy dynamic-memory recall failed: %s", exc)

    # 3. 去重前先按 score 排序：同一内容的多个副本保留分数最高的那条。
    #    （先去重再排序会让插入顺序决定幸存者，可能留下低分副本并挤掉高分内容。）
    scored_results.sort(key=lambda item: item[0], reverse=True)

    # 4. 去重（按文本前 100 字符的 bigram Jaccard 相似度 > 0.7 视为重复）
    deduped: list[tuple[float, MemoryEntry]] = []
    seen_prefixes: list[str] = []
    for score, entry in scored_results:
        entry_prefix = entry.text[:100].lower()
        entry_bigrams = _char_bigrams(entry_prefix)
        is_dup = False
        for seen in seen_prefixes:
            seen_bigrams = _char_bigrams(seen)
            union = entry_bigrams | seen_bigrams
            if union and len(entry_bigrams & seen_bigrams) / len(union) > 0.7:
                is_dup = True
                break
        if not is_dup:
            deduped.append((score, entry))
            seen_prefixes.append(entry_prefix)

    return [entry for _, entry in deduped[:k]]
