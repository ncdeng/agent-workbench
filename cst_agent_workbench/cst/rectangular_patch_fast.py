import math
import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional


_PROBE_FEED_INTENT_KEYWORDS = [
    "probe-fed", "probe fed", "coax-fed", "coax fed", "probe feed",
    "探针馈电", "同轴馈电",
]

_MICROSTRIP_FEED_INTENT_KEYWORDS = [
    "microstrip-fed", "microstrip fed", "microstrip line feed", "microstrip feed",
    "微带线馈电", "微带馈电",
]

_RECTANGULAR_PATCH_KEYWORDS = [
    "矩形微带贴片",
    "矩形贴片",
    "贴片天线",
    "rectangular microstrip patch",
    "rectangular patch",
    "patch antenna",
]

_PATCH_CREATE_INTENT_KEYWORDS = [
    "创建", "建立", "生成", "建一个", "create", "build", "generate",
]

_PATCH_FOLLOWUP_CREATE_KEYWORDS = [
    "再做一个",
    "再建一个",
    "新建一个",
    "重新做一个",
    "再给我做一个",
    "另一个",
    "another",
    "new one",
]

_PATCH_FOLLOWUP_INHERIT_KEYWORDS = [
    "其他都一样",
    "其他一样",
    "其他不变",
    "其余不变",
    "保持一样",
    "和现在这个一样",
    "和前面那个一样",
    "材料属性都一样",
    "板材厚度都一样",
    "同样的材料",
    "同上",
    "照旧",
    "沿用上一个",
    "same as previous",
    "same as before",
    "keep the same",
]

_COPPER_KEYWORDS = ["铜", "copper"]

DEFAULT_PATCH_SUBSTRATE_NAME = "FR-4 (lossy)"
DEFAULT_PATCH_EPSILON_R = 4.3
DEFAULT_PATCH_LOSS_TANGENT = 0.02
DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM = 1.6
DEFAULT_PATCH_CONDUCTOR_NAME = "Copper (annealed)"
DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM = 0.035

_ROGERS_5880_NAME = "Rogers5880"
_ROGERS_5880_EPSILON_R = 2.2
_ROGERS_5880_LOSS_TANGENT = 0.0009


@dataclass
class RectangularPatchRequest:
    f0_ghz: float
    substrate_name: str
    epsilon_r: float
    loss_tangent: float
    substrate_thickness_mm: float
    conductor_name: str
    conductor_thickness_mm: float
    feed_strategy: str


def make_default_rectangular_patch_request(f0_ghz: float, feed_strategy: str) -> RectangularPatchRequest:
    """Return the project default for a standard rectangular microstrip patch."""
    return RectangularPatchRequest(
        f0_ghz=float(f0_ghz),
        substrate_name=DEFAULT_PATCH_SUBSTRATE_NAME,
        epsilon_r=DEFAULT_PATCH_EPSILON_R,
        loss_tangent=DEFAULT_PATCH_LOSS_TANGENT,
        substrate_thickness_mm=DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM,
        conductor_name=DEFAULT_PATCH_CONDUCTOR_NAME,
        conductor_thickness_mm=DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM,
        feed_strategy=feed_strategy,
    )


