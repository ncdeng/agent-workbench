import os
import uuid
from dataclasses import replace
from typing import Any, Dict, Optional

from cst_agent_workbench import config
from cst_agent_workbench.cst.rectangular_patch_fast import RectangularPatchRequest, build_rectangular_patch_vba_artifact
from cst_agent_workbench.cst.solver_safety import validate_solver_ready

# Path to the Export Farfields in ASCII Format result template files
_CST_INSTALL_ROOT = config.CST_INSTALL_ROOT
_FARFIELD_TEMPLATE_RTP = os.path.join(
    _CST_INSTALL_ROOT,
    "Library", "Result Templates", "Farfield and Antenna Properties",
    "Export Farfields in ASCII Format^+MWS+DS.rtp",
)
_FARFIELD_TEMPLATE_RPP_CONTENT = (
    "11\r\n0\r\n0\r\n1\r\n"
    "Export Farfields in ASCII Format\r\n"
    "Export Farfields in ASCII Format\r\n"
    "1\r\n0D\r\nFarfield and Antenna Properties\r\n"
    "P\r\nED10\r\n"
    "Export Farfields in ASCII Format^+MWS+DS.rtp\r\n"
    "VBA\r\n1\r\n"
    "Export Farfields in ASCII Format\r\n0\r\n"
)
_FARFIELD_TEMPLATE_R0D_CONTENT = None  # lazy-loaded from source


def _coerce_success(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "ok", "success", "succeeded"}
    return False


def _repo_local_farfield_template_r0d() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'farfield_template.r0d',
    )


def _farfield_template_r0d_sources():
    sources = []
    if config.FARFIELD_TEMPLATE_R0D_SOURCE:
        sources.append(config.FARFIELD_TEMPLATE_R0D_SOURCE)
    sources.append(_repo_local_farfield_template_r0d())
    return sources


def _load_farfield_template_r0d() -> Optional[bytes]:
    global _FARFIELD_TEMPLATE_R0D_CONTENT
    if _FARFIELD_TEMPLATE_R0D_CONTENT is not None:
        return _FARFIELD_TEMPLATE_R0D_CONTENT
    for src in _farfield_template_r0d_sources():
        if os.path.exists(src):
            with open(src, 'rb') as f:
                _FARFIELD_TEMPLATE_R0D_CONTENT = f.read()
            return _FARFIELD_TEMPLATE_R0D_CONTENT
    return None


