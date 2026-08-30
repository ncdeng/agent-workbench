"""矩形贴片 VBA snapshot 测试。

显式列出 RectangularPatchRequest 所有字段：dataclass 默认值变更不应静默改 snapshot。

当前 build_rectangular_patch_vba_artifact 只支持 microstrip feed（line 380 raise），
两个 case 用同一 feed_strategy + 不同基板覆盖 FR-4 / Rogers 两条材料分支。
若以后扩展 probe-fed/coax-fed，再加 case。
"""
from cst_agent_workbench.cst.rectangular_patch_fast import (
    RectangularPatchRequest,
    build_rectangular_patch_vba_artifact,
)


def test_case_fr4_2p4ghz(snapshot, reset_primitives):
    request = RectangularPatchRequest(
        f0_ghz=2.4,
        substrate_name="FR-4 (lossy)",
        epsilon_r=4.4,
        loss_tangent=0.02,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="microstrip",
    )
    artifact = build_rectangular_patch_vba_artifact(request)
    snapshot(artifact["vba_code"])


def test_case_rogers5880_9p4ghz(snapshot, reset_primitives):
    request = RectangularPatchRequest(
        f0_ghz=9.4,
        substrate_name="Rogers5880",
        epsilon_r=2.2,
        loss_tangent=0.0009,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="microstrip",
    )
    artifact = build_rectangular_patch_vba_artifact(request)
    snapshot(artifact["vba_code"])


def test_case_probe_fr4_2p4ghz(snapshot, reset_primitives):
    """D-1: probe-fed (coax) patch on FR-4 at 2.4 GHz。
    几何完全不同于 microstrip：无 inset notch，加探针 cylinder + ground hole。"""
    request = RectangularPatchRequest(
        f0_ghz=2.4,
        substrate_name="FR-4 (lossy)",
        epsilon_r=4.4,
        loss_tangent=0.02,
        substrate_thickness_mm=1.6,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="probe",
    )
    artifact = build_rectangular_patch_vba_artifact(request)
    snapshot(artifact["vba_code"])
