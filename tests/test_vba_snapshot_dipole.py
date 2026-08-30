"""半波振子 VBA snapshot 测试。

显式列出 DipoleRequest 所有字段，不依赖默认值。
wire 和 plate 两种模式独立 snapshot：plate 用矩形 brick，wire 用沿 z 轴 cylinder。
"""
from cst_agent_workbench.cst.dipole_fast import (
    DipoleRequest,
    build_dipole_vba,
    synthesize_dipole,
)


def test_case_2p4ghz(snapshot, reset_primitives):
    request = DipoleRequest(
        f0_ghz=2.4,
        conductor_thickness_mm=0.035,
        arm_radius_mm=0.5,
        wire_or_plate="plate",
    )
    dims = synthesize_dipole(request)
    vba = build_dipole_vba(request, dims)
    snapshot(vba)


def test_case_wire_2p4ghz(snapshot, reset_primitives):
    request = DipoleRequest(
        f0_ghz=2.4,
        conductor_thickness_mm=0.035,
        arm_radius_mm=0.5,
        wire_or_plate="wire",
    )
    dims = synthesize_dipole(request)
    vba = build_dipole_vba(request, dims)
    snapshot(vba)
