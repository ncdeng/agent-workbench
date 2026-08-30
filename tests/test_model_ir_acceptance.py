from __future__ import annotations

from dataclasses import replace
import math

import pytest

from cst_agent_workbench.model_ir import (
    AcceptanceContext,
    AcceptanceCriterion,
    ModelIRResultEvidence,
    canonical_payload_sha256,
    evaluate_acceptance_criteria,
)


def _criterion(**changes) -> AcceptanceCriterion:
    data = {
        "criterion_id": "s11-at-5p8",
        "metric": "s11_db",
        "operator": "<=",
        "target": -10,
        "unit": "dB",
        "frequency_ghz": 5.8,
    }
    data.update(changes)
    return AcceptanceCriterion(**data)


def _s11(curve_points=None, **changes):
    plot_data = curve_points if curve_points is not None else [
        {"freq": 5.0, "s_db": -3.0},
        {"freq": 5.8, "s_db": -12.0},
        {"freq": 6.5, "s_db": -4.0},
    ]
    data = {
        "success": True,
        "item": "1D Results\\S-Parameters\\S1,1",
        "result_kind": "s_parameter",
        "points": len(plot_data),
        "total_points": len(plot_data),
        "returned_points": len(plot_data),
        "downsampled": False,
        "plot_data": plot_data,
    }
    data.update(changes)
    return data


def _context(**changes) -> AcceptanceContext:
    data = {
        "execution_id": "exec-1",
        "ir_id": "public-slot-patch",
        "revision": 2,
        "solver_run_id": "solver-1",
        "project_path": r"D:\cst_agent_rag_data\case.cst",
        "project_sha256": "a" * 64,
        "design_point_sha256": "b" * 64,
        "solver_started_at": "2026-08-12T10:00:00+00:00",
    }
    data.update(changes)
    return AcceptanceContext(**data)


def _evidence(payload=None, **changes) -> ModelIRResultEvidence:
    result = _s11() if payload is None else payload
    data = {
        "payload": result,
        "execution_id": "exec-1",
        "ir_id": "public-slot-patch",
        "revision": 2,
        "solver_run_id": "solver-1",
        "project_path": r"D:\cst_agent_rag_data\case.cst",
        "project_sha256": "a" * 64,
        "design_point_sha256": "b" * 64,
        "solver_tool_call_id": "exec-1:solver",
        "result_tool_call_id": "exec-1:s11",
        "solver_completed_at": "2026-08-12T10:01:00+00:00",
        "result_captured_at": "2026-08-12T10:01:01+00:00",
        # 不能急切求值：NaN payload 会在这里就抛 ValueError，轮不到调用方的
        # override 生效（生产代码对 NaN 的拒绝路径反而没被测到）。
        "payload_sha256": None,
        "fresh_solver_boundary": True,
    }
    data.update(changes)
    if data["payload_sha256"] is None:
        try:
            data["payload_sha256"] = canonical_payload_sha256(result)
        except (TypeError, ValueError):
            data["payload_sha256"] = "0" * 64
    return ModelIRResultEvidence(**data)


def _evaluate(criteria=None, evidence=None, context=None):
    return evaluate_acceptance_criteria(
        [_criterion()] if criteria is None else criteria,
        [_evidence()] if evidence is None else evidence,
        context=_context() if context is None else context,
    )


def test_bound_typed_result_and_physical_pass_are_separate_successes():
    evaluation = _evaluate()

    assert evaluation.typed_result_success is True
    assert evaluation.criteria_evaluable is True
    assert evaluation.physical_target_success is True
    assert evaluation.criteria[0].actual == -12.0
    assert evaluation.criteria[0].result_ref == "exec-1:s11"


def test_valid_typed_result_can_physically_fail():
    payload = _s11(curve_points=[{"freq": 5.0, "s_db": -2}, {"freq": 5.8, "s_db": -7}])
    evaluation = _evaluate(evidence=[_evidence(payload)])

    assert evaluation.typed_result_success is True
    assert evaluation.criteria_evaluable is True
    assert evaluation.physical_target_success is False


def test_s11_interpolation_is_limited_to_complete_curve_range():
    payload = _s11(curve_points=[{"freq": 5.0, "s_db": -4}, {"freq": 6.0, "s_db": -12}])
    inside = _evaluate(criteria=[_criterion(frequency_ghz=5.5, target=-8)], evidence=[_evidence(payload)])
    outside = _evaluate(criteria=[_criterion(frequency_ghz=7.0)])

    assert inside.criteria[0].actual == -8.0
    assert inside.physical_target_success is True
    assert outside.typed_result_success is True
    assert outside.physical_target_success is None
    assert outside.criteria[0].reason_code == "frequency_outside_result_range"


