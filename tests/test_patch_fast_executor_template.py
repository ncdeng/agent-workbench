
from cst_agent_workbench.cst import patch_fast_executor
from cst_agent_workbench.cst.patch_fast_executor import PatchFastExecutorMixin


class _TemplateInstaller(PatchFastExecutorMixin):
    def __init__(self):
        self.events = []

    def _record_fast_path_event(self, *args):
        self.events.append(args)


def _project_with_model_dir(tmp_path):
    project_path = tmp_path / "demo.cst"
    model_dir = tmp_path / "demo" / "Model" / "3D"
    model_dir.mkdir(parents=True)
    return project_path, model_dir


def test_install_farfield_export_template_uses_configured_source(monkeypatch, tmp_path):
    configured = tmp_path / "configured.r0d"
    configured.write_bytes(b"configured-template")
    project_path, model_dir = _project_with_model_dir(tmp_path)

    monkeypatch.setattr(patch_fast_executor.config, "FARFIELD_TEMPLATE_R0D_SOURCE", str(configured))
    monkeypatch.setattr(patch_fast_executor, "_FARFIELD_TEMPLATE_R0D_CONTENT", None)
    monkeypatch.setattr(
        patch_fast_executor,
        "_repo_local_farfield_template_r0d",
        lambda: str(tmp_path / "missing-repo-template.r0d"),
    )

    installer = _TemplateInstaller()
    result = installer._install_farfield_export_template(str(project_path))

    assert result["success"] is True
    assert (model_dir / "Export Farfields in ASCII Format.r0d").read_bytes() == b"configured-template"
    assert not installer.events


def test_install_farfield_export_template_uses_repo_local_template(monkeypatch, tmp_path):
    repo_template = tmp_path / "farfield_template.r0d"
    repo_template.write_bytes(b"repo-template")
    project_path, model_dir = _project_with_model_dir(tmp_path)

    monkeypatch.setattr(patch_fast_executor.config, "FARFIELD_TEMPLATE_R0D_SOURCE", "")
    monkeypatch.setattr(patch_fast_executor, "_FARFIELD_TEMPLATE_R0D_CONTENT", None)
    monkeypatch.setattr(
        patch_fast_executor,
        "_repo_local_farfield_template_r0d",
        lambda: str(repo_template),
    )

    installer = _TemplateInstaller()
    result = installer._install_farfield_export_template(str(project_path))

    assert result["success"] is True
    assert (model_dir / "Export Farfields in ASCII Format.r0d").read_bytes() == b"repo-template"
    assert (model_dir / "Model.rpp").exists()


def test_install_farfield_export_template_reports_missing_template(monkeypatch, tmp_path):
    project_path, model_dir = _project_with_model_dir(tmp_path)

    monkeypatch.setattr(patch_fast_executor.config, "FARFIELD_TEMPLATE_R0D_SOURCE", "")
    monkeypatch.setattr(patch_fast_executor, "_FARFIELD_TEMPLATE_R0D_CONTENT", None)
    monkeypatch.setattr(
        patch_fast_executor,
        "_repo_local_farfield_template_r0d",
        lambda: str(tmp_path / "missing-repo-template.r0d"),
    )

    installer = _TemplateInstaller()
    result = installer._install_farfield_export_template(str(project_path))

    assert result["success"] is False
    assert "未找到 farfield_template.r0d" in result["message"]
    assert not (model_dir / "Model.rpp").exists()
    assert not (model_dir / "Export Farfields in ASCII Format.r0d").exists()
    assert installer.events
    assert "未找到 farfield_template.r0d" in installer.events[0][3]