class PatchFastExecutorMixin:
    def _try_install_farfield_export_template(self, project_path: str) -> Dict[str, str | bool]:
        event_count = len(getattr(self, "tool_events", []))
        result = self._install_farfield_export_template(project_path)
        if _coerce_success(result.get("success")):
            return result
        if len(getattr(self, "tool_events", [])) == event_count:
            self._record_fast_path_event(
                "3_边界与端口",
                "install_farfield_export_template",
                False,
                str(result.get("message", "")),
                "未安装 farfield 模板，将改用 CST 在线远场导出",
            )
        return result

    def _export_farfield_online_after_solver(self, project_path: str, fallback_item_path: str) -> Dict[str, Any]:
        # A failed readback must not leave a previous project's cut visible.
        self.last_farfield_results = {}
        if not project_path:
            return {"success": False, "message": "缺少工程路径，跳过远场在线导出"}

        try:
            tree_result = self.cst.list_farfield_tree_items(timeout=30)
        except Exception as exc:
            tree_result = {"success": False, "message": str(exc), "items": []}

        items = tree_result.get("items") or []
        fallback_key = str(fallback_item_path or "").casefold()
        item_path = next(
            (item for item in items if str(item).casefold() == fallback_key),
            items[0] if items else fallback_item_path,
        )
        if not item_path:
            result = {"success": False, "message": tree_result.get("message", "未找到 Farfields 结果树项")}
            self._record_fast_path_event(
                "5_远场导出",
                "export_farfield_ascii",
                False,
                str(result["message"]),
                "求解后未找到可导出的 farfield 结果树项",
            )
            return result

        from cst_agent_workbench.results.service import _build_farfield_export_dir, _sanitize_farfield_filename
        export_dir = _build_farfield_export_dir(project_path)
        output_path = os.path.join(export_dir, _sanitize_farfield_filename(item_path) + ".txt")
        try:
            export_result = self.cst.export_farfield_ascii(
                item_path=item_path,
                output_path=output_path,
                theta_step_deg=1.0,
                phi_step_deg=1.0,
                timeout=120,
            )
        except Exception as exc:
            export_result = {"success": False, "message": str(exc), "item_path": item_path, "output_path": output_path}

        self._record_fast_path_event(
            "5_远场导出",
            "export_farfield_ascii",
            _coerce_success(export_result.get("success")),
            str(export_result.get("message", "")),
            f"求解后通过 CST 在线接口导出 farfield: {item_path}",
        )
        if not _coerce_success(export_result.get("success")):
            return export_result

        from cst_agent_workbench.results.service import _build_exported_farfield_result

        cut_result = _build_exported_farfield_result(item_path, "phi", 0.0, export_result)
        cut_ok = _coerce_success(cut_result.get("success")) and bool(cut_result.get("plot_data"))
        self._record_fast_path_event(
            "5_远场导出",
            "read_farfield_cut",
            cut_ok,
            str(cut_result.get("message", "")),
            "将 CST farfield ASCII 转换为可绘制的 φ=0° 方向图",
        )
        if cut_ok:
            self.last_farfield_results = cut_result
        return cut_result

    def _run_rectangular_patch_fast_path_from_request(
        self,
        request: RectangularPatchRequest,
        execution_mode: str = "fast_path",
        allow_solver: bool = False,
    ) -> str:
        from cst_agent_workbench.cst.primitives import (
            register_material,
            register_object,
            register_farfield_monitor,
            register_frequency_range,
            register_parameter,
            register_port,
            reset_created_objects,
            unregister_object,
        )

        feed_strategy = (request.feed_strategy or "microstrip").strip().lower()
        if self._optimization_mode or feed_strategy not in {"microstrip", "probe"}:
            self.last_chat_status = {
                "ok": False,
                "error": "当前 fast path 仅支持非优化模式下的 microstrip/probe 矩形贴片。",
                "had_tool_failure": True,
                "mode": execution_mode,
            }
            return self.last_chat_status["error"]
        reset_created_objects()
        self._fast_path_counter += 1
        suffix = f"{self._fast_path_counter}_{uuid.uuid4().hex[:6]}"
        component_name = f"AntennaFP{suffix}"
        feed_component = f"FeedFP{suffix}"
        artifact = build_rectangular_patch_vba_artifact(
            request,
            component_name=component_name,
            feed_component=feed_component,
        )
        dims = dict(artifact["dims"])
        if artifact.get("probe"):
            dims.update(artifact["probe"])
        parameter_values = artifact["parameter_values"]
        sections = artifact["sections"]
        fast_project_dir = self._fast_path_project_dir()
        os.makedirs(fast_project_dir, exist_ok=True)
        fast_project_path = os.path.join(fast_project_dir, f"fast_patch_{suffix}.cst")

        new_project_result = self.cst.new_project(fast_project_path, timeout=120)
        self._record_fast_path_event(
            "0_新建工程",
            "new_project",
            bool(new_project_result.get("success")),
            new_project_result.get("message", ""),
            "为 fast path 创建独立 CST 工程，避免重复运行时旧几何叠加",
        )
        if not new_project_result.get("success"):
            self.last_chat_status = {
                "ok": False,
                "error": new_project_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在新建工程阶段失败: {new_project_result.get('message', '')}"
        self._cleanup_old_fast_path_projects()

        setup_batch_vba = sections["setup"]
        setup_result = self.cst.execute_vba(setup_batch_vba, label=f"fast_patch_setup_{suffix}", timeout=120)
        self.last_vba = setup_batch_vba
        self._record_fast_path_event("1_环境与材料", "fast_patch_setup", bool(setup_result.get("success")), setup_result.get("message", ""), "矩形贴片快路径: 参数、材料、频率和边界一次性下发")
        if not setup_result.get("success"):
            self.last_chat_status = {"ok": False, "error": setup_result.get("message", ""), "had_tool_failure": True, "mode": "fast_path"}
            return f"矩形贴片快速路径在初始化阶段失败: {setup_result.get('message', '')}"

        if setup_result.get("executed", False):
            for name, value in parameter_values.items():
                register_parameter(name, self._format_mm(value))
            register_material(request.substrate_name)
            register_frequency_range(self._format_mm(dims["fmin"]), self._format_mm(dims["fmax"]))

        geometry_batch_vba = sections["geometry"]
        geometry_result = self.cst.execute_vba(geometry_batch_vba, label=f"fast_patch_geometry_{suffix}", timeout=120)
        self.last_vba = geometry_batch_vba
        self._record_fast_path_event("2_几何建模", "fast_patch_geometry", bool(geometry_result.get("success")), geometry_result.get("message", ""), "矩形贴片快路径: 数值几何一次性下发")
        if not geometry_result.get("success"):
            self.last_chat_status = {"ok": False, "error": geometry_result.get("message", ""), "had_tool_failure": True, "mode": "fast_path"}
            return f"矩形贴片快速路径在几何阶段失败: {geometry_result.get('message', '')}"

        if geometry_result.get("executed", False):
            register_object(component_name, "Ground", request.conductor_name)
            register_object(component_name, "Substrate", request.substrate_name)
            register_object(component_name, "Patch", request.conductor_name)
            if feed_strategy == "probe":
                register_object(feed_component, "Probe", request.conductor_name)
                unregister_object(feed_component, "GroundHole")
            else:
                unregister_object(feed_component, "FeedLine")
                unregister_object(feed_component, "InsetGap")

        port_vba = sections["port"]
        port_result = self.cst.execute_vba(port_vba, label=f"fast_patch_port_{suffix}", timeout=120)
        self.last_vba = port_vba
        port_description = "矩形贴片快路径: probe discrete port" if feed_strategy == "probe" else "矩形贴片快路径: 侧边 waveguide port"
        self._record_fast_path_event("3_边界与端口", "fast_patch_port", bool(port_result.get("success")), port_result.get("message", ""), port_description)
        if not port_result.get("success"):
            self.last_chat_status = {"ok": False, "error": port_result.get("message", ""), "had_tool_failure": True, "mode": "fast_path"}
            return f"矩形贴片快速路径在端口阶段失败: {port_result.get('message', '')}"

        if port_result.get("executed", False):
            register_port(1, "50")

        farfield_name = f"farfield (f={self._format_mm(request.f0_ghz)})"
        farfield_frequency = self._format_mm(request.f0_ghz)
        farfield_vba = sections["farfield"]
        farfield_result = self.cst.execute_vba(
            farfield_vba,
            label=f"fast_patch_farfield_monitor_{suffix}",
            timeout=60,
        )
        self.last_vba = farfield_vba
        self._record_fast_path_event(
            "3_边界与端口",
            "create_farfield_monitor",
            bool(farfield_result.get("success")),
            farfield_result.get("message", ""),
            "矩形贴片快路径: 在求解前创建 farfield monitor",
        )
        if not farfield_result.get("success"):
            self.last_chat_status = {
                "ok": False,
                "error": farfield_result.get("message", ""),
                "had_tool_failure": True,
                "mode": "fast_path",
            }
            return f"矩形贴片快速路径在远场监视器阶段失败: {farfield_result.get('message', '')}"

        if farfield_result.get("executed", False):
            register_farfield_monitor(farfield_name, farfield_frequency, False)

        # 根据中心频率自适应设置网格精细度
        try:
            mesh_result = self.cst.set_mesh_by_frequency(request.f0_ghz, request.epsilon_r, project_path=fast_project_path)
            self._record_fast_path_event("3_边界与端口", "set_mesh", bool(mesh_result.get("success")), mesh_result.get("message", ""), f"自适应网格 f0={request.f0_ghz:.3f}GHz Dk={request.epsilon_r}")
        except Exception as _mesh_err:
            self._record_fast_path_event("3_边界与端口", "set_mesh", False, str(_mesh_err), f"自适应网格异常 f0={request.f0_ghz:.3f}GHz")

        if not allow_solver:
            solver_result = {
                "success": True,
                "skipped": True,
                "message": "未检测到明确求解/仿真请求，已按安全策略跳过 solver",
            }
            self._record_fast_path_event("4_运行仿真", "run_solver_skipped", True, solver_result["message"], "矩形贴片快路径: 安全门跳过自动求解")
            self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False, "mode": execution_mode}
            self.last_patch_request = replace(request)
            return self._build_fast_path_response(request, dims, component_name, solver_result, {})

        preflight = validate_solver_ready(self.cst, source="fast_path.rectangular_patch")
        if not _coerce_success(preflight.get("success")):
            self._record_fast_path_event("4_运行仿真", "run_solver_preflight", False, preflight.get("message", ""), "矩形贴片快路径: 求解前检查失败")
            self.last_chat_status = {"ok": False, "error": preflight.get("message", ""), "had_tool_failure": True, "mode": execution_mode}
            return self._build_fast_path_response(request, dims, component_name, preflight, {})

        self._try_install_farfield_export_template(fast_project_path)
        solver_result = self.cst.run_solver(timeout=300)
        solver_ok = _coerce_success(solver_result.get("success"))
        self._record_fast_path_event("4_运行仿真", "run_solver", solver_ok, solver_result.get("message", ""), "矩形贴片快路径: 单次求解")
        if solver_ok:
            self._export_farfield_online_after_solver(
                fast_project_path,
                f"Farfields\\farfield (f={self._format_mm(request.f0_ghz)})",
            )
        s11_summary = self._collect_s11_summary(request.f0_ghz) if solver_ok else {}
        s11_ok = _coerce_success(s11_summary.get("success")) and bool(s11_summary.get("plot_data"))
        if s11_ok and s11_summary.get("raw"):
            self.last_results = s11_summary["raw"]
        final_text = self._build_fast_path_response(request, dims, component_name, solver_result, s11_summary)
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

    def _install_farfield_export_template(self, project_path: str) -> Dict[str, str | bool]:
        """把 Model.rpp + .r0d 安装到新工程，让 CST 在求解后自动导出 farfield ASCII。"""
        proj_dir = project_path.replace('.cst', '')
        dst_dir = os.path.join(proj_dir, 'Model', '3D')
        if not os.path.exists(dst_dir):
            return {"success": False, "message": f"未找到 CST 工程模板目录: {dst_dir}"}

        r0d_content = _load_farfield_template_r0d()
        if r0d_content is None:
            message = "未找到 farfield_template.r0d；请设置 FARFIELD_TEMPLATE_R0D_SOURCE 或保留仓库根目录模板。"
            self._record_fast_path_event(
                "3_边界与端口",
                "install_farfield_export_template",
                False,
                message,
                "无法安装 farfield ASCII 导出模板配置",
            )
            return {"success": False, "message": message}

        dst_rpp = os.path.join(dst_dir, 'Model.rpp')
        dst_r0d = os.path.join(dst_dir, 'Export Farfields in ASCII Format.r0d')
        try:
            with open(dst_rpp, 'w', encoding='ascii') as f:
                f.write(_FARFIELD_TEMPLATE_RPP_CONTENT)
            with open(dst_r0d, 'wb') as f:
                f.write(r0d_content)
        except Exception as exc:
            return {"success": False, "message": f"写入 farfield 导出模板失败: {exc}"}
        return {"success": True, "message": "farfield ASCII 导出模板已安装"}
