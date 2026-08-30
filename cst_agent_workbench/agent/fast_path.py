"""Fast-path antenna modeling methods extracted from agent.py.

These methods bypass the LLM tool-calling loop for deterministic antenna synthesis
(rectangular patch, dipole, pixel patch).
"""

import os
import shutil
import uuid
from dataclasses import replace
from typing import Any, Dict, List, Optional

from cst_agent_workbench import config
from cst_agent_workbench.agent.helpers import _format_mm, coerce_success
from cst_agent_workbench.cst.primitives import (
    PRIMITIVES, reset_created_objects, register_object, unregister_object,
    register_material, register_parameter, register_port,
    register_frequency_range, register_farfield_monitor,
    create_farfield_monitor,
)
from cst_agent_workbench.cst.rectangular_patch_fast import (
    RectangularPatchRequest, build_side_waveguide_port_vba,
    make_default_rectangular_patch_request, resolve_rectangular_patch_request, synthesize_rectangular_patch,
)
from cst_agent_workbench.cst.patch_fast_executor import PatchFastExecutorMixin
from cst_agent_workbench.cst.solver_safety import solver_explicitly_requested, validate_solver_ready
from cst_agent_workbench.results.summary import summarize_s11_result


def _coerce_or(value: Any, fallback: Any) -> Any:
    """Return fallback when value is None; LLM tool-call payloads sometimes use explicit nulls."""
    return fallback if value is None else value


