from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "privacy_scan.py"
SPEC = importlib.util.spec_from_file_location("privacy_scan", SCRIPT)
assert SPEC and SPEC.loader
privacy_scan = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = privacy_scan
SPEC.loader.exec_module(privacy_scan)


def test_scan_redacts_secret_value_from_output():
    secret = "sk-" + "A" * 24
    finding = privacy_scan._scan_text(
        f'API_KEY="{secret}"',
        path="config.py",
    )[0]

    summary = finding.safe_summary()

    assert finding.rule == "provider_key"
    assert secret not in summary
    assert summary == "rule=provider_key;path=config.py;line=1"


def test_explicit_placeholders_are_allowed():
    findings = privacy_scan._scan_text(
        "EMBEDDING_API_KEY=replace_with_embedding_api_key",
        path=".env.example",
    )

    assert findings == []


def test_sha256_digits_are_not_misclassified_as_phone_number():
    findings = privacy_scan._scan_text(
        '"response_sha256": "0b8373c97b715f6f35eb2c1bd62eeced50c43240615a958c18dd137583433d"',
        path="review.json",
    )

    assert not [item for item in findings if item.rule == "mainland_phone"]


def test_real_phone_shape_is_reported_without_value():
    phone = "138" + "12345678"
    findings = privacy_scan._scan_text(f'phone: "{phone}"', path="profile.json")

    assert findings[0].rule == "mainland_phone"
    assert phone not in findings[0].safe_summary()


def test_unlabelled_technical_digits_are_not_reported_as_phone():
    findings = privacy_scan._scan_text(
        '"response_sha256": "not-a-digest-13812345678"',
        path="result.json",
    )

    assert not [item for item in findings if item.rule == "mainland_phone"]


def test_personal_artifacts_are_rejected_when_git_would_include_them(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy_scan, "ROOT", tmp_path)
    (tmp_path / "resume.pdf").write_bytes(b"private")

    findings = privacy_scan.scan_paths(["resume.pdf"])

    assert findings == (privacy_scan.Finding("personal_artifact", "resume.pdf"),)


def test_benchmark_reports_receive_the_same_privacy_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy_scan, "ROOT", tmp_path)
    report = tmp_path / "benchmarks" / "reports" / "run.json"
    report.parent.mkdir(parents=True)
    private_path = "C:" + r"\Users\private-user\case.cst"
    report.write_text(f'{{"path":"{private_path}"}}', encoding="utf-8")

    findings = privacy_scan.scan_paths(["benchmarks/reports/run.json"])

    assert findings[0].rule == "windows_user_path"
