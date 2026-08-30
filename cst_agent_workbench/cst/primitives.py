# CST VBA 受控原语函数
# 每个函数生成已验证的 VBA 片段（不含 Sub/End Sub），供 add_to_history 使用。
# 边界条件语法参考 CST 2025 官方示例 pcb_3d_simulation.py。

import re
from typing import Dict, List, Optional, Set, Tuple

# CST 内置材料名（常用子集，用于基础校验）
BUILTIN_MATERIALS = {
    "PEC", "Vacuum",
    "FR-4 (lossy)", "FR-4 (loss free)",
    "Copper (annealed)", "Copper (pure)",
    "Air",
    "Rogers RT5880 (lossy)", "Rogers RT5880 (loss free)",
    "Teflon (PTFE)",
    "Silicon",
    "Alumina (96%)",
}

# 支持的边界类型（参考 CST 2025 VBA 报错信息中的完整列表）
VALID_BOUNDARY_TYPES = {
    "open", "expanded open", "expanded lf open",
    "electric", "magnetic", "normal", "tangential",
    "periodic", "impedance", "unit cell",
}

# 支持的求解器类型（参考 pcb_3d_simulation.py: ChangeSolverType）
VALID_SOLVER_TYPES = {
    "HF Time Domain", "HF Frequency Domain", "HF IntegralEq",
    "HF Multilayer", "HF Eigenmode", "HF Asymptotic",
}

class ProjectRegistry:
    """Encapsulates CST project state (objects, materials, parameters, ports, etc.).

    Provides the same interface as the module-level functions but allows
    independent instances for testing or multi-project scenarios.
    """

    def __init__(self):
        self.objects: Dict[str, str] = {}
        self.materials: Set[str] = set()
        self.parameters: Dict[str, str] = {}
        self.ports: List[Dict] = []
        self.farfield_monitors: List[Dict] = []
        self.field_monitors: List[Dict] = []
        self.frequency_range: Dict[str, str] = {}

    def reset(self):
        self.objects.clear()
        self.materials.clear()
        self.parameters.clear()
        self.ports.clear()
        self.farfield_monitors.clear()
        self.field_monitors.clear()
        self.frequency_range.clear()

    def check_name_conflict(self, component: str, name: str) -> Optional[str]:
        key = f"{component}:{name}"
        if key in self.objects:
            return f"警告：对象 '{key}' 已存在，CST 可能报错 'This name already exists'。请使用不同的 name。"
        return None

    def register_object(self, component: str, name: str, material: str = ""):
        self.objects[f"{component}:{name}"] = material

    def unregister_object(self, component: str, name: str):
        self.objects.pop(f"{component}:{name}", None)

    def register_material(self, name: str):
        self.materials.add(name)

    def register_port(self, port_number: int, impedance: str = "50"):
        self.ports.append({"port_number": port_number, "impedance": impedance})

    def register_waveguide_port(self, port_number: int, number_of_modes: int, coordinate_mode: str):
        self.ports.append({
            "port_number": int(port_number),
            "port_type": "waveguide",
            "number_of_modes": int(number_of_modes),
            "coordinate_mode": str(coordinate_mode),
        })

    def register_farfield_monitor(self, name: str, frequency: str, use_subvolume: bool = False):
        self.farfield_monitors.append({
            "name": str(name),
            "frequency": str(frequency),
            "use_subvolume": bool(use_subvolume),
        })

    def get_farfield_monitors(self) -> List[Dict]:
        return list(self.farfield_monitors)

    def register_field_monitor(self, name: str, frequency: str, field_type: str):
        monitor = {
            "name": str(name),
            "frequency": str(frequency),
            "field_type": str(field_type),
        }
        self.field_monitors.append(monitor)
        if field_type == "Farfield":
            self.register_farfield_monitor(name, frequency, False)

    def get_field_monitors(self) -> List[Dict]:
        return list(self.field_monitors)

    def register_frequency_range(self, fmin: str, fmax: str):
        self.frequency_range["fmin"] = str(fmin)
        self.frequency_range["fmax"] = str(fmax)

    def get_frequency_range(self) -> Dict[str, str]:
        return dict(self.frequency_range)

    def register_parameter(self, name: str, value: str):
        self.parameters[name] = str(value)

    def get_parameters(self) -> Dict[str, str]:
        return self.parameters.copy()

    def get_model_summary(self) -> Dict:
        return {
            "objects": dict(self.objects),
            "ports": list(self.ports),
            "parameters": dict(self.parameters),
            "farfield_monitors": list(self.farfield_monitors),
            "field_monitors": list(self.field_monitors),
            "frequency_range": dict(self.frequency_range),
        }

    def validate_material(self, material: str) -> Optional[str]:
        if material in BUILTIN_MATERIALS or material in self.materials:
            return None
        return (
            f"警告：材料 '{material}' 既不是 CST 内置材料，也未通过 create_material 创建。"
            f"CST 可能报错。如果是自定义材料，请先调用 create_material 创建。"
            f"内置材料列表：{', '.join(sorted(BUILTIN_MATERIALS))}"
        )


# Default global registry — backward-compatible module-level access
_registry = ProjectRegistry()

_created_objects = _registry.objects
_created_materials = _registry.materials
_created_parameters = _registry.parameters
_created_ports = _registry.ports
_created_farfield_monitors = _registry.farfield_monitors
_created_field_monitors = _registry.field_monitors
_last_frequency_range = _registry.frequency_range


def reset_created_objects():
    """重置已创建对象、材料、参数和端口注册表（新建工程时调用）"""
    _registry.reset()


