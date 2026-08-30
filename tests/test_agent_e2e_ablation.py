import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import agent_e2e_ablation_runner as e2e_runner
from benchmarks.agent_e2e_ablation_runner import (
    _evaluate_argument_oracle,
    _evaluate_tool_oracle,
    _repeat_stability,
    _score_case,
    run_ablation,
    validate_dataset_schema,
    validate_report_schema,
)
from benchmarks.freeze_agent_e2e_dataset import build_manifest, verify_manifest
from benchmarks.agent_e2e_claim_review import apply_claim_review, export_review_template
from benchmarks.build_agent_e2e_frozen_dev_v1 import build_dataset as build_frozen_dataset
from benchmarks.build_agent_e2e_frozen_dev_v2 import build_dataset as build_frozen_dataset_v2


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks" / "agent_e2e_cases.json"
CANONICAL = ROOT / "benchmarks" / "agent_e2e_canonical.json"
FROZEN_DATASET = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v1.json"
FROZEN_DATASET_V2 = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v2.json"


def _write_frozen_dataset(tmp_path: Path) -> tuple[Path, Path]:
    source = json.loads(DATASET.read_text(encoding="utf-8"))
    base = deepcopy(source["cases"][0])
    categories = ["planning", "tool_selection", "groundedness"]
    scenarios = ["status_read", "status_constraints", "multi_turn_context"]
    designs = ["no_design", "generic_vba", "existing_project_solver"]
    failures = ["none", "disconnect"]
    cases = []
    for index in range(30):
        case = deepcopy(base)
        case["case_id"] = f"frozen_case_{index + 1:02d}"
        case["seed"] = 50000 + index
        case["category"] = categories[index % len(categories)]
        case["scenario_family"] = scenarios[index % len(scenarios)]
        case["design_family"] = designs[index % len(designs)]
        case["failure_family"] = failures[index % len(failures)]
        cases.append(case)
    source.update(
        {
            "dataset_id": "frozen-test-v1",
            "split": "frozen_evaluation",
            "dataset_role": "developer_visible_frozen_evaluation",
            "description": "Test-only 30-case frozen dataset.",
            "cases": cases,
        }
    )
    dataset_path = tmp_path / "frozen.json"
    dataset_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    manifest_path = tmp_path / "frozen.manifest.json"
    manifest_path.write_text(
        json.dumps(build_manifest(dataset_path), ensure_ascii=False),
        encoding="utf-8",
    )
    return dataset_path, manifest_path


def test_frozen_agent_e2e_dataset_has_machine_checkable_oracles():
    data = json.loads(DATASET.read_text(encoding="utf-8"))

    assert data["schema_version"] == "agent-e2e-case-v1"
    assert data["dataset_id"] == "cst-agent-e2e-fake-cst-v2"
    assert data["split"] == "development_regression"
    assert data["dataset_role"] == "development_regression"
    assert len(data["cases"]) == 10
    assert len({case["case_id"] for case in data["cases"]}) == 10
    for case in data["cases"]:
        assert case["scenario_family"]
        assert case["design_family"]
        assert case["failure_family"]
        assert case["turns"]
        assert case["seed"] > 0
        assert isinstance(case["fixture"]["injected_failures"], list)
        oracle = case["oracle"]
        assert isinstance(oracle["allowed_tools"], list)
        assert isinstance(oracle["forbidden_tools"], list)
        assert isinstance(oracle["valid_tool_sequences"], list)
        assert all(isinstance(fact, (str, list, dict)) for fact in oracle["required_grounding_facts"])
        for fact in oracle["required_grounding_facts"]:
            if isinstance(fact, dict):
                assert fact["label"]
                assert fact["all_of"]
        assert oracle["max_tool_calls"] >= 0


