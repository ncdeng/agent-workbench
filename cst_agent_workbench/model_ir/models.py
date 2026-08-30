"""Evidence-grounded intermediate representation for antenna modeling tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import math
from typing import Any, Mapping


SCHEMA_VERSION = "antenna-model-ir-v1"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _tuple_of_text(values: Any) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in (values or ()) if str(value).strip())


@dataclass(frozen=True)
class EvidenceRef:
    """A source anchor supporting one Model-IR claim.

    ``locator`` is deliberately source-neutral: examples include ``Table I``,
    ``Figure 2(b)``, ``section 3.1`` or ``user message 4``.
    """

    source_id: str
    locator: str
    quote: str = ""
    page: int | None = None
    method: str = "text"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(self, "locator", _required_text(self.locator, "locator"))
        if self.page is not None and int(self.page) < 1:
            raise ValueError("page must be >= 1")
        object.__setattr__(self, "page", None if self.page is None else int(self.page))
        method = str(self.method or "text").strip().lower()
        if method not in {"text", "table", "figure", "formula", "user", "derived"}:
            raise ValueError(f"unsupported evidence method: {method}")
        object.__setattr__(self, "method", method)
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        return cls(**dict(data))


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    expression: str
    unit: str = "mm"
    role: str = "geometry"
    provenance: str = "stated"
    dependencies: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "parameter name"))
        object.__setattr__(self, "expression", _required_text(self.expression, "parameter expression"))
        object.__setattr__(self, "unit", str(self.unit or "").strip())
        provenance = str(self.provenance or "").strip().lower()
        if provenance not in {"stated", "derived", "assumed", "user_provided"}:
            raise ValueError(f"unsupported parameter provenance: {provenance}")
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "dependencies", _tuple_of_text(self.dependencies))
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterSpec":
        return cls(**dict(data))


@dataclass(frozen=True)
class MaterialSpec:
    material_id: str
    name: str
    properties: dict[str, str] = field(default_factory=dict)
    provenance: str = "stated"
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "material_id", _required_text(self.material_id, "material_id"))
        object.__setattr__(self, "name", _required_text(self.name, "material name"))
        object.__setattr__(self, "properties", {str(k): str(v) for k, v in self.properties.items()})
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MaterialSpec":
        return cls(**dict(data))


@dataclass(frozen=True)
class GeometryOperation:
    """One ordered, compiler-facing geometry operation.

    ``kind`` is validated against compiler capabilities later. Keeping arguments
    as a mapping lets the IR grow without coupling it to CST VBA syntax.
    """

    operation_id: str
    kind: str
    arguments: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _required_text(self.operation_id, "operation_id"))
        object.__setattr__(self, "kind", _required_text(self.kind, "geometry kind").lower())
        object.__setattr__(self, "arguments", dict(self.arguments or {}))
        object.__setattr__(self, "depends_on", _tuple_of_text(self.depends_on))
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GeometryOperation":
        return cls(**dict(data))


@dataclass(frozen=True)
class SimulationObject:
    object_id: str
    kind: str
    settings: dict[str, Any]
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_id", _required_text(self.object_id, "object_id"))
        object.__setattr__(self, "kind", _required_text(self.kind, "simulation object kind").lower())
        object.__setattr__(self, "settings", dict(self.settings or {}))
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SimulationObject":
        return cls(**dict(data))


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    metric: str
    operator: str
    target: float
    unit: str
    frequency_ghz: float | None = None
    tolerance: float | None = None
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", _required_text(self.criterion_id, "criterion_id"))
        object.__setattr__(self, "metric", _required_text(self.metric, "metric"))
        if self.operator not in {"<", "<=", "==", ">=", ">", "within"}:
            raise ValueError(f"unsupported acceptance operator: {self.operator}")
        target = float(self.target)
        if not math.isfinite(target):
            raise ValueError("acceptance target must be finite")
        object.__setattr__(self, "target", target)
        if self.frequency_ghz is not None:
            frequency = float(self.frequency_ghz)
            if not math.isfinite(frequency):
                raise ValueError("frequency_ghz must be finite")
            object.__setattr__(self, "frequency_ghz", frequency)
        if self.tolerance is not None:
            tolerance = float(self.tolerance)
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("tolerance must be finite and >= 0")
            object.__setattr__(self, "tolerance", tolerance)
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AcceptanceCriterion":
        return cls(**dict(data))


@dataclass(frozen=True)
class Assumption:
    assumption_id: str
    statement: str
    impact: str
    target_paths: tuple[str, ...]
    status: str = "proposed"
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "assumption_id", _required_text(self.assumption_id, "assumption_id"))
        object.__setattr__(self, "statement", _required_text(self.statement, "assumption statement"))
        object.__setattr__(self, "impact", _required_text(self.impact, "assumption impact"))
        object.__setattr__(self, "target_paths", _tuple_of_text(self.target_paths))
        status = str(self.status or "").strip().lower()
        if status not in {"proposed", "accepted", "rejected"}:
            raise ValueError(f"unsupported assumption status: {status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Assumption":
        return cls(**dict(data))


@dataclass(frozen=True)
class Clarification:
    clarification_id: str
    question: str
    target_paths: tuple[str, ...]
    blocking: bool = True
    status: str = "open"
    answer: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "clarification_id", _required_text(self.clarification_id, "clarification_id"))
        object.__setattr__(self, "question", _required_text(self.question, "clarification question"))
        object.__setattr__(self, "target_paths", _tuple_of_text(self.target_paths))
        status = str(self.status or "").strip().lower()
        if status not in {"open", "resolved", "dismissed"}:
            raise ValueError(f"unsupported clarification status: {status}")
        if status == "resolved" and not str(self.answer or "").strip():
            raise ValueError("resolved clarification requires an answer")
        object.__setattr__(self, "status", status)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Clarification":
        return cls(**dict(data))


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    target_path: str
    description: str
    evidence: tuple[EvidenceRef, ...]
    status: str = "open"
    resolution: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "conflict_id", _required_text(self.conflict_id, "conflict_id"))
        object.__setattr__(self, "target_path", _required_text(self.target_path, "conflict target_path"))
        object.__setattr__(self, "description", _required_text(self.description, "conflict description"))
        object.__setattr__(
            self,
            "evidence",
            tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in self.evidence),
        )
        status = str(self.status or "").strip().lower()
        if status not in {"open", "resolved"}:
            raise ValueError(f"unsupported conflict status: {status}")
        if status == "resolved" and not str(self.resolution or "").strip():
            raise ValueError("resolved conflict requires a resolution")
        object.__setattr__(self, "status", status)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Conflict":
        return cls(**dict(data))


@dataclass(frozen=True)
class AntennaModelIR:
    ir_id: str
    title: str
    task_mode: str
    source_ids: tuple[str, ...]
    parameters: tuple[ParameterSpec, ...] = ()
    materials: tuple[MaterialSpec, ...] = ()
    geometry: tuple[GeometryOperation, ...] = ()
    ports: tuple[SimulationObject, ...] = ()
    boundaries: tuple[SimulationObject, ...] = ()
    monitors: tuple[SimulationObject, ...] = ()
    solver: SimulationObject | None = None
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    clarifications: tuple[Clarification, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    status: str = "draft"
    revision: int = 1
    confirmed_by: str = ""
    confirmed_at: str = ""
    created_at: str = field(default_factory=_now_utc)
    updated_at: str = field(default_factory=_now_utc)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "ir_id", _required_text(self.ir_id, "ir_id"))
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        mode = str(self.task_mode or "").strip().lower()
        if mode not in {"paper_reproduction", "requirement_synthesis", "existing_project_revision"}:
            raise ValueError(f"unsupported task_mode: {mode}")
        object.__setattr__(self, "task_mode", mode)
        object.__setattr__(self, "source_ids", _tuple_of_text(self.source_ids))
        status = str(self.status or "").strip().lower()
        if status not in {"draft", "review_required", "confirmed", "executing", "completed", "failed"}:
            raise ValueError(f"unsupported Model-IR status: {status}")
        object.__setattr__(self, "status", status)
        if int(self.revision) < 1:
            raise ValueError("revision must be >= 1")
        object.__setattr__(self, "revision", int(self.revision))
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")

        collection_types = {
            "parameters": ParameterSpec,
            "materials": MaterialSpec,
            "geometry": GeometryOperation,
            "ports": SimulationObject,
            "boundaries": SimulationObject,
            "monitors": SimulationObject,
            "acceptance_criteria": AcceptanceCriterion,
            "assumptions": Assumption,
            "clarifications": Clarification,
            "conflicts": Conflict,
        }
        for attr, item_type in collection_types.items():
            values = getattr(self, attr)
            object.__setattr__(
                self,
                attr,
                tuple(value if isinstance(value, item_type) else item_type.from_dict(value) for value in values),
            )
        if self.solver is not None and not isinstance(self.solver, SimulationObject):
            object.__setattr__(self, "solver", SimulationObject.from_dict(self.solver))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AntennaModelIR":
        return cls(**dict(data))

    def with_revision(self, **changes: Any) -> "AntennaModelIR":
        """Return a changed draft while invalidating a previous confirmation."""
        return replace(
            self,
            **changes,
            status="draft",
            revision=self.revision + 1,
            confirmed_by="",
            confirmed_at="",
            updated_at=_now_utc(),
        )

    def mark_confirmed(self, confirmed_by: str) -> "AntennaModelIR":
        return replace(
            self,
            status="confirmed",
            confirmed_by=_required_text(confirmed_by, "confirmed_by"),
            confirmed_at=_now_utc(),
            updated_at=_now_utc(),
        )