@pytest.mark.parametrize(
    "operator,target,tolerance,expected",
    [
        ("<", -11.0, None, True),
        ("<=", -12.0, None, True),
        (">", -13.0, None, True),
        (">=", -12.0, None, True),
        ("within", -11.5, 0.5, True),
        ("within", -11.4, 0.5, False),
    ],
)
def test_supported_operator_boundaries(operator, target, tolerance, expected):
    result = _evaluate(criteria=[_criterion(operator=operator, target=target, tolerance=tolerance)])
    assert result.physical_target_success is expected


def test_evaluator_capability_does_not_invalidate_model_ir_schema():
    unsupported = _evaluate(criteria=[_criterion(metric="gain_db")])
    equality = _evaluate(criteria=[_criterion(operator="==", target=-12)])

    assert unsupported.typed_result_success is False
    assert unsupported.criteria[0].status == "unsupported_metric"
    assert unsupported.physical_target_success is None
    assert equality.criteria[0].reason_code == "unsupported_operator_contract"


@pytest.mark.parametrize(
    "payload,reason_fragment",
    [
        (_s11(downsampled=True, total_points=100, points=100), "downsampled"),
        (_s11(curve_points=[]), "non-empty"),
        (_s11(curve_points=[{"freq": 5.8, "s_db": math.nan}]), "hashed canonically"),
        (_s11(curve_points=[{"freq": 5.8, "s_db": -12}, {"freq": 5.8, "s_db": -11}]), "unique"),
        (_s11(item="1D Results\\S-Parameters\\S2,1"), "explicit S1,1"),
        (_s11(success=False), "success=true"),
    ],
)
def test_invalid_typed_result_never_grants_physical_success(payload, reason_fragment):
    evidence = _evidence(payload, payload_sha256=("0" * 64 if math.nan in _numeric_values(payload) else canonical_payload_sha256(payload)))
    evaluation = _evaluate(evidence=[evidence])

    assert evaluation.typed_result_success is False
    assert evaluation.criteria_evaluable is False
    assert evaluation.physical_target_success is None
    assert reason_fragment in evaluation.criteria[0].message


def _numeric_values(payload):
    return [value for point in payload.get("plot_data", []) for value in point.values() if isinstance(value, float)]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"execution_id": "old-exec"}, "execution_id"),
        ({"revision": 1}, "revision"),
        ({"solver_run_id": "old-solver"}, "solver_run_id"),
        ({"project_sha256": "c" * 64}, "project_sha256"),
        ({"design_point_sha256": "d" * 64}, "design_point_sha256"),
        ({"fresh_solver_boundary": False}, "fresh solver boundary"),
        ({"solver_completed_at": "2026-08-12T09:59:00+00:00"}, "timestamps"),
    ],
)
def test_stale_or_mismatched_result_evidence_fails_closed(change, reason):
    evaluation = _evaluate(evidence=[replace(_evidence(), **change)])

    assert evaluation.typed_result_success is False
    assert evaluation.physical_target_success is None
    assert reason in evaluation.criteria[0].message


def test_payload_mutation_after_capture_is_detected():
    payload = _s11()
    evidence = _evidence(payload)
    payload["plot_data"][1]["s_db"] = -30

    evaluation = _evaluate(evidence=[evidence])

    assert evaluation.typed_result_success is False
    assert "SHA-256 mismatch" in evaluation.criteria[0].message


def test_multiple_matching_results_are_ambiguous():
    evaluation = _evaluate(evidence=[_evidence(), replace(_evidence(), result_tool_call_id="exec-1:s11-b")])

    assert evaluation.typed_result_success is True
    assert evaluation.criteria_evaluable is False
    assert evaluation.physical_target_success is None
    assert evaluation.criteria[0].status == "ambiguous_result"


def test_empty_criteria_or_results_do_not_vacuously_pass():
    no_criteria = _evaluate(criteria=[])
    no_results = _evaluate(evidence=[])

    assert no_criteria.typed_result_success is False
    assert no_criteria.physical_target_success is None
    assert no_results.typed_result_success is False
    assert no_results.physical_target_success is None


def test_bare_payload_is_not_accepted_as_trusted_evidence():
    with pytest.raises(AttributeError):
        evaluate_acceptance_criteria([_criterion()], [_s11()], context=_context())


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"target": math.inf}, "target must be finite"),
        ({"frequency_ghz": math.nan}, "frequency_ghz"),
        ({"tolerance": math.inf}, "tolerance"),
    ],
)
def test_acceptance_model_rejects_nonfinite_scalars(changes, match):
    with pytest.raises(ValueError, match=match):
        _criterion(**changes)
