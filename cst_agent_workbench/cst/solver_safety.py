from __future__ import annotations

from typing import Any

from cst_agent_workbench.cst.primitives import get_farfield_monitors, get_frequency_range, get_model_summary

_SOLVE_KEYWORDS = (
    "求解",
    "仿真",
    "运行仿真",
    "开始仿真",
    "simulate",
    "simulation",
    "solve",
    "run solver",
    "run simulation",
)


def solver_explicitly_requested(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in _SOLVE_KEYWORDS)


def _numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _check_frequency_range() -> tuple[bool, str]:
    freq_range = get_frequency_range()
    fmin = _numeric(freq_range.get("fmin"))
    fmax = _numeric(freq_range.get("fmax"))
    if fmin is None or fmax is None:
        return False, "未设置有效频率范围"
    if fmin >= fmax:
        return False, "频率范围必须满足 fmin < fmax"
    return True, ""


def validate_solver_ready(
    cst,
    *,
    source: str,
    require_connected: bool = True,
    require_project: bool = True,
    require_objects: bool = True,
    require_ports: bool = True,
    require_frequency_range: bool = True,
    require_farfield_monitor: bool = True,
) -> dict:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    if require_connected:
        offline_mode = bool(getattr(cst, "offline_mode", False))
        try:
            connected = bool(cst.is_connected())
        except Exception:
            connected = False
        checks["connected"] = connected and not offline_mode
        if not checks["connected"]:
            failures.append("CST 未连接或处于离线模式")

    if require_project:
        project_path = str(getattr(cst, "project_path", "") or "")
        checks["project"] = bool(project_path)
        if not checks["project"]:
            failures.append("当前没有 CST 工程路径")

    model_summary = get_model_summary()
    if require_objects:
        objects = model_summary.get("objects") or []
        checks["objects"] = bool(objects)
        if not checks["objects"]:
            failures.append("未创建几何对象")

    if require_ports:
        ports = model_summary.get("ports") or []
        checks["ports"] = bool(ports)
        if not checks["ports"]:
            failures.append("未创建端口")

    if require_frequency_range:
        frequency_ok, frequency_message = _check_frequency_range()
        checks["frequency_range"] = frequency_ok
        if not frequency_ok:
            failures.append(frequency_message)

    if require_farfield_monitor:
        monitors = get_farfield_monitors()
        checks["farfield_monitor"] = bool(monitors)
        if not checks["farfield_monitor"]:
            failures.append("未创建 farfield monitor")

    success = not failures
    return {
        "success": success,
        "message": "求解前检查通过" if success else "求解前检查失败：" + "；".join(failures),
        "source": source,
        "checks": checks,
    }