def normalize_patch_prompt(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("（", "(").replace("）", ")")
    normalized = normalized.replace("，", ",").replace("。", ".").replace("：", ":")
    normalized = normalized.replace("；", ";").replace("＋", "+").replace("－", "-")
    return normalized


def _extract_float(patterns: List[str], text: str) -> Optional[float]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _extract_substrate_name(text: str) -> str:
    if re.search(
        r'\b(?:rogers\s*5880|rt\s*/?\s*duroid\s*5880|rt\s*5880)\b',
        text,
        flags=re.IGNORECASE,
    ):
        return _ROGERS_5880_NAME
    patterns = [
        r'基板(?:用|材料(?:为)?|是)?\s*([A-Za-z0-9_.()\-]+)',
        r'基材(?:用|材料(?:为)?|是)?\s*([A-Za-z0-9_.()\-]+)',
        r'substrate(?:\s+use[sd]?|\s+is|\s*[:=])?\s*([A-Za-z0-9_.()\-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ,.;")
    return DEFAULT_PATCH_SUBSTRATE_NAME


def _is_defaultable_substrate_name(name: str) -> bool:
    normalized = re.sub(r"[\s_\-()]+", "", str(name or "").lower())
    return normalized in {"", "fr4", "fr4lossy", "fr4lossfree"}


def _substrate_property_preset(name: str) -> Optional[tuple[float, float]]:
    normalized = re.sub(r"[\s_\-()/]+", "", str(name or "").lower())
    if normalized in {"rogers5880", "rtduroid5880", "rt5880"}:
        return _ROGERS_5880_EPSILON_R, _ROGERS_5880_LOSS_TANGENT
    return None


def _extract_conductor_name(text: str) -> str:
    lowered = text.lower()
    if any(keyword in text or keyword in lowered for keyword in _COPPER_KEYWORDS):
        return DEFAULT_PATCH_CONDUCTOR_NAME
    return DEFAULT_PATCH_CONDUCTOR_NAME


def _extract_substrate_thickness_mm(text: str) -> Optional[float]:
    patterns = [
        r'(?:基板|基材|substrate)[^,.;\n]{0,80}?(?:厚度|厚|thickness)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm',
        r'(?:h|substrate_h)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm',
    ]
    value = _extract_float(patterns, text)
    if value is not None:
        return value
    generic_matches = re.findall(r'(?:厚度|厚|thickness)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm', text, flags=re.IGNORECASE)
    if generic_matches:
        try:
            return float(generic_matches[0])
        except ValueError:
            return None
    return None


def _extract_conductor_thickness_mm(text: str) -> Optional[float]:
    mm_patterns = [
        r'(?:导体|铜|copper)[^,.;\n]{0,80}?(?:厚度|厚|thickness)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm',
        r'(?:铜厚|导体厚度)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm',
    ]
    value_mm = _extract_float(mm_patterns, text)
    if value_mm is not None:
        return value_mm

    oz_patterns = [
        r'(?:导体|铜|copper)[^,.;\n]{0,80}?(?:厚度|厚|thickness)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:oz|盎司)',
        r'(\d+(?:\.\d+)?)\s*(?:oz|盎司)',
    ]
    ounces = _extract_float(oz_patterns, text)
    if ounces is None:
        return None
    return 0.035 * ounces


def is_rectangular_patch_request(text: str) -> bool:
    if not text:
        return False
    normalized = normalize_patch_prompt(text)
    lowered = normalized.lower()
    has_patch_keyword = any(keyword in normalized or keyword in lowered for keyword in _RECTANGULAR_PATCH_KEYWORDS)
    has_create_intent = any(keyword in normalized or keyword in lowered for keyword in _PATCH_CREATE_INTENT_KEYWORDS)
    return has_patch_keyword and has_create_intent


def extract_patch_target_freq_ghz(text: str) -> Optional[float]:
    normalized = normalize_patch_prompt(text)
    return _extract_float(
        [
            r'(?:中心频率|频率|center\s*frequency|target\s*frequency|f0)\s*(?:为|of|=|:)?\s*(\d+(?:\.\d+)?)\s*ghz',
            r'(\d+(?:\.\d+)?)\s*ghz',
        ],
        normalized,
    )


def parse_rectangular_patch_request(text: str, feed_strategy: str) -> Optional[RectangularPatchRequest]:
    if not is_rectangular_patch_request(text):
        return None

    normalized = normalize_patch_prompt(text)
    f0_ghz = extract_patch_target_freq_ghz(normalized)
    epsilon_r = _extract_float(
        [r'(?:dk|er|epsilon_r|epsilon|介电常数)\s*(?:为|=|:)?\s*(\d+(?:\.\d+)?)'],
        normalized,
    )
    loss_tangent = _extract_float(
        [r'(?:df|tand|tanδ|loss\s*tangent|损耗角正切)\s*(?:为|=|:)?\s*(\d+(?:\.\d+)?)'],
        normalized,
    )
    substrate_h = _extract_substrate_thickness_mm(normalized)
    conductor_t = _extract_conductor_thickness_mm(normalized)

    if f0_ghz is None:
        return None

    substrate_name = _extract_substrate_name(normalized)
    material_preset = _substrate_property_preset(substrate_name)
    missing_material_fields = [
        value is None for value in (epsilon_r, loss_tangent, substrate_h)
    ]
    if (
        any(missing_material_fields)
        and material_preset is None
        and not _is_defaultable_substrate_name(substrate_name)
    ):
        return None

    preset_epsilon_r, preset_loss_tangent = material_preset or (
        DEFAULT_PATCH_EPSILON_R,
        DEFAULT_PATCH_LOSS_TANGENT,
    )
    epsilon_r = preset_epsilon_r if epsilon_r is None else epsilon_r
    loss_tangent = preset_loss_tangent if loss_tangent is None else loss_tangent
    substrate_h = DEFAULT_PATCH_SUBSTRATE_THICKNESS_MM if substrate_h is None else substrate_h
    conductor_t = DEFAULT_PATCH_CONDUCTOR_THICKNESS_MM if conductor_t is None else conductor_t

    return RectangularPatchRequest(
        f0_ghz=f0_ghz,
        substrate_name=substrate_name,
        epsilon_r=epsilon_r,
        loss_tangent=loss_tangent,
        substrate_thickness_mm=substrate_h,
        conductor_name=_extract_conductor_name(normalized),
        conductor_thickness_mm=conductor_t,
        feed_strategy=feed_strategy,
    )


def _mentions_previous_patch_context(text: str) -> bool:
    normalized = normalize_patch_prompt(text)
    lowered = normalized.lower()
    markers = [
        "现在这个",
        "现有这个",
        "前面这个",
        "前面那个",
        "上一个",
        "上一条",
        "当前这个",
        "基于现在",
        "照着现在",
        "在现在这个基础上",
        "current one",
        "previous one",
        "last one",
    ]
    return any(marker in normalized or marker in lowered for marker in markers)


def is_patch_followup_request(text: str) -> bool:
    if not text:
        return False
    normalized = normalize_patch_prompt(text)
    lowered = normalized.lower()
    has_target_freq = extract_patch_target_freq_ghz(normalized) is not None
    has_patch_keyword = any(keyword in normalized or keyword in lowered for keyword in _RECTANGULAR_PATCH_KEYWORDS)
    has_create_intent = any(keyword in normalized or keyword in lowered for keyword in _PATCH_CREATE_INTENT_KEYWORDS)
    has_followup_create = any(keyword in normalized or keyword in lowered for keyword in _PATCH_FOLLOWUP_CREATE_KEYWORDS)
    has_inherit_intent = any(keyword in normalized or keyword in lowered for keyword in _PATCH_FOLLOWUP_INHERIT_KEYWORDS)
    refers_previous = _mentions_previous_patch_context(normalized)

    if not has_target_freq:
        return False
    if has_patch_keyword and (has_create_intent or has_followup_create or has_inherit_intent):
        return True
    return (has_create_intent or has_followup_create) and (has_inherit_intent or refers_previous)


def resolve_rectangular_patch_request(
    text: str,
    feed_strategy: str,
    previous_request: Optional[RectangularPatchRequest] = None,
) -> Optional[RectangularPatchRequest]:
    request = parse_rectangular_patch_request(text, feed_strategy)
    if request is not None:
        return request

    if previous_request is None or not is_patch_followup_request(text):
        return None

    target_freq = extract_patch_target_freq_ghz(text)
    if target_freq is None:
        return None

    normalized = normalize_patch_prompt(text)
    lowered = normalized.lower()
    has_explicit_feed = any(keyword in lowered or keyword in normalized for keyword in (
        _PROBE_FEED_INTENT_KEYWORDS + _MICROSTRIP_FEED_INTENT_KEYWORDS
    ))
    inherited_feed_strategy = previous_request.feed_strategy if not has_explicit_feed else feed_strategy
    return replace(previous_request, f0_ghz=target_freq, feed_strategy=inherited_feed_strategy)


def microstrip_impedance(width_over_h: float, epsilon_r: float) -> float:
    if width_over_h <= 0:
        raise ValueError("width_over_h must be positive")
    eeff = (epsilon_r + 1) / 2 + (epsilon_r - 1) / 2 * (1 / math.sqrt(1 + 12 / width_over_h))
    if width_over_h <= 1:
        return (60 / math.sqrt(eeff)) * math.log(8 / width_over_h + 0.25 * width_over_h)
    return (120 * math.pi) / (
        math.sqrt(eeff) * (width_over_h + 1.393 + 0.667 * math.log(width_over_h + 1.444))
    )


def solve_microstrip_width_mm(epsilon_r: float, substrate_h_mm: float, target_impedance: float = 50.0) -> float:
    low = 0.05
    high = 12.0
    for _ in range(60):
        mid = (low + high) / 2
        z0 = microstrip_impedance(mid, epsilon_r)
        if z0 > target_impedance:
            low = mid
        else:
            high = mid
    return ((low + high) / 2) * substrate_h_mm


def synthesize_rectangular_patch(req: RectangularPatchRequest) -> Dict[str, float]:
    lambda0_mm = 299.792458 / req.f0_ghz
    patch_w = lambda0_mm / 2 * math.sqrt(2 / (req.epsilon_r + 1))
    eeff = (req.epsilon_r + 1) / 2 + (req.epsilon_r - 1) / 2 * (
        1 / math.sqrt(1 + 12 * req.substrate_thickness_mm / patch_w)
    )
    delta_l = 0.412 * req.substrate_thickness_mm * (
        ((eeff + 0.3) * (patch_w / req.substrate_thickness_mm + 0.264))
        / ((eeff - 0.258) * (patch_w / req.substrate_thickness_mm + 0.8))
    )
    patch_l = lambda0_mm / (2 * math.sqrt(eeff)) - 2 * delta_l
    feed_w = solve_microstrip_width_mm(req.epsilon_r, req.substrate_thickness_mm, target_impedance=50.0)
    feed_l = max(3.0, 0.75 * patch_l)
    inset_gap = max(0.2, 0.4 * req.substrate_thickness_mm)
    notch_w = feed_w + 2 * inset_gap
    estimated_edge_resistance = 300.0
    inset_depth = (patch_l / math.pi) * math.acos(math.sqrt(min(0.99, 50.0 / estimated_edge_resistance)))
    inset_depth = min(max(inset_depth, 0.15 * patch_l), 0.45 * patch_l)
    substrate_margin = max(3.0, 6 * req.substrate_thickness_mm)
    rear_margin = substrate_margin
    sub_w = patch_w + 2 * substrate_margin
    sub_l = patch_l + feed_l + rear_margin
    return {
        "patch_w": patch_w,
        "patch_l": patch_l,
        "feed_w": feed_w,
        "feed_l": feed_l,
        "inset_gap": inset_gap,
        "notch_w": notch_w,
        "inset_depth": inset_depth,
        "sub_w": sub_w,
        "sub_l": sub_l,
        "ground_w": sub_w,
        "ground_l": sub_l,
        "substrate_margin": substrate_margin,
        "rear_margin": rear_margin,
        "fmin": max(0.1, req.f0_ghz * 0.75),
        "fmax": req.f0_ghz * 1.25,
    }


def format_mm(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def join_vba(snippets: List[str]) -> str:
    return "\n\n".join(snippet.strip() for snippet in snippets if snippet and snippet.strip())


def build_side_waveguide_port_vba(
    port_number: int,
    pick_solid: str,
    pick_x: str,
    pick_y: str,
    pick_z: str,
    y_pos: str,
    feed_width: str,
    substrate_height: str,
    copper_thickness: str,
) -> str:
    return (
        "Pick.ClearAllPicks\n"
        f'Pick.PickFaceFromPoint "{pick_solid}", {pick_x}, {pick_y}, {pick_z}\n'
        "With Port\n"
        "    .Reset\n"
        f'    .PortNumber "{port_number}"\n'
        f'    .Label "MS_WG_Port_{port_number}"\n'
        '    .Folder ""\n'
        '    .NumberOfModes "1"\n'
        '    .AdjustPolarization "False"\n'
        '    .PolarizationAngle "0.0"\n'
        '    .ReferencePlaneDistance "0.0"\n'
        '    .TextSize "50"\n'
        '    .TextMaxLimit "1"\n'
        '    .Coordinates "Picks"\n'
        '    .Orientation "positive"\n'
        '    .PortOnBound "False"\n'
        '    .ClipPickedPortToBound "False"\n'
        f'    .Xrange "-{feed_width}/2", "{feed_width}/2"\n'
        f'    .Yrange "{y_pos}", "{y_pos}"\n'
        f'    .Zrange "{substrate_height}", "{substrate_height}+{copper_thickness}"\n'
        f'    .XrangeAdd "3*{substrate_height}", "3*{substrate_height}"\n'
        '    .YrangeAdd "0.0", "0.0"\n'
        f'    .ZrangeAdd "{substrate_height}", "3*{substrate_height}"\n'
        '    .SingleEnded "False"\n'
        '    .WaveguideMonitor "False"\n'
        "    .Create\n"
        "End With\n"
        "Pick.ClearAllPicks"
    )


def build_explicit_waveguide_port_vba(
    port_number: int,
    y_pos: str,
    feed_width: str,
    substrate_height: str,
    copper_thickness: str,
) -> str:
    return (
        "With Port\n"
        "    .Reset\n"
        f'    .PortNumber "{port_number}"\n'
        f'    .Label "MS_WG_Port_{port_number}"\n'
        '    .Folder ""\n'
        '    .NumberOfModes "1"\n'
        '    .AdjustPolarization "False"\n'
        '    .PolarizationAngle "0.0"\n'
        '    .ReferencePlaneDistance "0.0"\n'
        '    .TextSize "50"\n'
        '    .TextMaxLimit "1"\n'
        '    .Coordinates "Free"\n'
        '    .Orientation "ymin"\n'
        '    .PortOnBound "False"\n'
        '    .ClipPickedPortToBound "False"\n'
        f'    .Xrange "-{feed_width}/2", "{feed_width}/2"\n'
        f'    .Yrange "{y_pos}", "{y_pos}"\n'
        f'    .Zrange "{substrate_height}", "{substrate_height}+{copper_thickness}"\n'
        f'    .XrangeAdd "3*{substrate_height}", "3*{substrate_height}"\n'
        '    .YrangeAdd "0.0", "0.0"\n'
        f'    .ZrangeAdd "{substrate_height}", "3*{substrate_height}"\n'
        '    .SingleEnded "False"\n'
        '    .WaveguideMonitor "False"\n'
        "    .Create\n"
        "End With"
    )


def synthesize_probe_position(
    dims: Dict[str, float],
    probe_radius_mm: float = 0.5,
    edge_resistance_ohm: float = 300.0,
) -> Dict[str, float]:
    """计算 coax-fed patch 的探针位置（50Ω 匹配）。

    用 Balanis 14-20a：R_in(y_from_edge) = R_edge · cos²(π·y_from_edge / L)
    解出 y_from_edge = (L/π) · acos(sqrt(50 / R_edge))。

    R_edge 是 patch 辐射边缘阻抗（~200..300Ω 取决于 εr），用 300 作经验值。
    返回的 probe_y_offset 是从 patch 中心沿 -y 方向（朝辐射边）的偏移。
    """
    patch_l = dims["patch_l"]
    if not (probe_radius_mm > 0):
        raise ValueError(f"probe_radius_mm 必须 > 0，当前 {probe_radius_mm}")
    if not (edge_resistance_ohm > 50):
        raise ValueError(f"edge_resistance_ohm 必须 > 50，当前 {edge_resistance_ohm}")
    target_z = max(0.05, min(0.95, 50.0 / edge_resistance_ohm))
    y_from_edge = (patch_l / math.pi) * math.acos(math.sqrt(target_z))
    probe_y_offset = -(patch_l / 2.0 - y_from_edge)
    ground_hole_radius_mm = probe_radius_mm + max(0.3, probe_radius_mm * 0.6)
    return {
        "probe_radius_mm": probe_radius_mm,
        "probe_y_offset": probe_y_offset,
        "y_from_edge": y_from_edge,
        "ground_hole_radius_mm": ground_hole_radius_mm,
        "edge_resistance_ohm": edge_resistance_ohm,
    }


def build_rectangular_patch_vba_artifact(
    request: RectangularPatchRequest,
    component_name: str = "AntennaFP1",
    feed_component: str = "FeedFP1",
) -> Dict[str, object]:
    feed = (request.feed_strategy or "microstrip").strip().lower()
    if feed == "probe":
        return _build_probe_fed_patch_vba_artifact(request, component_name, feed_component)
    if feed != "microstrip":
        raise ValueError(
            f"feed_strategy 必须是 'microstrip' 或 'probe'，当前 {request.feed_strategy!r}"
        )

    from cst_agent_workbench.cst import primitives as cst_primitives

    PRIMITIVES = cst_primitives.PRIMITIVES

    dims = synthesize_rectangular_patch(request)
    feed_ymin = -dims["patch_l"] / 2 - dims["feed_l"]
    parameter_values = {
        "f0": request.f0_ghz,
        "er": request.epsilon_r,
        "tand": request.loss_tangent,
        "substrate_h": request.substrate_thickness_mm,
        "copper_t": request.conductor_thickness_mm,
        "patch_W": dims["patch_w"],
        "patch_L": dims["patch_l"],
        "feed_W": dims["feed_w"],
        "feed_L": dims["feed_l"],
        "inset_gap": dims["inset_gap"],
        "notch_W": dims["notch_w"],
        "inset_depth": dims["inset_depth"],
        "sub_W": dims["sub_w"],
        "sub_L": dims["sub_l"],
        "ground_W": dims["ground_w"],
        "ground_L": dims["ground_l"],
        "rear_margin": dims["rear_margin"],
    }

    setup_snippets = []
    _, setup_vba = PRIMITIVES["set_units"]("mm", "GHz", "ns")
    setup_snippets.append(setup_vba)
    for name, value in parameter_values.items():
        _, param_vba = PRIMITIVES["store_parameter"](name, format_mm(value))
        setup_snippets.append(param_vba)
    _, material_vba = PRIMITIVES["create_material"](
        request.substrate_name,
        epsilon=request.epsilon_r,
        tand=request.loss_tangent,
        tand_freq=request.f0_ghz,
    )
    setup_snippets.append(material_vba)
    materials_before = set(cst_primitives._created_materials)
    cst_primitives.register_material(request.substrate_name)
    _, freq_vba = PRIMITIVES["set_frequency_range"](format_mm(dims["fmin"]), format_mm(dims["fmax"]))
    setup_snippets.append(freq_vba)
    # B-1: zmin = "electric" 模拟连续接地铜板（patch 底面物理上是 PEC），
    # 比"expanded open"更准 —— 后者会把地板边缘绕射人为吸收掉。
    _, boundary_vba = PRIMITIVES["set_boundary"](
        xmin="expanded open",
        xmax="expanded open",
        ymin="open",
        ymax="expanded open",
        zmin="electric",
        zmax="expanded open",
    )
    setup_snippets.append(boundary_vba)
    setup_batch_vba = join_vba(setup_snippets)

    try:
        geometry_snippets = []
        for primitive_name, arguments in [
            ("create_brick", {"name": "Ground", "component": component_name, "material": request.conductor_name, "xmin": "-ground_W/2", "xmax": "ground_W/2", "ymin": "-patch_L/2-feed_L", "ymax": "patch_L/2+rear_margin", "zmin": "-copper_t", "zmax": "0"}),
            ("create_brick", {"name": "Substrate", "component": component_name, "material": request.substrate_name, "xmin": "-ground_W/2", "xmax": "ground_W/2", "ymin": "-patch_L/2-feed_L", "ymax": "patch_L/2+rear_margin", "zmin": "0", "zmax": "substrate_h"}),
            ("create_brick", {"name": "Patch", "component": component_name, "material": request.conductor_name, "xmin": "-patch_W/2", "xmax": "patch_W/2", "ymin": "-patch_L/2", "ymax": "patch_L/2", "zmin": "substrate_h", "zmax": "substrate_h+copper_t"}),
            ("create_brick", {"name": "InsetGap", "component": feed_component, "material": "Vacuum", "xmin": "-notch_W/2", "xmax": "notch_W/2", "ymin": "-patch_L/2", "ymax": "-patch_L/2+inset_depth", "zmin": "substrate_h", "zmax": "substrate_h+copper_t"}),
            ("create_brick", {"name": "FeedLine", "component": feed_component, "material": request.conductor_name, "xmin": "-feed_W/2", "xmax": "feed_W/2", "ymin": "-patch_L/2-feed_L", "ymax": "-patch_L/2+inset_depth", "zmin": "substrate_h", "zmax": "substrate_h+copper_t"}),
        ]:
            _, snippet_vba = PRIMITIVES[primitive_name](**arguments)
            geometry_snippets.append(snippet_vba)
        _, subtract_vba = PRIMITIVES["boolean_subtract"](f"{component_name}:Patch", f"{feed_component}:InsetGap")
        geometry_snippets.append(subtract_vba)
        _, add_vba = PRIMITIVES["boolean_add"](f"{component_name}:Patch", f"{feed_component}:FeedLine")
        geometry_snippets.append(add_vba)

        # B-4: 给合并后的 Patch（含馈线段）加 mesh refinement，保证馈线宽度
        # 方向至少 4 个网格，提升阻抗匹配精度。step = feed_w/4。
        feed_mesh_step = max(0.05, dims["feed_w"] / 4.0)
        refine_group = f"feed_refine_{component_name}"
        _, mesh_group_vba = PRIMITIVES["create_mesh_refinement"](refine_group, feed_mesh_step)
        geometry_snippets.append(mesh_group_vba)
        _, mesh_assign_vba = PRIMITIVES["add_solid_to_mesh_group"](
            f"{component_name}:Patch", refine_group
        )
        geometry_snippets.append(mesh_assign_vba)
    finally:
        cst_primitives._created_materials.clear()
        cst_primitives._created_materials.update(materials_before)
    geometry_batch_vba = join_vba(geometry_snippets)

    port_vba = build_side_waveguide_port_vba(
        port_number=1,
        pick_solid=f"{component_name}:Patch",
        pick_x="0",
        pick_y=format_mm(feed_ymin),
        pick_z=format_mm(request.substrate_thickness_mm + request.conductor_thickness_mm / 2),
        y_pos="-patch_L/2-feed_L",
        feed_width="feed_W",
        substrate_height="substrate_h",
        copper_thickness="copper_t",
    )
    _, farfield_vba = cst_primitives.create_farfield_monitor(
        name=f"farfield (f={format_mm(request.f0_ghz)})",
        frequency=format_mm(request.f0_ghz),
        use_subvolume=False,
    )
    sections = {
        "setup": setup_batch_vba,
        "geometry": geometry_batch_vba,
        "port": port_vba,
        "farfield": farfield_vba,
    }
    vba_code = join_vba(
        [
            f"' --- {name} ---\n{section}"
            for name, section in sections.items()
        ]
    )
    return {
        "request": request,
        "dims": dims,
        "parameter_values": parameter_values,
        "sections": sections,
        "vba_code": vba_code,
    }


def _build_probe_fed_patch_vba_artifact(
    request: RectangularPatchRequest,
    component_name: str,
    feed_component: str,
) -> Dict[str, object]:
    """Coax / probe-fed 矩形贴片：探针穿过基板，地板带 clearance hole，
    discrete port 跨越地板厚度。物理结构比 microstrip-fed 简单（patch 不开 inset
    notch），馈电稳定性更高。"""
    from cst_agent_workbench.cst import primitives as cst_primitives

    PRIMITIVES = cst_primitives.PRIMITIVES

    dims = synthesize_rectangular_patch(request)
    probe = synthesize_probe_position(dims)

    parameter_values = {
        "f0": request.f0_ghz,
        "er": request.epsilon_r,
        "tand": request.loss_tangent,
        "substrate_h": request.substrate_thickness_mm,
        "copper_t": request.conductor_thickness_mm,
        "patch_W": dims["patch_w"],
        "patch_L": dims["patch_l"],
        "sub_W": dims["sub_w"],
        "sub_L": dims["sub_l"],
        "ground_W": dims["ground_w"],
        "ground_L": dims["ground_l"],
        "rear_margin": dims["rear_margin"],
        "probe_R": probe["probe_radius_mm"],
        "probe_Y": probe["probe_y_offset"],
        "hole_R": probe["ground_hole_radius_mm"],
    }

    setup_snippets = []
    _, setup_vba = PRIMITIVES["set_units"]("mm", "GHz", "ns")
    setup_snippets.append(setup_vba)
    for name, value in parameter_values.items():
        _, param_vba = PRIMITIVES["store_parameter"](name, format_mm(value))
        setup_snippets.append(param_vba)
    _, material_vba = PRIMITIVES["create_material"](
        request.substrate_name,
        epsilon=request.epsilon_r,
        tand=request.loss_tangent,
        tand_freq=request.f0_ghz,
    )
    setup_snippets.append(material_vba)
    materials_before = set(cst_primitives._created_materials)
    cst_primitives.register_material(request.substrate_name)
    _, freq_vba = PRIMITIVES["set_frequency_range"](
        format_mm(dims["fmin"]), format_mm(dims["fmax"])
    )
    setup_snippets.append(freq_vba)
    # zmin = "expanded open" — probe 下方需要空气域承载激励 gap，不能用 electric。
    _, boundary_vba = PRIMITIVES["set_boundary"](
        xmin="expanded open",
        xmax="expanded open",
        ymin="expanded open",
        ymax="expanded open",
        zmin="expanded open",
        zmax="expanded open",
    )
    setup_snippets.append(boundary_vba)
    setup_batch_vba = join_vba(setup_snippets)

    try:
        geometry_snippets = []
        # 1) Ground / Substrate / Patch（无 inset notch，简单矩形）
        for primitive_name, arguments in [
            ("create_brick", {"name": "Ground", "component": component_name,
                              "material": request.conductor_name,
                              "xmin": "-ground_W/2", "xmax": "ground_W/2",
                              "ymin": "-sub_L/2", "ymax": "sub_L/2",
                              "zmin": "-copper_t", "zmax": "0"}),
            ("create_brick", {"name": "Substrate", "component": component_name,
                              "material": request.substrate_name,
                              "xmin": "-ground_W/2", "xmax": "ground_W/2",
                              "ymin": "-sub_L/2", "ymax": "sub_L/2",
                              "zmin": "0", "zmax": "substrate_h"}),
            ("create_brick", {"name": "Patch", "component": component_name,
                              "material": request.conductor_name,
                              "xmin": "-patch_W/2", "xmax": "patch_W/2",
                              "ymin": "-patch_L/2", "ymax": "patch_L/2",
                              "zmin": "substrate_h", "zmax": "substrate_h+copper_t"}),
        ]:
            _, snippet_vba = PRIMITIVES[primitive_name](**arguments)
            geometry_snippets.append(snippet_vba)

        # 2) 探针（沿 z 从地板顶到 patch 顶，物理上贯穿基板和 patch）
        _, probe_vba = PRIMITIVES["create_cylinder"](
            name="Probe", component=feed_component,
            material=request.conductor_name,
            axis="z", outer_radius="probe_R",
            xcenter="0", ycenter="probe_Y",
            zmin="0", zmax="substrate_h+copper_t",
        )
        geometry_snippets.append(probe_vba)

        # 3) 地板 clearance hole（圆柱 vacuum，从地板下表面到上表面）
        _, hole_vba = PRIMITIVES["create_cylinder"](
            name="GroundHole", component=feed_component,
            material="Vacuum",
            axis="z", outer_radius="hole_R",
            xcenter="0", ycenter="probe_Y",
            zmin="-copper_t", zmax="0",
        )
        geometry_snippets.append(hole_vba)
        _, subtract_vba = PRIMITIVES["boolean_subtract"](
            f"{component_name}:Ground", f"{feed_component}:GroundHole"
        )
        geometry_snippets.append(subtract_vba)

        # 4) Mesh refinement：probe 周围细化（同 microstrip 模式 B-4 思路）
        feed_mesh_step = max(0.05, probe["probe_radius_mm"] / 2.0)
        refine_group = f"probe_refine_{component_name}"
        _, mesh_group_vba = PRIMITIVES["create_mesh_refinement"](
            refine_group, feed_mesh_step
        )
        geometry_snippets.append(mesh_group_vba)
        _, mesh_assign_vba = PRIMITIVES["add_solid_to_mesh_group"](
            f"{feed_component}:Probe", refine_group
        )
        geometry_snippets.append(mesh_assign_vba)
    finally:
        cst_primitives._created_materials.clear()
        cst_primitives._created_materials.update(materials_before)
    geometry_batch_vba = join_vba(geometry_snippets)

    # Discrete port 跨越地板厚度（gap 激励），位置在探针轴线上。
    # 坐标用数值（与 microstrip path 的 pick 坐标做法一致），避免 create_discrete_port
    # 校验阶段未识别参数名。
    probe_y_str = format_mm(probe["probe_y_offset"])
    copper_t_str = format_mm(-request.conductor_thickness_mm)
    _, port_vba = PRIMITIVES["create_discrete_port"](
        port_number=1,
        p1_x="0", p1_y=probe_y_str, p1_z=copper_t_str,
        p2_x="0", p2_y=probe_y_str, p2_z="0",
        impedance="50",
    )

    _, farfield_vba = cst_primitives.create_farfield_monitor(
        name=f"farfield (f={format_mm(request.f0_ghz)})",
        frequency=format_mm(request.f0_ghz),
        use_subvolume=False,
    )
    sections = {
        "setup": setup_batch_vba,
        "geometry": geometry_batch_vba,
        "port": port_vba,
        "farfield": farfield_vba,
    }
    vba_code = join_vba(
        [f"' --- {name} ---\n{section}" for name, section in sections.items()]
    )
    return {
        "request": request,
        "dims": dims,
        "probe": probe,
        "parameter_values": parameter_values,
        "sections": sections,
        "vba_code": vba_code,
    }


def get_patch_feed_strategy(text: str) -> str:
    if not text:
        return "microstrip"
    lowered = text.lower()
    if any(keyword in lowered or keyword in text for keyword in _PROBE_FEED_INTENT_KEYWORDS):
        return "probe"
    if any(keyword in lowered or keyword in text for keyword in _MICROSTRIP_FEED_INTENT_KEYWORDS):
        return "microstrip"
    return "microstrip"

