"""半波振子（half-wave dipole）解析初始化与 CST VBA 建模。

初始化公式：arm_length = 0.235 * λ₀（经验修正的有效半波长）
"""
import re
from dataclasses import dataclass
from typing import Dict, Optional


_DIPOLE_KEYWORDS = [
    "振子", "偶极子", "半波振子", "dipole", "half-wave dipole", "half wave dipole",
]

_CREATE_INTENT_KEYWORDS = [
    "创建", "建立", "生成", "建一个", "create", "build", "generate",
]


@dataclass
class DipoleRequest:
    f0_ghz: float
    conductor_thickness_mm: float = 0.035  # 铜箔厚度
    arm_radius_mm: float = 0.5             # 振子臂半径（wire 模式使用）
    wire_or_plate: str = "plate"           # plate=贴片振子, wire=细线振子


def synthesize_dipole(req: DipoleRequest) -> Dict[str, float]:
    """计算半波振子几何尺寸。

    arm_length = 0.235 * λ₀
    arm_width（plate 模式）= max(1.0, arm_length * 0.05)
    gap = max(0.5, arm_length * 0.02)  # 馈电间隙
    """
    lambda0_mm = 299.792458 / req.f0_ghz
    arm_length = 0.235 * lambda0_mm
    arm_width = max(1.0, arm_length * 0.05)
    gap = max(0.5, arm_length * 0.02)
    return {
        "lambda0_mm": lambda0_mm,
        "arm_length": arm_length,
        "arm_width": arm_width,
        "gap": gap,
        "conductor_thickness_mm": req.conductor_thickness_mm,
        "fmin": req.f0_ghz * 0.7,
        "fmax": req.f0_ghz * 1.3,
    }


def build_dipole_vba(req: DipoleRequest, dims: Dict[str, float], component_name: str = "dipole") -> str:
    """生成参数化 CST VBA 代码：store_parameter 定义关键尺寸 + 两段矩形导体 + 离散端口 + 边界 + 远场。

    关键参数（可在 CST 参数编辑器中修改）：
        arm_length — 振子臂长 (mm)
        arm_width  — 振子臂宽 (mm)
        gap        — 馈电间隙 (mm)
        copper_t   — 铜箔厚度 (mm)
        f0         — 目标频率 (GHz)
    """
    from cst_agent_workbench.cst.primitives import (
        set_units, store_parameter, create_brick, create_cylinder,
        set_boundary, set_frequency_range,
        create_farfield_monitor, create_discrete_port,
    )

    arm_l = dims["arm_length"]
    arm_w = dims["arm_width"]
    gap = dims["gap"]
    t = dims["conductor_thickness_mm"]
    f0 = req.f0_ghz
    fmin = str(round(dims["fmin"], 4))
    fmax = str(round(dims["fmax"], 4))
    is_wire = (req.wire_or_plate or "plate").strip().lower() == "wire"
    arm_radius = req.arm_radius_mm

    # 端口坐标（数值，不可用参数变量）
    p1_z = f"{-gap/2:.6f}"
    p2_z = f"{gap/2:.6f}"

    snippets = []

    # 1. 单位
    _, u = set_units("mm", "GHz", "ns")
    snippets.append(u)

    # 2. 参数化关键尺寸（wire 模式额外存 arm_radius，便于 GUI 改）
    base_params = [
        ("arm_length", str(round(arm_l, 6))),
        ("arm_width",  str(round(arm_w, 6))),
        ("gap",        str(round(gap, 6))),
        ("copper_t",   str(round(t, 6))),
        ("f0",         str(round(f0, 6))),
    ]
    if is_wire:
        base_params.append(("arm_radius", str(round(arm_radius, 6))))
    for name, value in base_params:
        _, p = store_parameter(name, value)
        snippets.append(p)

    # 3-4. 振子两臂：plate 模式用 brick，wire 模式用沿 z 轴 cylinder
    if is_wire:
        _, vba = create_cylinder(
            name="upper_arm", component=component_name, material="Copper (annealed)",
            axis="z", outer_radius="arm_radius",
            xcenter="0", ycenter="0",
            zmin="gap/2", zmax="gap/2+arm_length",
        )
        snippets.append(vba)
        _, vba = create_cylinder(
            name="lower_arm", component=component_name, material="Copper (annealed)",
            axis="z", outer_radius="arm_radius",
            xcenter="0", ycenter="0",
            zmin="-(gap/2+arm_length)", zmax="-gap/2",
        )
        snippets.append(vba)
    else:
        _, vba = create_brick(
            name="upper_arm", component=component_name, material="Copper (annealed)",
            xmin="-arm_width/2", xmax="arm_width/2",
            ymin="-copper_t/2",  ymax="copper_t/2",
            zmin="gap/2",        zmax="gap/2+arm_length",
        )
        snippets.append(vba)
        _, vba = create_brick(
            name="lower_arm", component=component_name, material="Copper (annealed)",
            xmin="-arm_width/2",          xmax="arm_width/2",
            ymin="-copper_t/2",           ymax="copper_t/2",
            zmin="-(gap/2+arm_length)",   zmax="-gap/2",
        )
        snippets.append(vba)

    # 5. 离散端口（坐标用数值）
    _, vba = create_discrete_port(
        port_number=1,
        p1_x="0", p1_y="0", p1_z=p1_z,
        p2_x="0", p2_y="0", p2_z=p2_z,
        impedance="50",
    )
    snippets.append(vba)

    # 6. 边界条件
    _, vba = set_boundary(
        xmin="expanded open", xmax="expanded open",
        ymin="expanded open", ymax="expanded open",
        zmin="expanded open", zmax="expanded open",
    )
    snippets.append(vba)

    # 7. 频率范围
    _, vba = set_frequency_range(fmin, fmax)
    snippets.append(vba)

    # 8. 远场监视器
    _, vba = create_farfield_monitor(name=f"farfield (f={f0})", frequency=str(f0))
    snippets.append(vba)

    return "\n\n".join(snippets)


def parse_dipole_request(user_message: str) -> Optional[DipoleRequest]:
    """从用户消息提取频率，返回 DipoleRequest；无法识别返回 None。"""
    if not user_message:
        return None
    text = user_message
    lowered = text.lower()

    has_dipole = any(kw in text or kw in lowered for kw in _DIPOLE_KEYWORDS)
    has_create = any(kw in text or kw in lowered for kw in _CREATE_INTENT_KEYWORDS)
    if not (has_dipole and has_create):
        return None

    # 提取频率
    freq_patterns = [
        r'(?:中心频率|频率|center\s*frequency|target\s*frequency|f0)\s*(?:为|of|=|:)?\s*(\d+(?:\.\d+)?)\s*ghz',
        r'(\d+(?:\.\d+)?)\s*ghz',
    ]
    f0_ghz: Optional[float] = None
    for pattern in freq_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                f0_ghz = float(match.group(1))
                break
            except ValueError:
                continue

    if f0_ghz is None:
        return None

    # 可选：wire 或 plate
    wire_or_plate = "plate"
    if "wire" in lowered or "线" in text:
        wire_or_plate = "wire"

    return DipoleRequest(f0_ghz=f0_ghz, wire_or_plate=wire_or_plate)