def test_40_case_frozen_dataset_is_generator_reproducible_and_manifest_verified():
    manifest_path = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v1.manifest.json"
    loaded = json.loads(FROZEN_DATASET.read_text(encoding="utf-8"))

    assert loaded == build_frozen_dataset()
    assert loaded["split"] == "frozen_evaluation"
    assert loaded["dataset_role"] == "developer_visible_frozen_evaluation"
    assert loaded["generation_provenance"]["blinded"] is False
    assert len(loaded["cases"]) == 40
    assert verify_manifest(FROZEN_DATASET, manifest_path) == []
    validate_dataset_schema(loaded)

    manifest_v2 = ROOT / "benchmarks" / "agent_e2e_frozen_dev_v2.manifest.json"
    loaded_v2 = json.loads(FROZEN_DATASET_V2.read_text(encoding="utf-8"))
    assert loaded_v2 == build_frozen_dataset_v2()
    assert loaded_v2["generation_provenance"]["model_outputs_used_to_edit_oracles"] is True
    assert verify_manifest(FROZEN_DATASET_V2, manifest_v2) == []
    validate_dataset_schema(loaded_v2)


def test_full_agent_e2e_runs_real_chat_loop_and_targeted_ablations():
    artifact_root = ROOT / ".pytest_cache" / "agent_e2e"
    report = run_ablation(
        dataset_path=DATASET,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full", "no_context", "no_planner", "no_recovery"],
        artifact_root=artifact_root,
        case_ids=["status_grounded_01", "context_followup_01", "recovery_disconnect_01"],
    )

    assert report["schema_version"] == "agent-e2e-report-v1"
    assert report["run"]["case_count"] == 3
    assert report["run"]["sample_count"] == 3
    assert report["run"]["repeat_count"] == 1
    assert report["run"]["agent_brain"] == "native"
    assert len(report["run"]["harness_identity"]["combined_sha256"]) == 64
    assert report["run"]["secrets_recorded"] is False
    assert report["run"]["large_artifact_root"].startswith(str(ROOT))

    full = report["groups"]["full"]
    assert full["metrics"]["task_success_rate"] == 1.0
    assert full["metrics"]["recovery_rate"] == 1.0
    assert full["metrics"]["tokens"]["estimated"] is True
    assert full["metrics"]["groundedness"]["claim_precision"] is None
    assert full["metrics"]["groundedness"]["required_fact_recall"] == 1.0
    assert full["metrics"]["groundedness"]["matching_method"] == "case_authored_lexical_alternatives"
    assert full["metrics"]["planner"]["fallback_count"] == 0
    assert full["path_proof"]["planner_call_count"] > 0
    assert full["path_proof"]["invalid_ablation_cases"] == []
    assert all(case["trace_status"] == "completed" for case in full["cases"])

    no_context = report["groups"]["no_context"]
    assert no_context["metrics"]["strict_grounded_success_rate"] < full["metrics"]["strict_grounded_success_rate"]
    assert no_context["error_taxonomy"]["grounding_or_context_failure"] >= 1
    assert no_context["path_proof"]["invalid_ablation_cases"] == []

    no_planner = report["groups"]["no_planner"]
    assert no_planner["path_proof"]["planner_call_count"] == 0
    assert no_planner["path_proof"]["invalid_ablation_cases"] == []

    no_recovery = report["groups"]["no_recovery"]
    assert no_recovery["metrics"]["recovery_rate"] == 0.0
    assert no_recovery["path_proof"]["recovery_attempt_count"] == 0


def test_pi_harness_rejects_python_only_deterministic_provider(tmp_path):
    with pytest.raises(ValueError, match="requires provider=openai_compatible"):
        run_ablation(
            dataset_path=DATASET,
            provider="deterministic_proxy",
            agent_brain="pi",
            model="deterministic-e2e",
            group_names=["full"],
            artifact_root=tmp_path / "pi-invalid-provider",
            case_ids=["status_grounded_01"],
        )