def _check_name_conflict(component: str, name: str) -> Optional[str]:
    """检查对象名是否已存在。返回警告信息或 None。"""
    return _registry.check_name_conflict(component, name)


def register_object(component: str, name: str, material: str = ""):
    """注册已成功创建的对象（execute_vba 成功后调用）。"""
    _registry.register_object(component, name, material)


def unregister_object(component: str, name: str):
    """注销已被布尔操作吸收或删除的对象。"""
    _registry.unregister_object(component, name)


def register_material(name: str):
    """注册已成功创建的材料（execute_vba 成功后调用）。"""
    _registry.register_material(name)


def register_port(port_number: int, impedance: str = "50"):
    """注册已成功创建的端口（execute_vba 成功后调用）。"""
    _registry.register_port(port_number, impedance)


def register_waveguide_port(port_number: int, number_of_modes: int, coordinate_mode: str):
    _registry.register_waveguide_port(port_number, number_of_modes, coordinate_mode)


def register_farfield_monitor(name: str, frequency: str, use_subvolume: bool = False):
    """注册已成功创建的 farfield monitor。"""
    _registry.register_farfield_monitor(name, frequency, use_subvolume)


def get_farfield_monitors() -> List[Dict]:
    """返回当前工程中已知的 farfield monitor。"""
    return _registry.get_farfield_monitors()


def register_field_monitor(name: str, frequency: str, field_type: str):
    """注册已成功创建的统一频域场 monitor。"""
    _registry.register_field_monitor(name, frequency, field_type)


def get_field_monitors() -> List[Dict]:
    return _registry.get_field_monitors()


def register_frequency_range(fmin: str, fmax: str):
    """记录最近一次设置的频率范围。"""
    _registry.register_frequency_range(fmin, fmax)


def get_frequency_range() -> Dict[str, str]:
    """返回最近一次设置的频率范围。"""
    return _registry.get_frequency_range()


def register_parameter(name: str, value: str):
    """注册已成功定义的 CST 参数（execute_vba 成功后调用）。"""
    _registry.register_parameter(name, value)


def get_parameters() -> Dict[str, str]:
    """返回当前工程的所有参数。"""
    return _registry.get_parameters()


def get_model_summary() -> Dict:
    """返回当前模型状态摘要，供前端面板显示。"""
    return _registry.get_model_summary()


def _validate_material(material: str) -> Optional[str]:
    """校验材料名是否为内置材料或已通过 create_material 创建。返回警告信息或 None。"""
    return _registry.validate_material(material)


