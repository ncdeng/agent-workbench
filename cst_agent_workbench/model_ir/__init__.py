"""Public Antenna Model-IR domain API."""

from .acceptance import (
    ACCEPTANCE_SCHEMA_VERSION,
    SUPPORTED_ACCEPTANCE_METRICS,
    AcceptanceContext,
    AcceptanceEvaluation,
    CriterionEvaluation,
    ModelIRResultEvidence,
    canonical_payload_sha256,
    evaluate_acceptance_criteria,
)

from .compiler import (
    MODEL_IR_CONFIGURATION_TOOLS,
    CompiledModelPlan,
    CompiledToolCall,
    ModelIRCompileError,
    compile_model_ir,
)
from .executor import (
    EXECUTION_SCHEMA_VERSION,
    ModelIRCallResult,
    ModelIRExecutionBusyError,
    ModelIRExecutionContractError,
    ModelIRExecutionReport,
    execute_compiled_model_plan,
    execute_model_ir,
)

from .models import (
    SCHEMA_VERSION,
    AcceptanceCriterion,
    AntennaModelIR,
    Assumption,
    Clarification,
    Conflict,
    EvidenceRef,
    GeometryOperation,
    MaterialSpec,
    ParameterSpec,
    SimulationObject,
)
from .repository import ModelIRRepository
from .validation import (
    SUPPORTED_GEOMETRY_KINDS,
    ValidationIssue,
    ValidationReport,
    assert_executable,
    confirm_model_ir,
    validate_model_ir,
)

__all__ = [
    "ACCEPTANCE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SUPPORTED_ACCEPTANCE_METRICS",
    "SUPPORTED_GEOMETRY_KINDS",
    "AcceptanceContext",
    "AcceptanceEvaluation",
    "AcceptanceCriterion",
    "AntennaModelIR",
    "Assumption",
    "Clarification",
    "CompiledModelPlan",
    "CompiledToolCall",
    "Conflict",
    "CriterionEvaluation",
    "EXECUTION_SCHEMA_VERSION",
    "EvidenceRef",
    "GeometryOperation",
    "MODEL_IR_CONFIGURATION_TOOLS",
    "MaterialSpec",
    "ModelIRRepository",
    "ModelIRCompileError",
    "ModelIRCallResult",
    "ModelIRExecutionBusyError",
    "ModelIRExecutionContractError",
    "ModelIRExecutionReport",
    "ModelIRResultEvidence",
    "ParameterSpec",
    "SimulationObject",
    "ValidationIssue",
    "ValidationReport",
    "assert_executable",
    "confirm_model_ir",
    "compile_model_ir",
    "canonical_payload_sha256",
    "execute_compiled_model_plan",
    "execute_model_ir",
    "evaluate_acceptance_criteria",
    "validate_model_ir",
]