def test_report_schema_validation_rejects_missing_dataset_split():
    report = run_ablation(
        dataset_path=DATASET,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=ROOT / ".pytest_cache" / "agent_e2e_schema",
        case_ids=["status_grounded_01"],
    )
    validate_report_schema(report)
    del report["run"]["dataset_split"]
    with pytest.raises(ValueError, match="dataset_split"):
        validate_report_schema(report)


def test_empty_allowlist_and_zero_tool_limit_reject_every_tool_call():
    result = _evaluate_tool_oracle(
        {
            "allowed_tools": [],
            "required_tools_all": [],
            "forbidden_tools": [],
            "valid_tool_sequences": [[]],
            "max_tool_calls": 0,
        },
        ["arbitrary_tool"],
    )

    assert result["hard_constraints_ok"] is False
    assert result["tool_sequence_ok"] is False
    assert result["invalid_calls"] == ["arbitrary_tool"]
    assert {item["name"]: item["passed"] for item in result["constraint_checks"]} == {
        "allowed_tools": False,
        "required_tools_all": True,
        "valid_tool_sequence": False,
        "max_tool_calls": False,
    }


def test_argument_oracle_uses_subset_matching_and_occurrence():
    checks = _evaluate_argument_oracle(
        {
            "expected_tool_arguments": [
                {
                    "tool_name": "run_solver",
                    "occurrence": 2,
                    "arguments": {"timeout": 300, "settings": {"adaptive": True}},
                }
            ]
        },
        [
            {"tool_name": "run_solver", "arguments": {"timeout": 60}},
            {
                "tool_name": "run_solver",
                "arguments": {
                    "timeout": 300.0,
                    "settings": {"adaptive": True, "passes": 4},
                },
            },
        ],
    )

    assert checks[0]["passed"] is True

    semantic = _evaluate_argument_oracle(
        {
            "expected_tool_arguments": [
                {
                    "tool_name": "execute_vba_script",
                    "arguments": {
                        "vba_code": {
                            "$contains": ["RECOVER-1", "ReportInformation"],
                            "$not_contains": ["Delete", "Solver.Start"],
                        }
                    },
                }
            ]
        },
        [
            {
                "tool_name": "execute_vba_script",
                "arguments": {
                    "vba_code": 'Sub Main()\nReportInformation "RECOVER-1 ready"\nEnd Sub'
                },
            }
        ],
    )
    assert semantic[0]["passed"] is True


def test_frozen_runner_requires_matching_manifest_before_creating_artifacts(tmp_path):
    dataset_path, manifest_path = _write_frozen_dataset(tmp_path)
    artifact_root = tmp_path / "artifacts"

    with pytest.raises(ValueError, match="requires a manifest"):
        run_ablation(
            dataset_path=dataset_path,
            provider="deterministic_proxy",
            model="deterministic-e2e",
            group_names=["full"],
            artifact_root=artifact_root,
        )
    assert not artifact_root.exists()

    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    payload["description"] += " byte drift"
    dataset_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest mismatch.*dataset_sha256"):
        run_ablation(
            dataset_path=dataset_path,
            manifest_path=manifest_path,
            provider="deterministic_proxy",
            model="deterministic-e2e",
            group_names=["full"],
            artifact_root=artifact_root,
        )
    assert not artifact_root.exists()


def test_full_frozen_eval_rejects_filters_and_marks_complete_run_release_eligible(tmp_path):
    dataset_path, manifest_path = _write_frozen_dataset(tmp_path)

    with pytest.raises(ValueError, match="cannot use case or family filters"):
        run_ablation(
            dataset_path=dataset_path,
            manifest_path=manifest_path,
            provider="deterministic_proxy",
            model="deterministic-e2e",
            group_names=["full"],
            artifact_root=tmp_path / "filtered",
            case_ids=["frozen_case_01"],
            full_frozen_eval=True,
        )

    report = run_ablation(
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=tmp_path / "full",
        full_frozen_eval=True,
    )

    assert report["run"]["manifest_verified"] is True
    assert report["run"]["evaluation_scope"] == "full_frozen_evaluation"
    assert report["run"]["release_eligible"] is True
    assert report["run"]["case_count"] == 30
    assert all(item["passed"] for item in report["threshold_results"])