class FastPathMixin(PatchFastExecutorMixin):
    """Mixin providing fast-path antenna modeling methods."""

    def _join_vba(self, snippets: List[str]) -> str:
        return "\n\n".join(snippet.strip() for snippet in snippets if snippet and snippet.strip())

    def _fast_path_project_dir(self) -> str:
        return os.path.abspath(config.CST_FAST_PATH_DIR)

    def _remove_fast_path_project_artifacts(self, project_path: str) -> None:
        if not project_path:
            return
        stem, ext = os.path.splitext(project_path)
        if ext.lower() == ".cst" and os.path.exists(project_path):
            try:
                os.remove(project_path)
            except OSError:
                pass
        if os.path.isdir(stem):
            try:
                shutil.rmtree(stem)
            except OSError:
                pass

    def _cleanup_old_fast_path_projects(self, keep_latest: Optional[int] = None) -> None:
        keep_latest = config.FAST_PATH_KEEP_LATEST if keep_latest is None else keep_latest
        keep_latest = max(1, int(keep_latest))
        project_dir = self._fast_path_project_dir()
        if not os.path.isdir(project_dir):
            return

        candidates = []
        for name in os.listdir(project_dir):
            if not name.startswith("fast_patch_") or not name.endswith(".cst"):
                continue
            project_path = os.path.join(project_dir, name)
            try:
                mtime = os.path.getmtime(project_path)
            except OSError:
                continue
            candidates.append((mtime, project_path))

        candidates.sort(reverse=True)
        for _, old_project_path in candidates[keep_latest:]:
            self.cst.close_project(old_project_path, timeout=20)
            self._remove_fast_path_project_artifacts(old_project_path)

    def _collect_s11_summary(self, target_freq_ghz: float) -> Dict:
        if not self.cst.project_path:
            return {"success": False, "message": "当前没有可读取结果的 CST 工程路径"}

        open_result = self.results.open(self.cst.project_path)
        if not coerce_success(open_result.get("success")):
            return open_result

        s11_result = self.results.get_s_parameter(1, 1)
        if not coerce_success(s11_result.get("success")):
            return s11_result

        summary = summarize_s11_result(s11_result, target_freq_ghz)
        if not summary.success:
            empty_result = summary.to_dict()
            empty_result["message"] = str(s11_result.get("message") or "S11 结果为空")
            return empty_result

        return summary.to_dict()

    def _build_fast_path_response(
        self,
        req: RectangularPatchRequest,
        dims: Dict[str, float],
        component_name: str,
        solver_result: Dict,
        s11_summary: Dict,
    ) -> str:
        def fmt(value: Any) -> str:
            return self._format_mm(float(value)) if value is not None else "-"

        feed_strategy = (req.feed_strategy or "microstrip").strip().lower()
        lines = [
            "已使用矩形贴片快速路径直接完成建模，绕过了通用大模型建模链路。",
            f"- 组件: `{component_name}`",
            f"- 基板: `{req.substrate_name}` (Dk={req.epsilon_r}, Df={req.loss_tangent}, h={self._format_mm(req.substrate_thickness_mm)} mm)",
            f"- 导体: `{req.conductor_name}`，厚度 {self._format_mm(req.conductor_thickness_mm)} mm",
            f"- Patch: W={self._format_mm(dims['patch_w'])} mm, L={self._format_mm(dims['patch_l'])} mm",
        ]
        if feed_strategy == "probe":
            lines.extend([
                f"- Probe feed: y={fmt(dims.get('probe_y_offset'))} mm, radius={fmt(dims.get('probe_radius_mm'))} mm",
                f"- Ground clearance hole: radius={fmt(dims.get('ground_hole_radius_mm'))} mm",
                "- Feed port: discrete 50Ω probe port",
            ])
        else:
            lines.extend([
                f"- Feed line: W={self._format_mm(dims['feed_w'])} mm, launch={self._format_mm(dims['feed_l'])} mm",
                f"- Inset feed: depth={self._format_mm(dims['inset_depth'])} mm, gap={self._format_mm(dims['inset_gap'])} mm",
                "- Feed port: side waveguide port",
            ])
        lines.extend([
            f"- Substrate/Ground: {self._format_mm(dims['sub_w'])} mm × {self._format_mm(dims['sub_l'])} mm",
            f"- 频率范围: {self._format_mm(dims['fmin'])} 到 {self._format_mm(dims['fmax'])} GHz",
        ])
        if solver_result.get("skipped"):
            lines.append(f"- 求解: 已跳过，{solver_result.get('message', '')}")
        elif coerce_success(solver_result.get("success")):
            lines.append("- 求解: 已完成一次求解")
        else:
            lines.append(f"- 求解: 失败，{solver_result.get('message', '')}")

        if coerce_success(s11_summary.get("success")) and s11_summary.get("plot_data"):
            lines.append(f"- S11最小值: {s11_summary['min_s11_db']:.2f} dB @ {s11_summary['min_freq_ghz']:.3f} GHz")
            if s11_summary.get("target_s11_db") is not None and s11_summary.get("target_freq_ghz") is not None:
                lines.append(f"- 在 {s11_summary['target_freq_ghz']:.3f} GHz 处: {s11_summary['target_s11_db']:.2f} dB")
            if s11_summary.get("bandwidth_ghz") is not None and s11_summary.get("bandwidth_pct") is not None:
                lines.append(f"- BW@-10 dB: {s11_summary['bandwidth_ghz']:.4f} GHz ({s11_summary['bandwidth_pct']:.2f}%)")
        elif s11_summary.get("message"):
            lines.append(f"- 结果读取: {s11_summary['message']}")

        lines.append("这条路径只写入数值参数和数值几何，不再先走符号表达式建模再失败重试。")

        # LLM 物理分析
        if coerce_success(s11_summary.get("success")) and self.client is not None:
            try:
                from cst_agent_workbench.agent.analyzer import analyze_s11_with_llm
                from cst_agent_workbench.cst.primitives import get_parameters
                summary_dict = {
                    "min_s11_db": s11_summary.get("min_s11_db"),
                    "min_freq_ghz": s11_summary.get("min_freq_ghz"),
                    "bandwidth_ghz": s11_summary.get("bandwidth_ghz"),
                    "at_f0_s11_db": s11_summary.get("target_s11_db"),
                }
                llm_analysis, usage = analyze_s11_with_llm(
                    self.client, self.model, summary_dict, req.f0_ghz, get_parameters(), timeout=20
                )
                if usage:
                    self.token_stats["prompt"] += usage.get("prompt", 0)
                    self.token_stats["completion"] += usage.get("completion", 0)
                    if usage.get("cached", 0):
                        self.token_stats["cached"] = self.token_stats.get("cached", 0) + usage["cached"]
                    if usage.get("cache_write", 0):
                        self.token_stats["cache_write"] = self.token_stats.get("cache_write", 0) + usage["cache_write"]
                    self.token_stats["calls"] += 1
                if llm_analysis:
                    lines.append(f"\n**AI 分析：**\n{llm_analysis}")
                else:
                    lines.append("\n**AI 分析：** 分析返回空")
            except Exception as _e:
                lines.append(f"\n**AI 分析：** 异常 {_e}")
        elif self.client is None:
            lines.append("\n**AI 分析：** client 为 None，未连接 LLM")

        return "\n".join(lines)

    def _build_last_patch_request_context(self) -> str:
        req = self.last_patch_request
        if req is None:
            return ""
        return (
            "[最近一次标准矩形贴片请求]\n"
            f"- f0 = {req.f0_ghz:.4f} GHz\n"
            f"- substrate = {req.substrate_name}\n"
            f"- Dk = {req.epsilon_r}\n"
            f"- Df = {req.loss_tangent}\n"
            f"- h = {req.substrate_thickness_mm:.4f} mm\n"
            f"- conductor = {req.conductor_name}\n"
            f"- conductor_thickness = {req.conductor_thickness_mm:.4f} mm\n"
            f"- feed_strategy = {req.feed_strategy}\n"
            "如果用户说“其他都一样/沿用上一版/只改频率”，优先调用 build_rectangular_patch_fast 并继承这组参数。"
        )

    def _resolve_rectangular_patch_request_from_tool_arguments(self, arguments: dict) -> RectangularPatchRequest:
        requested_feed_strategy = arguments.get("feed_strategy")
        if isinstance(requested_feed_strategy, str) and not requested_feed_strategy.strip():
            requested_feed_strategy = None
        inherit_previous = bool(arguments.get("inherit_previous_request"))
        previous_request = self.last_patch_request if inherit_previous else None
        if requested_feed_strategy is None and previous_request is not None:
            feed_strategy = str(previous_request.feed_strategy or self._patch_feed_strategy or "microstrip").strip().lower()
        else:
            feed_strategy = str(requested_feed_strategy or self._patch_feed_strategy or "microstrip").strip().lower()
        if feed_strategy not in {"microstrip", "probe"}:
            raise ValueError("build_rectangular_patch_fast 当前仅支持 microstrip/probe feed")

        if previous_request is not None:
            request = replace(previous_request, feed_strategy=feed_strategy)
        else:
            request = None

        if request is None:
            required_fields = ["f0_ghz"]
            missing = [name for name in required_fields if arguments.get(name) is None]
            if missing:
                raise ValueError(
                    "缺少完整矩形贴片 fast path 所需参数，且未指定 inherit_previous_request: "
                    + ", ".join(missing)
                )
            request = make_default_rectangular_patch_request(float(arguments["f0_ghz"]), feed_strategy)
        else:
            arg_f0 = arguments.get("f0_ghz")
            if arg_f0 is not None:
                request = replace(request, f0_ghz=float(arg_f0))

        # Uniform per-field overrides; tolerates `None` in tool arguments (LLM may emit nulls).
        request = replace(
            request,
            substrate_name=str(arguments.get("substrate_name") or request.substrate_name),
            epsilon_r=float(_coerce_or(arguments.get("epsilon_r"), request.epsilon_r)),
            loss_tangent=float(_coerce_or(arguments.get("loss_tangent"), request.loss_tangent)),
            substrate_thickness_mm=float(_coerce_or(arguments.get("substrate_thickness_mm"), request.substrate_thickness_mm)),
            conductor_name=str(arguments.get("conductor_name") or request.conductor_name),
            conductor_thickness_mm=float(_coerce_or(arguments.get("conductor_thickness_mm"), request.conductor_thickness_mm)),
            feed_strategy=feed_strategy,
        )
        return request

    def _run_rectangular_patch_fast_path_from_request(
        self,
        request: RectangularPatchRequest,
        execution_mode: str = "fast_path",
        allow_solver: bool = False,
    ) -> str:
        feed_strategy = (request.feed_strategy or "microstrip").strip().lower()
        if self._optimization_mode or feed_strategy not in {"microstrip", "probe"}:
            self.last_chat_status = {
                "ok": False,
                "error": "当前 fast path 仅支持非优化模式下的 microstrip/probe 矩形贴片。",
                "had_tool_failure": True,
                "mode": execution_mode,
            }
            return self.last_chat_status["error"]
        if feed_strategy == "probe":
            return PatchFastExecutorMixin._run_rectangular_patch_fast_path_from_request(
                self,
                request,
                execution_mode=execution_mode,
                allow_solver=allow_solver,
            )
        dims = synthesize_rectangular_patch(request)
        reset_created_objects()
        self._fast_path_counter += 1
        suffix = f"{self._fast_path_counter}_{uuid.uuid4().hex[:6]}"
        component_name = f"AntennaFP{suffix}"
        feed_component = f"FeedFP{suffix}"
        fast_project_dir = self._fast_path_project_dir()
        os.makedirs(fast_project_dir, exist_ok=True)
        fast_project_path = os.path.join(fast_project_dir, f"fast_patch_{suffix}.cst")

        new_project_result = self.cst.new_project(fast_project_path, timeout=120)
        self._record_fast_path_event(
            "0_新建工程",
            "new_project",
            coerce_success(new_project_result.get("success")),
            new_project_result.get("message", ""),
            "为 fast path 创建独立 CST 工程，避免重复运行时旧几何叠加",
        )
        if not coerce_success(new_project_result.get("success")):
            self.last_chat_status = {
                "ok": False,
                "error": new_project_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在新建工程阶段失败: {new_project_result.get('message', '')}"
        self._cleanup_old_fast_path_projects()

        patch_xmax = dims["patch_w"] / 2
        patch_ymin = -dims["patch_l"] / 2
        patch_ymax = dims["patch_l"] / 2
        feed_ymin = patch_ymin - dims["feed_l"]
        ground_ymax = patch_ymax + dims["rear_margin"]

        patch_xmin_expr = "-patch_W/2"
        patch_xmax_expr = "patch_W/2"
        patch_ymin_expr = "-patch_L/2"
        patch_ymax_expr = "patch_L/2"
        feed_xmin_expr = "-feed_W/2"
        feed_xmax_expr = "feed_W/2"
        feed_ymin_expr = "-patch_L/2-feed_L"
        feed_ymax_expr = "-patch_L/2+inset_depth"
        notch_xmin_expr = "-notch_W/2"
        notch_xmax_expr = "notch_W/2"
        notch_ymin_expr = "-patch_L/2"
        notch_ymax_expr = "-patch_L/2+inset_depth"
        ground_xmin_expr = "-ground_W/2"
        ground_xmax_expr = "ground_W/2"
        ground_ymin_expr = "-patch_L/2-feed_L"
        ground_ymax_expr = "patch_L/2+rear_margin"
        port_y_expr = "-patch_L/2-feed_L"

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
            _, param_vba = PRIMITIVES["store_parameter"](name, _format_mm(value))
            setup_snippets.append(param_vba)
        _, material_vba = PRIMITIVES["create_material"](
            request.substrate_name,
            epsilon=request.epsilon_r,
            tand=request.loss_tangent,
            tand_freq=request.f0_ghz,
        )
        setup_snippets.append(material_vba)
        _, freq_vba = PRIMITIVES["set_frequency_range"](_format_mm(dims["fmin"]), _format_mm(dims["fmax"]))
        setup_snippets.append(freq_vba)
        _, boundary_vba = PRIMITIVES["set_boundary"](
            xmin="expanded open",
            xmax="expanded open",
            ymin="open",
            ymax="expanded open",
            zmin="expanded open",
            zmax="expanded open",
        )
        setup_snippets.append(boundary_vba)

        setup_batch_vba = self._join_vba(setup_snippets)
        setup_result = self.cst.execute_vba(
            setup_batch_vba,
            label=f"fast_patch_setup_{suffix}",
            timeout=120,
        )
        self.last_vba = setup_batch_vba
        self._record_fast_path_event(
            "1_环境与材料",
            "fast_patch_setup",
            coerce_success(setup_result.get("success")),
            setup_result.get("message", ""),
            "矩形贴片快路径: 参数、材料、频率和边界一次性下发",
        )
        if not coerce_success(setup_result.get("success")):
            self.last_chat_status = {
                "ok": False,
                "error": setup_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在初始化阶段失败: {setup_result.get('message', '')}"

        if setup_result.get("executed", False):
            for name, value in parameter_values.items():
                register_parameter(name, _format_mm(value))
            register_material(request.substrate_name)
            register_frequency_range(_format_mm(dims["fmin"]), _format_mm(dims["fmax"]))

        geometry_snippets = []
        for primitive_name, arguments in [
            ("create_brick", {
                "name": "Ground",
                "component": component_name,
                "material": request.conductor_name,
                "xmin": ground_xmin_expr,
                "xmax": ground_xmax_expr,
                "ymin": ground_ymin_expr,
                "ymax": ground_ymax_expr,
                "zmin": "-copper_t",
                "zmax": "0",
            }),
            ("create_brick", {
                "name": "Substrate",
                "component": component_name,
                "material": request.substrate_name,
                "xmin": ground_xmin_expr,
                "xmax": ground_xmax_expr,
                "ymin": ground_ymin_expr,
                "ymax": ground_ymax_expr,
                "zmin": "0",
                "zmax": "substrate_h",
            }),
            ("create_brick", {
                "name": "Patch",
                "component": component_name,
                "material": request.conductor_name,
                "xmin": patch_xmin_expr,
                "xmax": patch_xmax_expr,
                "ymin": patch_ymin_expr,
                "ymax": patch_ymax_expr,
                "zmin": "substrate_h",
                "zmax": "substrate_h+copper_t",
            }),
            ("create_brick", {
                "name": "InsetGap",
                "component": feed_component,
                "material": "Vacuum",
                "xmin": notch_xmin_expr,
                "xmax": notch_xmax_expr,
                "ymin": notch_ymin_expr,
                "ymax": notch_ymax_expr,
                "zmin": "substrate_h",
                "zmax": "substrate_h+copper_t",
            }),
            ("create_brick", {
                "name": "FeedLine",
                "component": feed_component,
                "material": request.conductor_name,
                "xmin": feed_xmin_expr,
                "xmax": feed_xmax_expr,
                "ymin": feed_ymin_expr,
                "ymax": feed_ymax_expr,
                "zmin": "substrate_h",
                "zmax": "substrate_h+copper_t",
            }),
        ]:
            _, snippet_vba = PRIMITIVES[primitive_name](**arguments)
            geometry_snippets.append(snippet_vba)
        _, subtract_vba = PRIMITIVES["boolean_subtract"](f"{component_name}:Patch", f"{feed_component}:InsetGap")
        geometry_snippets.append(subtract_vba)
        _, add_vba = PRIMITIVES["boolean_add"](f"{component_name}:Patch", f"{feed_component}:FeedLine")
        geometry_snippets.append(add_vba)

        geometry_batch_vba = self._join_vba(geometry_snippets)
        geometry_result = self.cst.execute_vba(
            geometry_batch_vba,
            label=f"fast_patch_geometry_{suffix}",
            timeout=120,
        )
        self.last_vba = geometry_batch_vba
        self._record_fast_path_event(
            "2_几何建模",
            "fast_patch_geometry",
            coerce_success(geometry_result.get("success")),
            geometry_result.get("message", ""),
            "矩形贴片快路径: 数值几何一次性下发",
        )
        if not coerce_success(geometry_result.get("success")):
            self.last_chat_status = {
                "ok": False,
                "error": geometry_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在几何阶段失败: {geometry_result.get('message', '')}"

        if geometry_result.get("executed", False):
            register_object(component_name, "Ground", request.conductor_name)
            register_object(component_name, "Substrate", request.substrate_name)
            register_object(component_name, "Patch", request.conductor_name)
            unregister_object(feed_component, "FeedLine")
            unregister_object(feed_component, "InsetGap")

        port_vba = build_side_waveguide_port_vba(
            port_number=1,
            pick_solid=f"{component_name}:Patch",
            pick_x="0",
            pick_y=_format_mm(feed_ymin),
            pick_z=_format_mm(request.substrate_thickness_mm + request.conductor_thickness_mm / 2),
            y_pos=port_y_expr,
            feed_width="feed_W",
            substrate_height="substrate_h",
            copper_thickness="copper_t",
        )
        port_result = self.cst.execute_vba(
            port_vba,
            label=f"fast_patch_port_{suffix}",
            timeout=120,
        )
        self.last_vba = port_vba
        self._record_fast_path_event(
            "3_边界与端口",
            "fast_patch_port",
            coerce_success(port_result.get("success")),
            port_result.get("message", ""),
            "矩形贴片快路径: 侧边 waveguide port",
        )
        if not coerce_success(port_result.get("success")):
            self.last_chat_status = {
                "ok": False,
                "error": port_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在端口阶段失败: {port_result.get('message', '')}"

        if port_result.get("executed", False):
            register_port(1, "50")

        farfield_name = f"farfield (f={_format_mm(request.f0_ghz)})"
        farfield_frequency = _format_mm(request.f0_ghz)
        _, farfield_vba = create_farfield_monitor(
            name=farfield_name,
            frequency=farfield_frequency,
            use_subvolume=False,
        )
        farfield_result = self.cst.execute_vba(
            farfield_vba,
            label=f"fast_patch_farfield_monitor_{suffix}",
            timeout=60,
        )
        self.last_vba = farfield_vba
        self._record_fast_path_event(
            "3_边界与端口",
            "create_farfield_monitor",
            coerce_success(farfield_result.get("success")),
            farfield_result.get("message", ""),
            "矩形贴片快路径: 在求解前创建 farfield monitor",
        )
        if not coerce_success(farfield_result.get("success")):
            self.last_chat_status = {
                "ok": False,
                "error": farfield_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在远场监视器阶段失败: {farfield_result.get('message', '')}"

        if farfield_result.get("executed", False):
            register_farfield_monitor(farfield_name, farfield_frequency, False)

        if not allow_solver:
            solver_result = {
                "success": True,
                "skipped": True,
                "message": "未检测到明确求解/仿真请求，已按安全策略跳过 solver",
            }
            self._record_fast_path_event(
                "4_运行仿真",
                "run_solver_skipped",
                True,
                solver_result["message"],
                "矩形贴片快路径: 安全门跳过自动求解",
            )
            self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": execution_mode}
            self.last_patch_request = replace(request)
            return self._build_fast_path_response(request, dims, component_name, solver_result, {})

        preflight = validate_solver_ready(self.cst, source="fast_path.rectangular_patch")
        if not coerce_success(preflight.get("success")):
            self._record_fast_path_event(
                "4_运行仿真",
                "run_solver_preflight",
                False,
                preflight.get("message", ""),
                "矩形贴片快路径: 求解前检查失败",
            )
            self.last_chat_status = {
                "ok": False,
                "error": preflight.get("message", ""),
                "had_tool_failure": True,
                "mode": execution_mode,
            }
            return self._build_fast_path_response(request, dims, component_name, preflight, {})

        self._try_install_farfield_export_template(fast_project_path)

        solver_result = self.cst.run_solver(timeout=360)
        solver_ok = coerce_success(solver_result.get("success"))
        self._record_fast_path_event(
            "4_运行仿真",
            "run_solver",
            solver_ok,
            solver_result.get("message", ""),
            "矩形贴片快路径: 单次求解",
        )
        s11_summary = self._collect_s11_summary(request.f0_ghz) if solver_ok else {}
        s11_ok = coerce_success(s11_summary.get("success")) and bool(s11_summary.get("plot_data"))
        if s11_ok and s11_summary.get("raw"):
            self.last_results = s11_summary["raw"]

        final_text = self._build_fast_path_response(request, dims, component_name, solver_result, s11_summary)

        if solver_ok and fast_project_path:
            self._export_farfield_online_after_solver(
                fast_project_path,
                f"Farfields\\farfield (f={_format_mm(request.f0_ghz)})",
            )
        success = solver_ok and s11_ok
        if not solver_ok:
            error_message = str(solver_result.get("message") or "solver failed")
        elif not s11_ok:
            error_message = str(s11_summary.get("message") or "solver 成功但 S11 结果为空")
        else:
            error_message = ""
        self.last_chat_status = {
            "ok": success,
            "error": error_message,
            "had_tool_failure": not success,
            "mode": execution_mode,
        }
        if success:
            self.last_patch_request = replace(request)
        return final_text

    def _run_rectangular_patch_fast_path(self, user_message: str) -> Optional[str]:
        if self._optimization_mode:
            return None

        request = resolve_rectangular_patch_request(
            user_message,
            self._patch_feed_strategy,
            previous_request=self.last_patch_request,
        )
        if request is None:
            request = self._resolve_rectangular_patch_followup_from_history(user_message)
        if request is None:
            return None

        self.opt_state.target_freq = request.f0_ghz

        return self._run_rectangular_patch_fast_path_from_request(
            request,
            execution_mode="fast_path",
            allow_solver=solver_explicitly_requested(user_message),
        )

    def _resolve_rectangular_patch_followup_from_history(self, user_message: str) -> Optional[RectangularPatchRequest]:
        message_text = str(user_message or "")
        lowered = message_text.lower()
        followup_markers = [
            "已经打开", "打开了", "创建并求解", "创建并且求解", "直接创建",
            "继续创建", "继续构建", "继续做", "继续求解", "求解吧",
            "create it", "build it", "solve it", "go ahead",
        ]
        if not any(marker in message_text or marker in lowered for marker in followup_markers):
            return None
        for msg in reversed(self.history[-8:]):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            request = resolve_rectangular_patch_request(
                str(content or ""),
                self._patch_feed_strategy,
                previous_request=self.last_patch_request,
            )
            if request is not None:
                return request
        return None

    def _run_dipole_fast_path(self, user_message: str):
        """拦截半波振子自然语言请求，走确定性 fast path。"""
        if self._optimization_mode:
            return None
        from cst_agent_workbench.cst.dipole_fast import parse_dipole_request
        req = parse_dipole_request(user_message)
        if req is None:
            return None
        outcome = self._execute_dipole_request(
            req,
            allow_solver=solver_explicitly_requested(user_message),
            execution_mode="dipole_fast_path",
        )
        return outcome["message"]

    def _execute_dipole_request(
        self,
        req,
        *,
        allow_solver: bool,
        execution_mode: str,
        create_project: bool = False,
    ) -> Dict[str, Any]:
        """Build a deterministic dipole and optionally require solver + S11 readback."""
        from cst_agent_workbench.cst.dipole_fast import build_dipole_vba, synthesize_dipole

        dims = synthesize_dipole(req)
        self.opt_state.target_freq = req.f0_ghz
        project_result: Dict[str, Any] = {}
        if create_project:
            self._fast_path_counter += 1
            suffix = f"{self._fast_path_counter}_{uuid.uuid4().hex[:6]}"
            project_dir = self._fast_path_project_dir()
            os.makedirs(project_dir, exist_ok=True)
            project_path = os.path.join(project_dir, f"fast_dipole_{suffix}.cst")
            project_result = self.cst.new_project(project_path, timeout=120)
            if not coerce_success(project_result.get("success")):
                error_message = str(project_result.get("message") or "new project failed")
                self.last_chat_status = {"ok": False, "error": error_message, "had_tool_failure": True, "mode": execution_mode}
                return {
                    "success": False,
                    "message": f"振子新建工程失败：{error_message}",
                    "mode": execution_mode,
                    "dims": dims,
                    "project": project_result,
                    "build": {},
                    "preflight": {},
                    "solver": {},
                    "s11": {},
                }
        # Only discard the previous host-side model inventory once any requested
        # project transition has succeeded.  Otherwise a failed new-project call
        # would corrupt the preflight state for the still-open project.
        reset_created_objects()
        vba = build_dipole_vba(req, dims)
        build_result = self.cst.execute_vba(vba, label="build_dipole_fast", timeout=120)
        build_ok = coerce_success(build_result.get("success"))
        if build_ok and build_result.get("executed", False):
            register_object("dipole", "upper_arm", "Copper (annealed)")
            register_object("dipole", "lower_arm", "Copper (annealed)")
            register_port(1, "50")
            register_frequency_range(f"{dims['fmin']:.4f}", f"{dims['fmax']:.4f}")
            register_farfield_monitor(f"farfield (f={req.f0_ghz})", str(req.f0_ghz), False)
        message = (
            "半波振子天线已创建：\n"
            f"- 目标频率：{req.f0_ghz} GHz\n"
            f"- 臂长：{dims['arm_length']:.2f} mm（= 0.235λ₀）\n"
            f"- 间隙：{dims['gap']:.2f} mm\n"
            f"- 仿真频率范围：{dims['fmin']:.2f}\u2013{dims['fmax']:.2f} GHz"
        )
        if not build_ok:
            error_message = build_result.get("message", "")
            self.last_chat_status = {"ok": False, "error": error_message, "had_tool_failure": True, "mode": execution_mode}
            return {
                "success": False,
                "message": f"振子建模失败：{error_message}",
                "mode": execution_mode,
                "dims": dims,
                "project": project_result,
                "build": build_result,
                "preflight": {},
                "solver": {},
                "s11": {},
            }
        if not allow_solver:
            self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": execution_mode}
            return {
                "success": True,
                "message": message + "\n- 求解：已按安全策略跳过；如需运行仿真，请明确说“求解/仿真”。",
                "mode": execution_mode,
                "dims": dims,
                "project": project_result,
                "build": build_result,
                "preflight": {},
                "solver": {"success": True, "skipped": True},
                "s11": {},
            }
        preflight = validate_solver_ready(self.cst, source="fast_path.dipole")
        if not coerce_success(preflight.get("success")):
            preflight_message = preflight.get("message", "")
            self.last_chat_status = {"ok": False, "error": preflight_message, "had_tool_failure": True, "mode": execution_mode}
            return {
                "success": False,
                "message": message + f"\n- 求解前检查失败：{preflight_message}",
                "mode": execution_mode,
                "dims": dims,
                "project": project_result,
                "build": build_result,
                "preflight": preflight,
                "solver": {},
                "s11": {},
            }
        project_path = self.cst.project_path or ""
        if project_path:
            self._try_install_farfield_export_template(project_path)
        solver_result = self.cst.run_solver(timeout=300)
        solver_ok = coerce_success(solver_result.get("success"))
        s11_summary = self._collect_s11_summary(req.f0_ghz) if solver_ok else {}
        s11_ok = coerce_success(s11_summary.get("success")) and bool(s11_summary.get("plot_data"))
        if s11_ok and s11_summary.get("raw"):
            self.last_results = s11_summary["raw"]
        if solver_ok and project_path:
            self._export_farfield_online_after_solver(project_path, f"Farfields\\farfield (f={req.f0_ghz})")
        success = solver_ok and s11_ok
        if success:
            message += (
                "\n- 求解：已完成一次求解"
                f"\n- S11最小值：{s11_summary['min_s11_db']:.2f} dB @ {s11_summary['min_freq_ghz']:.3f} GHz"
                f"\n- 在 {s11_summary['target_freq_ghz']:.3f} GHz 处：{s11_summary['target_s11_db']:.2f} dB"
            )
            error_message = ""
        elif not solver_ok:
            error_message = str(solver_result.get("message") or "solver failed")
            message = f"振子求解失败：{error_message}"
        else:
            error_message = str(s11_summary.get("message") or "solver 成功但 S11 结果为空")
            message += f"\n- 结果读取失败：{error_message}"
        self.last_chat_status = {
            "ok": success,
            "error": error_message,
            "had_tool_failure": not success,
            "mode": execution_mode,
        }
        return {
            "success": success,
            "message": message,
            "mode": execution_mode,
            "dims": dims,
            "project": project_result,
            "build": build_result,
            "preflight": preflight,
            "solver": solver_result,
            "s11": s11_summary,
        }

    def _run_pixel_patch_fast_path(self, user_message: str):
        """拦截像素化贴片自然语言请求，走确定性 fast path。"""
        if self._optimization_mode:
            return None
        lowered = user_message.lower()
        pixel_keywords = ["像素", "pixel", "pixel patch", "像素天线", "像素化贴片"]
        if not any(kw in user_message or kw in lowered for kw in pixel_keywords):
            return None
        import re
        m = re.search(r'(\d+(?:\.\d+)?)\s*ghz', user_message, re.IGNORECASE)
        if m is None:
            return None
        f0_ghz = float(m.group(1))
        self.opt_state.target_freq = f0_ghz
        allow_solver = solver_explicitly_requested(user_message)
        # 解析可选参数：N×M 格，每格 X mm
        n_rows = n_cols = 8
        cell_size = 2.0
        m_grid = re.search(r'(\d+)[×x\*](\d+)', user_message)
        if m_grid:
            n_rows = int(m_grid.group(1))
            n_cols = int(m_grid.group(2))
        m_cell = re.search(r'每格\s*(\d+(?:\.\d+)?)\s*mm', user_message)
        if m_cell:
            cell_size = float(m_cell.group(1))
        from cst_agent_workbench.cst.pixel_patch import PixelPatchConfig, random_initial_grid, pixel_grid_to_vba
        config = PixelPatchConfig(f0_ghz=f0_ghz, n_rows=n_rows, n_cols=n_cols, cell_size_mm=cell_size)
        grid = random_initial_grid(config)
        vba = pixel_grid_to_vba(grid, config)
        reset_created_objects()
        result = self.cst.execute_vba(vba, timeout=120)
        if not coerce_success(result.get("success")):
            self.last_chat_status = {"ok": False, "error": result.get("message", ""), "had_tool_failure": True, "mode": "pixel_patch_fast_path"}
            return f"像素贴片建模失败：{result.get('message', '')}"
        if result.get("executed", False):
            register_material(config.substrate_name)
            register_object("ground_plane", "ground", "PEC")
            register_object("substrate", "substrate", config.substrate_name)
            register_object("pixel_patch", "feed_pixel", "PEC")
            register_port(1, "50")
            register_frequency_range(f"{f0_ghz * 0.7:.3f}", f"{f0_ghz * 1.3:.3f}")
            register_farfield_monitor(f"farfield (f={f0_ghz})", str(f0_ghz), False)
        msg = (
            f"像素化贴片天线已创建（随机初始网格）：\n"
            f"- 目标频率：{f0_ghz} GHz\n"
            f"- 网格：{config.n_rows}×{config.n_cols} 像素，每格 {config.cell_size_mm}mm\n"
            f"- 基板：h={config.substrate_thickness_mm}mm，εr={config.epsilon_r}\n"
            f"- 建议用差分进化（DE）算法对像素矩阵进行优化"
        )
        if not allow_solver:
            self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": "pixel_patch_fast_path"}
            return msg + "\n- 求解：已按安全策略跳过；如需运行仿真，请明确说“求解/仿真”。"
        preflight = validate_solver_ready(self.cst, source="fast_path.pixel_patch")
        if not coerce_success(preflight.get("success")):
            preflight_message = preflight.get("message", "")
            self.last_chat_status = {"ok": False, "error": preflight_message, "had_tool_failure": True, "mode": "pixel_patch_fast_path"}
            return msg + f"\n- 求解前检查失败：{preflight_message}"
        project_path = self.cst.project_path or ""
        if project_path:
            self._try_install_farfield_export_template(project_path)
        solver_result = self.cst.run_solver(timeout=360)
        solver_ok = coerce_success(solver_result.get("success"))
        if solver_ok and project_path:
            self._export_farfield_online_after_solver(project_path, f"Farfields\\farfield (f={f0_ghz})")
        solver_message = solver_result.get("message", "")
        self.last_chat_status = {"ok": solver_ok, "error": "" if solver_ok else solver_message, "had_tool_failure": not solver_ok, "mode": "pixel_patch_fast_path"}
        return msg if solver_ok else f"像素贴片求解失败：{solver_message}"