def _validate_number(value, name: str, min_val=None, max_val=None) -> str:
    """校验数值参数，返回字符串形式"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"参数 {name} 必须是数值，实际值: {value}")
    if min_val is not None and v < min_val:
        raise ValueError(f"参数 {name}={v} 不能小于 {min_val}")
    if max_val is not None and v > max_val:
        raise ValueError(f"参数 {name}={v} 不能大于 {max_val}")
    if v == int(v):
        return str(int(v))
    return str(v)


def _validate_vba_string(value, name: str, *, allow_empty: bool = False) -> str:
    """Return a safe one-line string for embedding in generated VBA."""
    text = str("" if value is None else value).strip()
    if not text and not allow_empty:
        raise ValueError(f"参数 {name} 不能为空")
    if any(char in text for char in ('"', "\r", "\n", "\x00")):
        raise ValueError(f"参数 {name} 包含不允许的 VBA 字符")
    return text


def _validate_vector(values, name: str, *, require_nonzero: bool = False) -> Tuple[str, str, str]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError(f"参数 {name} 必须是长度为 3 的数组")
    vector = tuple(_validate_vba_string(value, f"{name}[{index}]") for index, value in enumerate(values))
    if require_nonzero:
        try:
            if all(float(value) == 0 for value in vector):
                raise ValueError(f"参数 {name} 不能是零向量")
        except ValueError as exc:
            if "不能是零向量" in str(exc):
                raise
            # Parameter expressions cannot be evaluated here; CST validates them at rebuild time.
    return vector


# ── 单位设置 ──

def set_units(geometry: str = "mm", frequency: str = "GHz",
              time: str = "ns") -> Tuple[str, str]:
    """设置单位系统。返回 (label, vba_code)。"""
    valid_geo = {"mm", "um", "m", "cm", "mil", "inch", "ft"}
    valid_freq = {"Hz", "kHz", "MHz", "GHz", "THz"}
    if geometry not in valid_geo:
        raise ValueError(f"geometry 单位 '{geometry}' 不合法，可选: {valid_geo}")
    if frequency not in valid_freq:
        raise ValueError(f"frequency 单位 '{frequency}' 不合法，可选: {valid_freq}")
    vba = (
        f'With Units\n'
        f'    .Geometry "{geometry}"\n'
        f'    .Frequency "{frequency}"\n'
        f'    .Time "{time}"\n'
        f'End With'
    )
    return ("set units", vba)


# ── 参数变量 ──

def store_parameter(name: str, value: str) -> Tuple[str, str]:
    """定义 CST 参数变量。
    参数名可在后续几何体的范围参数中引用（如 create_brick 的 xmin 写 "ground_L/2"）。
    value 可以是数值字符串（如 "30"）或引用其他参数的表达式（如 "patch_L+2*margin"）。
    参考 CST 2025 VBA: StoreDoubleParameter / MakeSureParameterExists。
    返回 (label, vba_code)。"""
    if not name or not name.strip():
        raise ValueError("参数名不能为空")
    name = name.strip()
    # 参数名只允许字母、数字、下划线（CST 参数命名规则）
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
        raise ValueError(
            f"参数名 '{name}' 不合法，只允许字母、数字和下划线，且以字母或下划线开头"
        )
    if not value or not str(value).strip():
        raise ValueError("参数值不能为空")
    value = str(value).strip()
    # 检查 value 中引用的参数名是否已定义（简单检查：提取纯字母下划线标识符）
    # 注意：value 可能是纯数值（如 "30"），也可能是表达式（如 "patch_L+2*margin"）
    # StoreParameter 接受字符串表达式（如 "40" 或 "patch_L+2*margin"）
    # StoreDoubleParameter 只接受纯数值，不适合表达式场景
    # 参考官方宏: StoreParameter("SubstrateHeight", sParameter1)
    vba = f'StoreParameter "{name}", "{value}"'
    return (f"define parameter: {name}={value}", vba)


def delete_parameter(name: str) -> Tuple[str, str]:
    """删除 CST 参数变量。返回 (label, vba_code)。"""
    if not name or not name.strip():
        raise ValueError("参数名不能为空")
    name = name.strip()
    vba = f'DeleteParameter "{name}"'
    return (f"delete parameter: {name}", vba)


# ── 材料 ──

def _auto_material_color(name: str) -> Tuple[str, str, str]:
    """根据材料名称自动选择有辨识度的 RGB 颜色。"""
    nl = name.lower()
    if any(k in nl for k in ["fr-4", "fr4", "rogers", "ro4", "substrate", "teflon", "ptfe", "alumina"]):
        return ("0.85", "0.65", "0.20")   # 琥珀色 — 介质基板
    if any(k in nl for k in ["copper", "cu"]):
        return ("0.85", "0.55", "0.20")   # 铜色 — 导体层
    if "pec" in nl:
        return ("0.3", "0.3", "0.3")      # 深灰 — 理想导体
    if "silicon" in nl:
        return ("0.5", "0.5", "0.7")      # 蓝灰 — 半导体
    return ("0.6", "0.6", "0.6")           # 中灰 — 默认


def create_material(name: str, epsilon: float = 1.0, mue: float = 1.0,
                    kappa: float = 0, tand: float = 0, tand_freq: float = 0,
                    colour_r: str = None, colour_g: str = None,
                    colour_b: str = None) -> Tuple[str, str]:
    """创建自定义高频材料。
    对齐 CST 2025 官方材料宏模板（ADS Converter 1.bas 第 2585 行、vba_snippets.py）。
    关键：当 tand != 0 时，必须设 .TanDGiven "True" + .TanDModel "ConstTanD"，
    否则 CST 的 Tangent delta 不会真正生效。
    返回 (label, vba_code)。"""
    if not name or not name.strip():
        raise ValueError("材料名称不能为空")
    if colour_r is None:
        colour_r, colour_g, colour_b = _auto_material_color(name)
    eps = _validate_number(epsilon, "epsilon", min_val=0)
    mu = _validate_number(mue, "mue", min_val=0)
    # 根据 tand 是否非零决定 TanDGiven 和 TanDModel
    tand_given = "True" if float(tand) != 0 else "False"
    tand_model = "ConstTanD" if float(tand) != 0 else "ConstSigma"
    vba = (
        f'With Material\n'
        f'    .Reset\n'
        f'    .Name "{name}"\n'
        f'    .Folder ""\n'
        f'    .FrqType "hf"\n'
        f'    .Type "Normal"\n'
        f'    .MaterialUnit "Frequency", "GHz"\n'
        f'    .MaterialUnit "Geometry", "mm"\n'
        f'    .Epsilon "{eps}"\n'
        f'    .Mue "{mu}"\n'
        f'    .Kappa "{kappa}"\n'
        f'    .TanD "{tand}"\n'
        f'    .TanDFreq "{tand_freq}"\n'
        f'    .TanDGiven "{tand_given}"\n'
        f'    .TanDModel "{tand_model}"\n'
        f'    .SetConstTanDStrategyEps "AutomaticOrder"\n'
        f'    .ConstTanDModelOrderEps "3"\n'
        f'    .DjordjevicSarkarUpperFreqEps "0"\n'
        f'    .SetElParametricConductivity "False"\n'
        f'    .KappaM "0.0"\n'
        f'    .TanDM "0.0"\n'
        f'    .TanDMFreq "0.0"\n'
        f'    .TanDMGiven "False"\n'
        f'    .TanDMModel "ConstSigma"\n'
        f'    .SetConstTanDStrategyMu "AutomaticOrder"\n'
        f'    .ConstTanDModelOrderMu "3"\n'
        f'    .DjordjevicSarkarUpperFreqMu "0"\n'
        f'    .SetMagParametricConductivity "False"\n'
        f'    .DispModelEps "None"\n'
        f'    .DispModelMue "None"\n'
        f'    .Rho "0.0"\n'
        f'    .Colour "{colour_r}", "{colour_g}", "{colour_b}"\n'
        f'    .Wireframe "False"\n'
        f'    .Transparency "0"\n'
        f'    .Create\n'
        f'End With'
    )
    return (f"define material: {name}", vba)


# ── 几何原语 ──

def create_brick(name: str, component: str, material: str,
                 xmin: str, xmax: str, ymin: str, ymax: str,
                 zmin: str, zmax: str) -> Tuple[str, str]:
    """创建长方体。范围参数支持 CST 表达式（如 "10/2"）。返回 (label, vba_code)。"""
    if not name:
        raise ValueError("name 不能为空")
    if not component:
        raise ValueError("component 不能为空")
    if not material:
        raise ValueError("material 不能为空")
    material_warning = _validate_material(material)
    if material_warning:
        raise ValueError(material_warning)
    conflict_warning = _check_name_conflict(component, name)
    if conflict_warning:
        raise ValueError(conflict_warning)
    vba = (
        f'With Brick\n'
        f'    .Reset\n'
        f'    .Name "{name}"\n'
        f'    .Component "{component}"\n'
        f'    .Material "{material}"\n'
        f'    .Xrange "{xmin}", "{xmax}"\n'
        f'    .Yrange "{ymin}", "{ymax}"\n'
        f'    .Zrange "{zmin}", "{zmax}"\n'
        f'    .Create\n'
        f'End With'
    )
    return (f"define brick: {component}:{name}", vba)


def create_cylinder(name: str, component: str, material: str,
                    axis: str, outer_radius: str,
                    inner_radius: str = "0",
                    xcenter: str = "0", ycenter: str = "0",
                    zmin: str = "0", zmax: str = "0") -> Tuple[str, str]:
    """创建圆柱体。返回 (label, vba_code)。"""
    if axis.lower() not in ("x", "y", "z"):
        raise ValueError(f"axis 必须是 x/y/z，实际值: {axis}")
    if not name:
        raise ValueError("name 不能为空")
    material_warning = _validate_material(material)
    if material_warning:
        raise ValueError(material_warning)
    conflict_warning = _check_name_conflict(component, name)
    if conflict_warning:
        raise ValueError(conflict_warning)
    vba = (
        f'With Cylinder\n'
        f'    .Reset\n'
        f'    .Name "{name}"\n'
        f'    .Component "{component}"\n'
        f'    .Material "{material}"\n'
        f'    .OuterRadius "{outer_radius}"\n'
        f'    .InnerRadius "{inner_radius}"\n'
        f'    .Axis "{axis}"\n'
        f'    .Xcenter "{xcenter}"\n'
        f'    .Ycenter "{ycenter}"\n'
        f'    .Zrange "{zmin}", "{zmax}"\n'
        f'    .Create\n'
        f'End With'
    )
    return (f"define cylinder: {component}:{name}", vba)


def create_extruded_polygon(
    name: str,
    component: str,
    material: str,
    points: List[List[str]],
    height: str,
    origin: List[str] = None,
    u_vector: List[str] = None,
    v_vector: List[str] = None,
) -> Tuple[str, str]:
    """Create a parameterized solid by extruding a closed point-list profile.

    This follows CST 2025 ``Extrude.Mode \"Pointlist\"`` directly instead of
    creating a temporary curve. One History entry therefore owns the complete
    profile and remains rebuildable when CST parameters change.
    """
    object_name = _validate_vba_string(name, "name")
    component_name = _validate_vba_string(component, "component")
    material_name = _validate_vba_string(material, "material")
    material_warning = _validate_material(material_name)
    if material_warning:
        raise ValueError(material_warning)
    conflict_warning = _check_name_conflict(component_name, object_name)
    if conflict_warning:
        raise ValueError(conflict_warning)
    if not isinstance(points, list) or not 3 <= len(points) <= 512:
        raise ValueError("points 必须包含 3 到 512 个二维顶点")
    normalized_points: List[Tuple[str, str]] = []
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"points[{index}] 必须是 [u, v]")
        normalized_points.append(
            (
                _validate_vba_string(point[0], f"points[{index}][0]"),
                _validate_vba_string(point[1], f"points[{index}][1]"),
            )
        )
    if normalized_points[-1] == normalized_points[0]:
        normalized_points.pop()
    if len(normalized_points) < 3 or len(set(normalized_points)) < 3:
        raise ValueError("polygon 必须至少包含 3 个不同顶点")
    height_value = _validate_vba_string(height, "height")
    origin_values = _validate_vector(origin or ["0", "0", "0"], "origin")
    u_values = _validate_vector(u_vector or ["1", "0", "0"], "u_vector", require_nonzero=True)
    v_values = _validate_vector(v_vector or ["0", "1", "0"], "v_vector", require_nonzero=True)
    point_lines = [f'    .Point "{normalized_points[0][0]}", "{normalized_points[0][1]}"']
    point_lines.extend(f'    .LineTo "{u}", "{v}"' for u, v in normalized_points[1:])
    vba = "\n".join(
        [
            "With Extrude",
            "    .Reset",
            f'    .Name "{object_name}"',
            f'    .Component "{component_name}"',
            f'    .Material "{material_name}"',
            '    .Mode "Pointlist"',
            f'    .Height "{height_value}"',
            '    .Twist "0.0"',
            '    .Taper "0.0"',
            f'    .Origin "{origin_values[0]}", "{origin_values[1]}", "{origin_values[2]}"',
            f'    .Uvector "{u_values[0]}", "{u_values[1]}", "{u_values[2]}"',
            f'    .Vvector "{v_values[0]}", "{v_values[1]}", "{v_values[2]}"',
            *point_lines,
            "    .Create",
            "End With",
        ]
    )
    return (f"define extruded polygon: {component_name}:{object_name}", vba)


def transform_shape(
    shape: str,
    operation: str,
    vector: List[str] = None,
    angle: List[str] = None,
    center: List[str] = None,
    copy: bool = False,
    repetitions: int = 1,
    unite: bool = False,
) -> Tuple[str, str]:
    """Translate or rotate one solid with bounded repetitions."""
    shape_name = _validate_vba_string(shape, "shape")
    if ":" not in shape_name:
        raise ValueError("shape 格式应为 'Component:Name'")
    operation_name = str(operation or "").strip().lower()
    if operation_name not in {"translate", "rotate"}:
        raise ValueError("operation 必须是 translate 或 rotate")
    try:
        repetitions_value = int(repetitions)
    except (TypeError, ValueError) as exc:
        raise ValueError("repetitions 必须是整数") from exc
    if not 1 <= repetitions_value <= 64:
        raise ValueError("repetitions 必须在 1 到 64 之间")
    vector_values = _validate_vector(vector or ["0", "0", "0"], "vector")
    angle_values = _validate_vector(angle or ["0", "0", "0"], "angle")
    center_values = _validate_vector(center or ["0", "0", "0"], "center")
    values = vector_values if operation_name == "translate" else angle_values
    try:
        if all(float(value) == 0 for value in values):
            raise ValueError(f"{operation_name} 变换不能全部为 0")
    except ValueError as exc:
        if "不能全部为 0" in str(exc):
            raise
    copy_flag = "True" if copy else "False"
    unite_flag = "True" if unite else "False"
    operation_title = "Translate" if operation_name == "translate" else "Rotate"
    vba = (
        "With Transform\n"
        "    .Reset\n"
        f'    .Name "{shape_name}"\n'
        f'    .Vector "{vector_values[0]}", "{vector_values[1]}", "{vector_values[2]}"\n'
        f'    .Angle "{angle_values[0]}", "{angle_values[1]}", "{angle_values[2]}"\n'
        '    .Origin "Free"\n'
        f'    .Center "{center_values[0]}", "{center_values[1]}", "{center_values[2]}"\n'
        f'    .MultipleObjects "{copy_flag}"\n'
        f'    .GroupObjects "{unite_flag}"\n'
        f'    .Repetitions "{repetitions_value}"\n'
        '    .MultipleSelection "False"\n'
        f'    .Transform "Shape", "{operation_title}"\n'
        "End With"
    )
    return (f"{operation_name} shape: {shape_name}", vba)


def set_wcs(
    mode: str = "global",
    origin: List[str] = None,
    u_vector: List[str] = None,
    normal: List[str] = None,
) -> Tuple[str, str]:
    """Activate global WCS or define and activate a local coordinate system."""
    mode_value = str(mode or "").strip().lower()
    if mode_value not in {"global", "local"}:
        raise ValueError("mode 必须是 global 或 local")
    if mode_value == "global":
        return ("activate global WCS", 'WCS.ActivateWCS "global"')
    origin_values = _validate_vector(origin or ["0", "0", "0"], "origin")
    u_values = _validate_vector(u_vector or ["1", "0", "0"], "u_vector", require_nonzero=True)
    normal_values = _validate_vector(normal or ["0", "0", "1"], "normal", require_nonzero=True)
    vba = (
        'WCS.ActivateWCS "global"\n'
        f'WCS.SetOrigin "{origin_values[0]}", "{origin_values[1]}", "{origin_values[2]}"\n'
        f'WCS.SetUVector "{u_values[0]}", "{u_values[1]}", "{u_values[2]}"\n'
        f'WCS.SetNormal "{normal_values[0]}", "{normal_values[1]}", "{normal_values[2]}"\n'
        'WCS.ActivateWCS "local"'
    )
    return ("define local WCS", vba)


# ── 求解器与边界 ──

def set_frequency_range(fmin: str, fmax: str) -> Tuple[str, str]:
    """设置频率范围。返回 (label, vba_code)。
    参考 pcb_3d_simulation.py 第 98 行。"""
    _validate_number(fmin, "fmin", min_val=0)
    _validate_number(fmax, "fmax", min_val=0)
    if float(fmin) >= float(fmax):
        raise ValueError(f"fmin ({fmin}) 必须小于 fmax ({fmax})")
    return ("set frequency range", f'Solver.FrequencyRange "{fmin}", "{fmax}"')


def set_boundary(xmin: str = "expanded open", xmax: str = "expanded open",
                 ymin: str = "expanded open", ymax: str = "expanded open",
                 zmin: str = "expanded open", zmax: str = "expanded open") -> Tuple[str, str]:
    """设置边界条件。默认 'expanded open' 会自动在结构外围添加空气间距（对应 GUI 中的 'open (add space)'）。
    如果用 'open'，边界会紧贴结构表面，可能导致端口距离边界太近而报错。
    语法参考 pcb_3d_simulation.py 的 _boundary_condition。
    返回 (label, vba_code)。"""
    for param_name, val in [("xmin", xmin), ("xmax", xmax), ("ymin", ymin),
                            ("ymax", ymax), ("zmin", zmin), ("zmax", zmax)]:
        if val.lower() not in VALID_BOUNDARY_TYPES:
            raise ValueError(
                f"边界类型 '{val}' 不合法 ({param_name})，可选: {VALID_BOUNDARY_TYPES}"
            )
    # 注意：官方示例里 Boundary 没有 .Create 调用
    vba = (
        f'With Boundary\n'
        f'    .Xmin "{xmin}"\n'
        f'    .Xmax "{xmax}"\n'
        f'    .Ymin "{ymin}"\n'
        f'    .Ymax "{ymax}"\n'
        f'    .Zmin "{zmin}"\n'
        f'    .Zmax "{zmax}"\n'
        f'    .Xsymmetry "none"\n'
        f'    .Ysymmetry "none"\n'
        f'    .Zsymmetry "none"\n'
        f'    .ApplyInAllDirections "False"\n'
        f'End With'
    )
    return ("set boundary", vba)


def set_background(xmin: str = "0", xmax: str = "0",
                   ymin: str = "0", ymax: str = "0",
                   zmin: str = "0", zmax: str = "0") -> Tuple[str, str]:
    """设置背景（空气层）到结构的距离。
    当边界为 'open (add space)' 时通常不需要手动设置（自动添加 λ/4）。
    当边界为 'open' 时，需要通过此函数设置足够的空气间距，否则端口可能距离边界太近。
    语法参考 pcb_3d_simulation.py 第 96 行 _boundary_distance。
    返回 (label, vba_code)。"""
    vba = (
        f'With Background\n'
        f'    .ResetBackground\n'
        f'    .XminSpace "{xmin}"\n'
        f'    .XmaxSpace "{xmax}"\n'
        f'    .YminSpace "{ymin}"\n'
        f'    .YmaxSpace "{ymax}"\n'
        f'    .ZminSpace "{zmin}"\n'
        f'    .ZmaxSpace "{zmax}"\n'
        f'    .ApplyInAllDirections "False"\n'
        f'End With'
    )
    return ("set background", vba)


def change_solver_type(solver: str = "HF Time Domain") -> Tuple[str, str]:
    """切换求解器类型。
    参考 pcb_3d_simulation.py 第 97 行。返回 (label, vba_code)。"""
    if solver not in VALID_SOLVER_TYPES:
        raise ValueError(f"求解器类型 '{solver}' 不合法，可选: {VALID_SOLVER_TYPES}")
    return ("set solver type", f'ChangeSolverType "{solver}"')


# ── 端口 ──

def create_discrete_port(port_number: int,
                         p1_x: str, p1_y: str, p1_z: str,
                         p2_x: str, p2_y: str, p2_z: str,
                         impedance: str = "50",
                         port_type: str = "SParameter") -> Tuple[str, str]:
    """创建离散端口。
    参考 CST 2025 Online Help: DiscretePort Object。
    SetP1/SetP2 第一个参数是 picked 标志，设为 False 表示直接使用坐标而非 GUI 拾取点。
    坐标必须是数值字符串（如 "3.5", "-10"），不要使用未定义的 CST 参数变量名。
    P1 和 P2 不能相同（零长度端口无效）。
    返回 (label, vba_code)。"""
    _validate_number(port_number, "port_number", min_val=1)
    _validate_number(impedance, "impedance", min_val=0)

    # 校验坐标是数值而非未定义的变量名
    coords = {"p1_x": p1_x, "p1_y": p1_y, "p1_z": p1_z,
              "p2_x": p2_x, "p2_y": p2_y, "p2_z": p2_z}
    for cname, cval in coords.items():
        cval_stripped = str(cval).strip()
        if not cval_stripped:
            raise ValueError(f"端口坐标 {cname} 不能为空")
        # 尝试解析为数值；如果不是纯数值则检查是否为已定义参数或表达式
        try:
            float(cval_stripped)
        except ValueError:
            # 含有运算符的表达式（如 "1.6+0.035"、"substrate_h+copper_t"）是合法的 CST 表达式
            if any(op in cval_stripped for op in ("+", "-", "*", "/", "(", ")")):
                # 表达式中的标识符应为已定义参数
                identifiers = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', cval_stripped)
                undefined = [i for i in identifiers if i not in _created_parameters]
                if undefined:
                    raise ValueError(
                        f"端口坐标 {cname}='{cval}' 表达式中引用了未定义的参数: {undefined}。"
                        f"请先用 store_parameter 定义这些参数，或使用具体数值。"
                    )
            elif cval_stripped in _created_parameters:
                # 纯参数名引用（如 "feed_x"），已通过 store_parameter 定义
                pass
            else:
                raise ValueError(
                    f"端口坐标 {cname}='{cval}' 看起来是一个未定义的变量名，"
                    f"CST 会将其求值为 0。请先用 store_parameter 定义该参数，"
                    f"或使用具体数值（如 '1.6'）或算术表达式（如 '1.6+0.035'）。"
                    f"已定义的参数: {sorted(_created_parameters) if _created_parameters else '无'}"
                )

    # 校验非零长度端口
    try:
        p1 = (float(p1_x), float(p1_y), float(p1_z))
        p2 = (float(p2_x), float(p2_y), float(p2_z))
        if p1 == p2:
            raise ValueError(
                f"端口 P1{p1} 和 P2{p2} 坐标完全相同，这会创建零长度端口。"
                f"P1 通常在地面/基板底部，P2 在贴片/导体顶部，两者的 Z 坐标应不同。"
            )
    except (TypeError, ValueError) as e:
        if "零长度" in str(e) or "完全相同" in str(e):
            raise
        # 表达式坐标无法解析为 float，跳过零长度检查

    pn = str(int(port_number))
    vba = (
        f'With DiscretePort\n'
        f'    .Reset\n'
        f'    .PortNumber "{pn}"\n'
        f'    .Type "{port_type}"\n'
        f'    .Impedance "{impedance}"\n'
        f'    .SetP1 "False", "{p1_x}", "{p1_y}", "{p1_z}"\n'
        f'    .SetP2 "False", "{p2_x}", "{p2_y}", "{p2_z}"\n'
        f'    .InvertDirection "False"\n'
        f'    .LocalCoordinates "False"\n'
        f'    .Monitor "True"\n'
        f'    .Radius "0.0"\n'
        f'    .Create\n'
        f'End With'
    )
    return (f"define port: {pn}", vba)


def _waveguide_axis_ranges(values, name: str) -> Tuple[str, str]:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError(f"{name} 必须是长度为 2 的数组")
    return (
        _validate_vba_string(values[0], f"{name}[0]"),
        _validate_vba_string(values[1], f"{name}[1]"),
    )


def create_waveguide_port(
    port_number: int,
    coordinate_mode: str,
    orientation: str,
    number_of_modes: int = 1,
    ranges: Dict = None,
    pick: Dict = None,
    range_add: Dict = None,
    port_on_bound: bool = True,
    clip_picked_port_to_bound: bool = False,
    reference_plane_distance: float = 0.0,
) -> Tuple[str, str]:
    """Create a CST waveguide port using official Free/Full/Picks semantics."""
    pn = int(_validate_number(port_number, "port_number", min_val=1))
    modes = int(_validate_number(number_of_modes, "number_of_modes", min_val=1, max_val=50))
    mode = str(coordinate_mode or "").strip()
    if mode not in {"Free", "Full", "Picks"}:
        raise ValueError("coordinate_mode 必须是 Free、Full 或 Picks")
    boundary_orientations = {"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
    picked_orientations = {"Positive", "Negative"}
    if mode == "Picks" and orientation not in picked_orientations:
        raise ValueError("Picks 模式 orientation 必须是 Positive 或 Negative")
    if mode != "Picks" and orientation not in boundary_orientations:
        raise ValueError("Free/Full 模式 orientation 必须是 xmin/xmax/ymin/ymax/zmin/zmax")

    preamble: List[str] = []
    range_lines: List[str] = []
    if mode == "Free":
        if pick is not None or range_add is not None:
            raise ValueError("Free 模式不接受 pick 或 range_add")
        if not isinstance(ranges, dict) or set(ranges) != {"x", "y", "z"}:
            raise ValueError("Free 模式 ranges 必须且只能包含 x、y、z")
        for axis in ("x", "y", "z"):
            low, high = _waveguide_axis_ranges(ranges[axis], f"ranges.{axis}")
            range_lines.append(f'    .{axis.upper()}range "{low}", "{high}"')
    elif mode == "Full":
        if ranges is not None or pick is not None or range_add is not None:
            raise ValueError("Full 模式不接受 ranges、pick 或 range_add")
    else:
        if ranges is not None:
            raise ValueError("Picks 模式不接受 ranges")
        if not isinstance(pick, dict) or set(pick) != {"solid", "face_id"}:
            raise ValueError("Picks 模式 pick 必须且只能包含 solid、face_id")
        solid = _validate_vba_string(pick["solid"], "pick.solid")
        if solid.count(":") != 1:
            raise ValueError("pick.solid 格式应为 Component:Name")
        face_id = int(_validate_number(pick["face_id"], "pick.face_id", min_val=1))
        preamble.extend(["Pick.ClearAllPicks", f'Pick.PickFaceFromId "{solid}", "{face_id}"'])
        additions = (
            {"x": [0, 0], "y": [0, 0], "z": [0, 0]}
            if range_add is None
            else range_add
        )
        if not isinstance(additions, dict) or set(additions) != {"x", "y", "z"}:
            raise ValueError("Picks 模式 range_add 必须且只能包含 x、y、z")
        for axis in ("x", "y", "z"):
            low, high = _waveguide_axis_ranges(additions[axis], f"range_add.{axis}")
            range_lines.append(f'    .{axis.upper()}rangeAdd "{low}", "{high}"')

    reference = _validate_number(reference_plane_distance, "reference_plane_distance")
    on_bound = "True" if port_on_bound else "False"
    clip = "True" if clip_picked_port_to_bound else "False"
    port_lines = [
        "With Port",
        "    .Reset",
        f'    .PortNumber "{pn}"',
        f'    .NumberOfModes "{modes}"',
        '    .AdjustPolarization "False"',
        '    .PolarizationAngle "0.0"',
        f'    .ReferencePlaneDistance "{reference}"',
        '    .TextSize "50"',
        f'    .Coordinates "{mode}"',
        f'    .Orientation "{orientation}"',
        f'    .PortOnBound "{on_bound}"',
        f'    .ClipPickedPortToBound "{clip}"',
        *range_lines,
        '    .SingleEnded "False"',
        "    .Create",
        "End With",
    ]
    return (f"define waveguide port: {pn}", "\n".join([*preamble, *port_lines]))


def create_frequency_field_monitor(name: str, frequency: float, field_type: str) -> Tuple[str, str]:
    """Create an official CST frequency-domain E/H/farfield monitor."""
    monitor_name = _validate_vba_string(name, "name")
    freq = _validate_number(frequency, "frequency", min_val=0)
    if float(freq) <= 0:
        raise ValueError("frequency 必须 > 0")
    normalized_type = str(field_type or "").strip()
    if normalized_type not in {"Efield", "Hfield", "Farfield"}:
        raise ValueError("field_type 必须是 Efield、Hfield 或 Farfield")
    dimension_line = '    .Dimension "Volume"\n' if normalized_type in {"Efield", "Hfield"} else ""
    vba = (
        "With Monitor\n"
        "    .Reset\n"
        f'    .Name "{monitor_name}"\n'
        f"{dimension_line}"
        '    .Domain "Frequency"\n'
        f'    .FieldType "{normalized_type}"\n'
        f'    .Frequency "{freq}"\n'
        "    .Create\n"
        "End With"
    )
    return (f"create {normalized_type} monitor: {monitor_name}", vba)


def create_farfield_monitor(name: str, frequency: str, use_subvolume: bool = False) -> Tuple[str, str]:
    """创建 Farfield monitor。必须在求解前创建，否则结果树中通常不会生成可读取的远场结果。"""
    monitor_name = _validate_vba_string(name, "name")
    _, official_vba = create_frequency_field_monitor(monitor_name, frequency, "Farfield")
    subvolume_flag = "True" if use_subvolume else "False"
    vba = official_vba.replace('    .Create\n', f'    .UseSubvolume "{subvolume_flag}"\n    .Create\n')
    return (f"create farfield monitor: {monitor_name}", vba)


# ── 布尔操作 ──

def boolean_subtract(obj1: str, obj2: str) -> Tuple[str, str]:
    """布尔减法。obj1 和 obj2 格式为 "Component:Name"。返回 (label, vba_code)。"""
    if ":" not in obj1 or ":" not in obj2:
        raise ValueError("对象名格式应为 'Component:Name'")
    return ("boolean subtract", f'Solid.Subtract "{obj1}", "{obj2}"')


def boolean_add(obj1: str, obj2: str) -> Tuple[str, str]:
    """布尔并集。obj1 和 obj2 格式为 "Component:Name"。返回 (label, vba_code)。"""
    if ":" not in obj1 or ":" not in obj2:
        raise ValueError("对象名格式应为 'Component:Name'")
    return ("boolean add", f'Solid.Add "{obj1}", "{obj2}"')


# ── 网格细化（Mesh refinement）──
# CST 官方做法：先 Group.Add "<name>", "mesh"，然后用 MeshSettings.ItemMeshSettings
# 设网格步长，再 Group.AddItem 把固体加入。参考 Library/Macros/Solver/Mesh/
# Apply Mesh Refinement To Dummy Object^-DS.mcr line 63-75。

def create_mesh_refinement(name: str, step_mm: float) -> Tuple[str, str]:
    """创建命名 mesh group 并设置步长。固体通过 add_solid_to_mesh_group 后加。

    name: mesh group 名称（CST 内不能含冒号）
    step_mm: 该 group 的最大单元尺寸（毫米）。Hex 三向同步，Tet 用同值
    """
    name = _validate_vba_string(name, "name")
    if ":" in name:
        raise ValueError("mesh group name 不能为空且不能含冒号")
    if not (step_mm > 0):
        raise ValueError(f"step_mm 必须 > 0，当前 {step_mm}")
    step_str = f"{step_mm:.4f}".rstrip("0").rstrip(".") or "0"
    vba = (
        f'Group.Add "{name}", "mesh"\n\n'
        f'With MeshSettings\n'
        f'  With .ItemMeshSettings("group${name}")\n'
        f'    .SetMeshType "Hex"\n'
        f'    .Set "Step", "{step_str}", "{step_str}", "{step_str}"\n'
        f'    .SetMeshType "Tet"\n'
        f'    .Set "Size", "{step_str}"\n'
        f'  End With\n'
        f'End With'
    )
    return (f"create mesh refinement: {name} step={step_str}mm", vba)


def add_solid_to_mesh_group(solid: str, group_name: str) -> Tuple[str, str]:
    """把固体（"Component:Name" 形式）加入已有 mesh group。"""
    solid = _validate_vba_string(solid, "solid")
    group_name = _validate_vba_string(group_name, "group_name")
    if solid.count(":") != 1 or not all(part.strip() for part in solid.split(":", 1)):
        raise ValueError("solid 格式应为 'Component:Name'")
    return (
        f"add to mesh group: {solid} -> {group_name}",
        f'Group.AddItem "solid${solid}", "{group_name}"',
    )


def add_solids_to_mesh_group(solids: List[Dict[str, str]], group_name: str) -> Tuple[str, str]:
    """Add structured solid references to one mesh group in deterministic order."""
    group = _validate_vba_string(group_name, "group_name")
    if not isinstance(solids, list) or not solids:
        raise ValueError("solids 必须是非空数组")
    lines = []
    normalized = []
    for index, solid in enumerate(solids):
        if not isinstance(solid, dict):
            raise ValueError(f"solids[{index}] 必须是对象")
        component = _validate_vba_string(solid.get("component"), f"solids[{index}].component")
        name = _validate_vba_string(solid.get("name"), f"solids[{index}].name")
        reference = f"{component}:{name}"
        _, line = add_solid_to_mesh_group(reference, group)
        lines.append(line)
        normalized.append(reference)
    return (
        f"add {len(normalized)} solids to mesh group: {group}",
        "\n".join(lines),
    )


# ── 求解器控制 ──

def run_solver() -> Tuple[str, str]:
    """启动当前配置的求解器。
    实际执行通过 cst.interface 原生 API (model3d.run_solver())，
    不走 VBA add_to_history，自带参数化更新（F7 Rebuild）。
    返回 (label, vba_code)，其中 vba_code 仅用于离线模式展示。"""
    return ("run solver", "Solver.Start")


# ── 原语注册表，供 Agent 查询 ──

PRIMITIVES = {
    "set_units": set_units,
    "store_parameter": store_parameter,
    "delete_parameter": delete_parameter,
    "create_material": create_material,
    "create_brick": create_brick,
    "create_cylinder": create_cylinder,
    "create_extruded_polygon": create_extruded_polygon,
    "transform_shape": transform_shape,
    "set_wcs": set_wcs,
    "set_frequency_range": set_frequency_range,
    "set_boundary": set_boundary,
    "set_background": set_background,
    "change_solver_type": change_solver_type,
    "create_discrete_port": create_discrete_port,
    "create_waveguide_port": create_waveguide_port,
    "create_farfield_monitor": create_farfield_monitor,
    "create_frequency_field_monitor": create_frequency_field_monitor,
    "boolean_add": boolean_add,
    "boolean_subtract": boolean_subtract,
    "create_mesh_refinement": create_mesh_refinement,
    "add_solid_to_mesh_group": add_solid_to_mesh_group,
    "add_solids_to_mesh_group": add_solids_to_mesh_group,
    "run_solver": run_solver,
}