def test_task_success_is_not_conflated_with_lexical_grounding():
    agent = SimpleNamespace(
        tool_events=[],
        last_chat_status={"ok": True},
        trace_history=[],
        session=SimpleNamespace(metadata={}),
    )
    case = {
        "case_id": "lexical_false_negative",
        "category": "groundedness",
        "seed": 99,
        "turns": [{"content": "status"}],
        "oracle": {
            "allowed_tools": [],
            "required_tools_all": [],
            "forbidden_tools": [],
            "valid_tool_sequences": [[]],
            "max_tool_calls": 0,
            "required_grounding_facts": ["exact phrase absent"],
            "expected_recovery": None,
        },
    }

    result = _score_case(
        case=case,
        agent=agent,
        responses=["The execution completed with a semantically valid answer."],
        latency_ms=1.0,
        token_before={"prompt": 0, "completion": 0, "total": 0, "calls": 0},
        token_after={"prompt": 0, "completion": 0, "total": 0, "calls": 0},
        client=SimpleNamespace(calls=[]),
        errors=[],
    )

    assert result["task_success"] is True
    assert result["execution_success"] is True
    assert result["strict_grounded_success"] is False


def test_dataset_validator_rejects_duplicate_ids_and_incomplete_frozen_metadata():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    duplicate = deepcopy(dataset)
    duplicate["cases"][1]["case_id"] = duplicate["cases"][0]["case_id"]
    with pytest.raises(ValueError, match="duplicate case_id"):
        validate_dataset_schema(duplicate)

    incomplete = deepcopy(dataset)
    incomplete["split"] = "frozen_evaluation"
    del incomplete["generation_provenance"]
    with pytest.raises(ValueError, match="generation_provenance"):
        validate_dataset_schema(incomplete)


def test_dataset_manifest_detects_byte_or_metadata_drift(tmp_path):
    manifest = build_manifest(DATASET)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_manifest(DATASET, manifest_path) == []

    manifest["case_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_manifest(DATASET, manifest_path) == ["case_count"]


def test_claim_review_is_bound_to_exact_response_and_adds_semantic_metrics(tmp_path):
    report = run_ablation(
        dataset_path=DATASET,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=tmp_path / "artifacts",
        case_ids=["status_grounded_01"],
    )
    review = export_review_template(report)
    case_review = review["groups"]["full"]["cases"][0]
    for fact in case_review["required_fact_judgments"]:
        fact["supported"] = True
        fact["evidence"] = "The tool result reports the connection state."
    case_review["response_claims"] = [
        {
            "claim_text": "CST is online.",
            "supported_by_tool_evidence": True,
            "evidence": "check_cst_status result",
            "notes": "",
        }
    ]

    reviewed = apply_claim_review(report, review)
    groundedness = reviewed["groups"]["full"]["metrics"]["groundedness"]
    assert groundedness["claim_precision"] == 1.0
    assert groundedness["semantic_required_fact_recall"] == 1.0
    assert groundedness["semantic_task_success_rate"] == 1.0

    review["groups"]["full"]["cases"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="response SHA mismatch"):
        apply_claim_review(report, review)


def test_claim_review_uses_sample_ids_for_repeated_cases(tmp_path):
    report = run_ablation(
        dataset_path=DATASET,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=tmp_path / "repeat_review_artifacts",
        case_ids=["status_grounded_01"],
        repeat_count=2,
    )
    review = export_review_template(report)
    samples = review["groups"]["full"]["cases"]

    assert [sample["sample_id"] for sample in samples] == [
        "status_grounded_01::repeat_01",
        "status_grounded_01::repeat_02",
    ]
    for sample in samples:
        for fact in sample["required_fact_judgments"]:
            fact["supported"] = True
            fact["evidence"] = "status evidence"
        sample["response_claims"] = [
            {
                "claim_text": "The status tool completed.",
                "supported_by_tool_evidence": True,
                "evidence": "tool event",
                "notes": "",
            }
        ]

    reviewed = apply_claim_review(report, review)

    assert reviewed["groups"]["full"]["metrics"]["groundedness"][
        "semantic_task_success_rate"
    ] == 1.0


def test_canonical_registry_matches_dataset_and_report_provenance():
    canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))
    assert canonical["schema_version"] == "agent-e2e-evidence-registry-v2"
    assert canonical["external_validation"] == {
        "double_human_reviewer_calibration": "pending",
        "sealed_external_custody_run": "pending",
    }

    def file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    for dataset in canonical["datasets"].values():
        dataset_path = ROOT / dataset["path"]
        manifest_path = ROOT / dataset["manifest_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert file_sha256(dataset_path) == dataset["dataset_sha256"]
        assert file_sha256(manifest_path) == dataset["manifest_sha256"]
        assert manifest["dataset_id"] == dataset["dataset_id"]
        assert manifest["dataset_sha256"] == dataset["dataset_sha256"]
        assert manifest["case_count"] == dataset["case_count"]
        assert dataset["developer_visible"] is True
        assert dataset["blinded"] is False

    for key, item in canonical["reports"].items():
        artifact_path = Path(item["path"])
        if not artifact_path.is_absolute():
            artifact_path = ROOT / artifact_path
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert file_sha256(artifact_path) == item["sha256"]
        dataset_key = item.get("dataset_key")
        dataset = canonical["datasets"].get(dataset_key) if dataset_key else None
        if item["artifact_type"] == "agent_e2e_report":
            assert dataset is not None
            assert artifact["run"]["run_id"] == item["run_id"]
            assert artifact["run"]["dataset_sha256"] == dataset["dataset_sha256"]
            assert artifact["run"]["manifest_sha256"] == dataset["manifest_sha256"]
            assert artifact["run"]["manifest_verified"] is True
        elif item["artifact_type"] == "semantic_audit":
            assert dataset is not None
            source = canonical["reports"][item["source_report_key"]]
            assert artifact["schema_version"] == "agent-e2e-semantic-audit-v2"
            assert artifact["source_run_id"] == item["source_run_id"]
            assert artifact["dataset_sha256"] == dataset["dataset_sha256"]
            assert artifact["provenance"]["source_report"]["sha256"] == source["sha256"]
            assert artifact["provenance"]["manifest"]["sha256"] == dataset["manifest_sha256"]
            assert artifact["provenance"]["manifest_case_order_verified"] is True
        elif item["artifact_type"] == "external_approval_e2e_report":
            assert artifact["schema_version"] == "raw-vba-approval-e2e-report-v1"
            assert artifact["summary"]["all_passed"] is True
            assert artifact["summary"]["checks_passed"] == artifact["summary"]["checks_total"]
            assert artifact["bindings"]["tool_name"] == "execute_vba_script"
            assert artifact["environment"]["project_path"].lower().startswith("d:\\")
        else:  # pragma: no cover - registry schema is intentionally closed in this test
            raise AssertionError(f"unknown artifact type for {key}: {item['artifact_type']}")


def test_repeated_runs_have_unique_samples_confidence_intervals_and_family_filter(tmp_path):
    report = run_ablation(
        dataset_path=DATASET,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=tmp_path / "repeat_artifacts",
        repeat_count=3,
        scenario_families=["status_read"],
    )

    assert report["run"]["case_count"] == 1
    assert report["run"]["sample_count"] == 3
    assert report["run"]["repeat_count"] == 3
    cases = report["groups"]["full"]["cases"]
    assert len({case["sample_id"] for case in cases}) == 3
    assert [case["run_index"] for case in cases] == [1, 2, 3]
    assert [case["seed"] for case in cases] == [21001, 21002, 21003]
    assert all(len(case["final_response_sha256"]) == 64 for case in cases)
    interval = report["groups"]["full"]["metrics"]["confidence_intervals"]["task_success_rate"]
    assert interval["successes"] == 3
    assert interval["trials"] == 3
    assert interval["rate"] == 1.0
    assert 0.0 < interval["low"] < interval["high"] == 1.0
    stability = report["groups"]["full"]["metrics"]["repeat_stability"]
    assert stability["unique_case_count"] == 1
    assert stability["repeat_count_min"] == stability["repeat_count_max"] == 3
    assert stability["case_all_repeats_task_success_rate"] == 1.0
    assert stability["task_success_unstable_case_rate"] == 0.0


def test_repeat_stability_reports_case_level_flakiness_and_tool_variance():
    samples = [
        {
            "case_id": "unstable_case",
            "task_success": True,
            "strict_grounded_success": True,
            "tool_calls": ["check_cst_status"],
            "final_response_sha256": "a" * 64,
        },
        {
            "case_id": "unstable_case",
            "task_success": False,
            "strict_grounded_success": False,
            "tool_calls": ["execute_vba_script"],
            "final_response_sha256": "b" * 64,
        },
        {
            "case_id": "unstable_case",
            "task_success": True,
            "strict_grounded_success": False,
            "tool_calls": ["check_cst_status"],
            "final_response_sha256": "c" * 64,
        },
    ]

    stability = _repeat_stability(samples)

    assert stability["case_all_repeats_task_success_rate"] == 0.0
    assert stability["case_majority_task_success_rate"] == 1.0
    assert stability["task_success_unstable_case_ids"] == ["unstable_case"]
    assert stability["strict_success_unstable_case_ids"] == ["unstable_case"]
    assert stability["tool_sequence_unstable_case_ids"] == ["unstable_case"]
    assert stability["per_case"][0]["dominant_tool_sequence_rate"] == pytest.approx(2 / 3)


def test_case_level_paired_endpoint_does_not_treat_repeats_as_independent():
    full = [
        {"case_id": "a", "task_success": True},
        {"case_id": "a", "task_success": True},
        {"case_id": "a", "task_success": False},
        {"case_id": "b", "task_success": True},
        {"case_id": "b", "task_success": True},
        {"case_id": "b", "task_success": True},
    ]
    ablation = [
        {"case_id": "a", "task_success": False},
        {"case_id": "a", "task_success": False},
        {"case_id": "a", "task_success": True},
        {"case_id": "b", "task_success": True},
        {"case_id": "b", "task_success": True},
        {"case_id": "b", "task_success": True},
    ]

    result = e2e_runner._case_level_paired_endpoint(
        full_cases=full,
        ablation_cases=ablation,
        metric="task_success",
        endpoint="majority",
    )

    assert result["pair_count"] == 2
    assert result["treatment_wins"] == 1
    assert result["ties"] == 1
    assert result["unit"] == "independent_case"


def test_checkpoint_resume_reuses_completed_samples_after_fingerprint_validation(tmp_path, monkeypatch):
    checkpoint = tmp_path / "agent_e2e_checkpoint.json"
    case_id = json.loads(DATASET.read_text(encoding="utf-8"))["cases"][0]["case_id"]
    common = {
        "dataset_path": DATASET,
        "provider": "deterministic_proxy",
        "model": "deterministic-e2e",
        "group_names": ["full"],
        "artifact_root": tmp_path / "checkpoint_artifacts",
        "case_ids": [case_id],
        "repeat_count": 2,
        "checkpoint_path": checkpoint,
    }
    first = run_ablation(**common)

    assert checkpoint.exists()
    assert first["run"]["checkpoint"]["executed_sample_count"] == 2
    assert first["run"]["checkpoint"]["resumed_sample_count"] == 0

    def fail_if_executed(**kwargs):
        raise AssertionError(f"checkpointed sample unexpectedly reran: {kwargs}")

    monkeypatch.setattr(e2e_runner, "_run_case", fail_if_executed)
    resumed = e2e_runner.run_ablation(**common, resume_checkpoint=True)

    assert resumed["run"]["run_id"] == first["run"]["run_id"]
    assert resumed["run"]["checkpoint"]["executed_sample_count"] == 0
    assert resumed["run"]["checkpoint"]["resumed_sample_count"] == 2
    assert [case["task_success"] for case in resumed["groups"]["full"]["cases"]] == [True, True]


def test_checkpoint_resume_rejects_changed_evaluation_identity(tmp_path):
    checkpoint = tmp_path / "agent_e2e_checkpoint.json"
    case_id = json.loads(DATASET.read_text(encoding="utf-8"))["cases"][0]["case_id"]
    common = {
        "dataset_path": DATASET,
        "provider": "deterministic_proxy",
        "model": "deterministic-e2e",
        "group_names": ["full"],
        "artifact_root": tmp_path / "checkpoint_artifacts",
        "case_ids": [case_id],
        "repeat_count": 1,
        "checkpoint_path": checkpoint,
    }
    run_ablation(**common)

    changed = dict(common, repeat_count=2, resume_checkpoint=True)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        run_ablation(**changed)


def test_deterministic_provider_can_drive_a_real_multi_tool_runtime_sequence(tmp_path):
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = deepcopy(dataset["cases"][0])
    case.update(
        {
            "case_id": "multi_tool_read_sequence",
            "scenario_family": "multi_tool_read",
            "turns": [
                {
                    "content": "先检查 CST 状态，再列出当前可用材料；不得执行 VBA 或求解。",
                    "tool_name": None,
                    "tool_arguments": {},
                    "tool_calls": [
                        {"name": "check_cst_status", "arguments": {}},
                        {"name": "list_project_materials", "arguments": {}},
                    ],
                    "planner_constraints": ["不得执行 VBA 或求解"],
                }
            ],
            "oracle": {
                "allowed_tools": ["check_cst_status", "list_project_materials"],
                "required_tools_all": ["check_cst_status", "list_project_materials"],
                "forbidden_tools": ["execute_vba_script", "run_solver"],
                "valid_tool_sequences": [["check_cst_status", "list_project_materials"]],
                "required_grounding_facts": [],
                "expected_recovery": None,
                "max_tool_calls": 2,
            },
        }
    )
    dataset["cases"] = [case]
    dataset_path = tmp_path / "multi_tool_dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    report = run_ablation(
        dataset_path=dataset_path,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=tmp_path / "artifacts",
    )

    result = report["groups"]["full"]["cases"][0]
    assert result["task_success"] is True, {
        "failure_labels": result["failure_labels"],
        "tool_calls": result["tool_calls"],
        "tool_events": result["tool_events"],
        "constraint_checks": result["constraint_checks"],
        "tool_sequence_valid": result["tool_sequence_valid"],
        "errors": result["errors"],
    }
    assert result["tool_calls"] == ["check_cst_status", "list_project_materials"]
    assert result["failure_labels"] == []


def test_repeated_solver_cases_reset_primitive_registry_between_samples(tmp_path):
    source = json.loads(FROZEN_DATASET.read_text(encoding="utf-8"))
    solver_case = next(case for case in source["cases"] if case["case_id"] == "solver_workflow_01")
    development = json.loads(DATASET.read_text(encoding="utf-8"))
    development["dataset_id"] = "solver-isolation-regression"
    development["cases"] = [solver_case]
    dataset_path = tmp_path / "solver_isolation.json"
    dataset_path.write_text(json.dumps(development, ensure_ascii=False), encoding="utf-8")

    report = run_ablation(
        dataset_path=dataset_path,
        provider="deterministic_proxy",
        model="deterministic-e2e",
        group_names=["full"],
        artifact_root=tmp_path / "solver_isolation_artifacts",
        repeat_count=2,
    )

    results = report["groups"]["full"]["cases"]
    assert [item["task_success"] for item in results] == [True, True]
    assert all(item["failed_constraints"] == [] for item in results)
