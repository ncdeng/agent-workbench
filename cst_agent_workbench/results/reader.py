# CST 结果读取模块
# 基于 cst.results 官方 API，支持在没有运行中 CST 的情况下读取 0D/1D 结果。
# 参考 pcb_3d_simulation.py 第 106-119 行。

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Dict, List, Optional

from cst_agent_workbench import config
from cst_agent_workbench.results.contracts import ResultKind, downsample_curve, paginate_items

try:
    from cst.results import ProjectFile
except ImportError:
    ProjectFile = None


_RESULTS_SCRIPT = """\
import json
import sys

from cst_agent_workbench.results import reader as reader_mod

payload = json.loads(sys.argv[1])
action = payload.get("action", "")
if reader_mod.ProjectFile is None:
    print(json.dumps({"success": False, "message": "cst.results 包未安装，无法读取结果"}, ensure_ascii=False))
else:
    reader = reader_mod.ResultsReader()
    open_result = reader.open(payload.get("cst_path", ""))
    if action == "open" or not open_result.get("success"):
        print(json.dumps(open_result, ensure_ascii=False))
    elif action == "list_results":
        print(json.dumps(reader.list_results(
            offset=payload.get("offset", 0),
            limit=payload.get("limit", 50),
            category=payload.get("category"),
            query=payload.get("query"),
        ), ensure_ascii=False))
    elif action == "read_result":
        print(json.dumps(reader.read_result(
            payload.get("item_path", ""),
            max_points=payload.get("max_points"),
        ), ensure_ascii=False))
    elif action == "list_farfield_results":
        print(json.dumps(reader.list_farfield_results(), ensure_ascii=False))
    elif action == "get_farfield_plot":
        print(json.dumps(reader.get_farfield_plot(payload.get("preferred_cut")), ensure_ascii=False))
    else:
        print(json.dumps({"success": False, "message": "未知结果读取动作: " + action}, ensure_ascii=False))
"""


class ResultsReader:
    """读取 CST 工程文件中的 0D/1D 结果。不需要运行中的 CST 实例。"""

    FARFIELD_PREFERRED_CUTS = ("\\phi=0\\", "\\theta=90\\")

    def __init__(self):
        self._project_file = None
        self._result_module = None
        self._cst_path = ""
        self._uses_subprocess = False
        self._subprocess_python_command = None

    @property
    def available(self) -> bool:
        """cst.results 是否可用"""
        return ProjectFile is not None

    def _run_subprocess(self, action: str, **payload) -> dict:
        from cst_agent_workbench.cst.controller import _resolve_cst_python_command

        if self._subprocess_python_command is None:
            self._subprocess_python_command = _resolve_cst_python_command()
        python_command = self._subprocess_python_command
        if not python_command:
            return {
                "success": False,
                "message": "cst.results 在当前 Python 不可用，且未找到 CST 支持的 Python 3.8-3.12",
            }
        payload = {"action": action, **payload}
        try:
            temp_dir = Path(config.CST_TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
                dir=temp_dir,
            ) as f:
                f.write(_RESULTS_SCRIPT)
                script_file = f.name
        except Exception as exc:
            return {"success": False, "message": f"写入结果读取脚本失败: {exc}"}

        try:
            env = os.environ.copy()
            env["TMP"] = str(temp_dir)
            env["TEMP"] = str(temp_dir)
            repo_dir = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = repo_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            result = subprocess.run(
                python_command + [script_file, json.dumps(payload, ensure_ascii=False)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                env=env,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in reversed(result.stdout.strip().splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            continue
                return {"success": False, "message": f"结果读取子进程输出中未找到有效 JSON: {result.stdout.strip()[-200:]}"}
            stderr_text = result.stderr.strip() or result.stdout.strip()
            return {"success": False, "message": f"结果读取子进程失败 (returncode={result.returncode}): {stderr_text[-200:]}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "结果读取子进程超时"}
        except Exception as exc:
            return {"success": False, "message": f"结果读取子进程异常: {exc}"}
        finally:
            try:
                os.unlink(script_file)
            except OSError:
                pass

    def open(self, cst_path: str) -> dict:
        """打开一个 CST 工程文件用于结果读取。"""
        if not cst_path or not os.path.exists(cst_path):
            return {"success": False, "message": f"工程文件不存在: {cst_path}"}

        if not self.available:
            result = self._run_subprocess("open", cst_path=cst_path)
            if result.get("success"):
                self._project_file = None
                self._result_module = "subprocess"
                self._cst_path = cst_path
                self._uses_subprocess = True
            return result

        try:
            self._project_file = ProjectFile(cst_path, allow_interactive=True)
            self._result_module = self._project_file.get_3d()
            self._cst_path = cst_path
            self._uses_subprocess = False
            return {"success": True, "message": f"已打开结果文件: {os.path.basename(cst_path)}"}
        except Exception as exc:
            self._project_file = None
            self._result_module = None
            self._uses_subprocess = False
            return {"success": False, "message": f"打开结果文件失败: {exc}"}

    def _normalize_item_path(self, item_path: str) -> str:
        return (item_path or "").replace("/", "\\").strip()

    def _is_farfield_item(self, item_path: str) -> bool:
        return self._normalize_item_path(item_path).lower().startswith("farfields\\")

    def _is_farfield_plot_item(self, item_path: str) -> bool:
        normalized = self._normalize_item_path(item_path).lower()
        return normalized.startswith("farfields\\1d results\\")

    def _is_raw_farfield_monitor_item(self, item_path: str) -> bool:
        normalized = self._normalize_item_path(item_path)
        return self._is_farfield_item(normalized) and not self._is_farfield_plot_item(normalized)

    def _collect_farfield_items(self, items: List[str]) -> dict:
        farfield_items = [item for item in items if self._is_farfield_item(item)]
        plot_items = [item for item in farfield_items if self._is_farfield_plot_item(item)]
        raw_monitor_items = [item for item in farfield_items if not self._is_farfield_plot_item(item)]

        if plot_items:
            message = f"发现 {len(plot_items)} 个可读取的远场 1D 切线结果"
            if raw_monitor_items:
                message += f"，以及 {len(raw_monitor_items)} 个原始远场监视器节点"
        elif raw_monitor_items:
            message = (
                f"仅发现 {len(raw_monitor_items)} 个原始远场监视器节点；"
                "当前 cst.results 路径不能可靠地把这些节点直接转换成可绘制的 1D 方向图。"
                "请优先在 Farfields\\1D Results 下选择 phi/theta 切线结果，或在 CST GUI 中查看远场图。"
            )
        else:
            message = "未发现远场结果"

        return {
            "items": farfield_items,
            "plot_items": plot_items,
            "raw_monitor_items": raw_monitor_items,
            "plot_count": len(plot_items),
            "raw_monitor_count": len(raw_monitor_items),
            "message": message,
        }


    def list_results(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        category: Optional[str] = None,
        query: Optional[str] = None,
    ) -> dict:
        """List a bounded, stable page of result-tree items."""
        if self._uses_subprocess:
            return self._run_subprocess(
                "list_results",
                cst_path=self._cst_path,
                offset=offset,
                limit=limit,
                category=category,
                query=query,
            )
        if self._result_module is None:
            return {"success": False, "message": "未打开结果文件，请先调用 open()"}

        try:
            all_items = list(self._result_module.get_tree_items())
            categories: Dict[str, List[str]] = {}
            for item in all_items:
                parts = item.split("\\")
                category_name = parts[0] if parts else "Other"
                categories.setdefault(category_name, []).append(item)

            category_filter = str(category or "").strip().casefold()
            query_filter = str(query or "").strip().casefold()
            items = all_items
            if category_filter:
                items = [item for item in items if item.split("\\", 1)[0].casefold() == category_filter]
            if query_filter:
                items = [item for item in items if query_filter in item.casefold()]
            page = paginate_items(items, offset=max(0, int(offset)), limit=max(1, min(100, int(limit))))
            farfield = self._collect_farfield_items(items)
            return {
                "success": True,
                "message": f"匹配 {len(items)} 个结果项，返回 {page['returned']} 个",
                "categories": {k: len(v) for k, v in categories.items()},
                "farfield": {
                    "plot_count": farfield["plot_count"],
                    "raw_monitor_count": farfield["raw_monitor_count"],
                    "message": farfield["message"],
                },
                "filters": {"category": category or None, "query": query or None},
                **page,
            }
        except Exception as exc:
            return {"success": False, "message": f"列出结果失败: {exc}"}

    def list_farfield_results(self) -> dict:
        """列出 Farfield 相关结果项。"""
        if self._uses_subprocess:
            return self._run_subprocess("list_farfield_results", cst_path=self._cst_path)
        if self._result_module is None:
            return {"success": False, "message": "未打开结果文件，请先调用 open()"}
        try:
            farfield = self._collect_farfield_items(list(self._result_module.get_tree_items()))
        except Exception as exc:
            return {"success": False, "message": f"列出远场结果失败: {exc}"}
        return {
            "success": True,
            "message": f"找到 {len(farfield['items'])} 个 farfield 结果项",
            "total": len(farfield["items"]),
            "items": farfield["items"],
            "plot_items": farfield.get("plot_items", []),
            "raw_monitor_items": farfield.get("raw_monitor_items", []),
        }

    def read_result(self, item_path: str, *, max_points: Optional[int] = None) -> dict:
        """读取指定的结果数据。
        item_path 示例: "1D Results\\S-Parameters\\S1,1"
        """
        if self._uses_subprocess:
            payload = {"cst_path": self._cst_path, "item_path": item_path}
            if max_points is not None:
                payload["max_points"] = max_points
            return self._run_subprocess("read_result", **payload)
        if self._result_module is None:
            return {"success": False, "message": "未打开结果文件，请先调用 open()"}

        item_path = self._normalize_item_path(item_path)
        if self._is_raw_farfield_monitor_item(item_path):
            try:
                farfield = self._collect_farfield_items(self._result_module.get_tree_items())
            except Exception:
                farfield = None

            message = (
                f"结果项 `{item_path}` 是原始远场监视器节点，不是可直接读取的 1D 方向图结果。"
                "当前 cst.results API 在此路径下只能稳定读取 0D/1D 结果，"
                "无法把原始 Farfields 监视器节点直接转换为方向图曲线。"
            )
            if farfield and farfield.get("plot_items"):
                message += " 可改读这些 1D 远场切线结果之一：" + "; ".join(farfield["plot_items"][:5])
            else:
                message += " 若结果树里只有该监视器节点，请在 CST GUI 中查看远场图，或先确认 Farfields\\1D Results 下已生成切线结果。"

            return {
                "success": False,
                "message": message,
                "item": item_path,
                "type": "farfield_monitor_only",
                "result_kind": ResultKind.UNSUPPORTED_3D.value,
                "farfield": farfield,
            }

        try:
            result_item = self._result_module.get_result_item(item_path)
            data = result_item.get_data()
            summary = self._summarize_data(data, item_path, max_points=max_points)
            return {
                "success": True,
                "message": f"已读取: {item_path}",
                "item": item_path,
                **summary,
            }
        except Exception as exc:
            return {"success": False, "message": f"读取结果失败 ({item_path}): {exc}"}

    def _summarize_data(self, data, item_path: str, *, max_points: Optional[int] = None) -> dict:
        """将结果数据转换为可序列化的摘要。"""
        summary = {}

        try:
            if isinstance(data, Mapping) or hasattr(data, "keys"):
                keys = list(data.keys())[:20]
                summary["type"] = type(data).__name__
                summary["result_kind"] = ResultKind.ARTIFACT_REF.value
                summary["keys"] = keys
                summary["sample"] = {str(k): self._safe_preview(data[k]) for k in keys[:5]}
                return summary
        except Exception:
            pass

        try:
            if hasattr(data, "x_data") and hasattr(data, "y_data"):
                x = [self._coerce_float(v) for v in list(data.x_data)]
                y = [self._coerce_float(v) for v in list(data.y_data)]
                pairs = [
                    (xv, yv)
                    for xv, yv in zip(x, y)
                    if xv is not None and yv is not None
                ]
                summary["type"] = "1d_curve"
                # The reader exposes raw x/y curves.  Only the domain service
                # may promote a farfield curve to ``farfield_cut`` after it
                # converts axes to angle_deg/gain_dbi and computes beam metrics.
                summary["result_kind"] = ResultKind.CURVE_1D.value
                if pairs:
                    x_values = [p[0] for p in pairs]
                    step = max(1, len(pairs) // 5)
                    sample_indices = list(range(0, len(pairs), step))[:6]
                    summary["x_range"] = [x_values[0], x_values[-1]]
                    summary["samples"] = [
                        {"x": pairs[i][0], "y": pairs[i][1]} for i in sample_indices if i < len(pairs)
                    ]
                    curve = downsample_curve(
                        [{"x": xv, "y": yv} for xv, yv in pairs],
                        max_points=max_points,
                        y_key="y",
                    )
                    summary.update(curve.metadata())
                    summary["plot_data"] = curve.points
                else:
                    summary.update({"points": 0, "total_points": 0, "returned_points": 0, "downsampled": False})
                    summary["plot_data"] = []
                return summary
        except Exception:
            pass

        try:
            if hasattr(data, "__len__"):
                length = len(data)

                if length > 0:
                    first = data[0]
                    if isinstance(first, tuple) and len(first) >= 2:
                        summary["type"] = "s_parameter"
                        summary["result_kind"] = ResultKind.S_PARAMETER.value
                        freqs = [float(d[0]) for d in data]
                        s_mag = [abs(complex(d[1])) for d in data]
                        import math
                        s_db = [20 * math.log10(m) if m > 0 else -999 for m in s_mag]
                        summary["freq_range"] = [freqs[0], freqs[-1]]
                        summary["s_mag_min"] = min(s_mag)
                        summary["s_mag_max"] = max(s_mag)
                        summary["s_db_min"] = min(s_db)
                        summary["s_db_max"] = max(s_db)
                        step = max(1, length // 5)
                        indices = list(range(0, length, step))[:6]
                        summary["samples"] = [
                            {"freq": freqs[i], "s_db": round(s_db[i], 3), "s_mag": round(s_mag[i], 6)}
                            for i in indices if i < length
                        ]
                        full_curve = [
                            {"freq": freqs[i], "s_db": s_db[i]}
                            for i in range(length)
                        ]
                        curve = downsample_curve(full_curve, max_points=max_points, y_key="s_db")
                        summary.update(curve.metadata())
                        summary["plot_data"] = curve.points
                        return summary

                summary["type"] = type(data).__name__
                summary["result_kind"] = ResultKind.SCALAR.value
                summary["length"] = length

                if length > 0:
                    try:
                        values = [float(v) for v in data[:min(length, 1000)]]
                        summary["min"] = min(values)
                        summary["max"] = max(values)
                        summary["first_5"] = values[:5]
                        summary["last_5"] = values[-5:]
                    except (TypeError, ValueError):
                        data_list = list(data)
                        summary["first_5"] = [self._safe_preview(v) for v in data_list[:5]]
                        summary["sample"] = [self._safe_preview(v) for v in data_list[:3]]
                return summary
        except Exception:
            pass

        summary["type"] = type(data).__name__
        summary["result_kind"] = ResultKind.SCALAR.value
        summary["repr"] = self._safe_preview(data)
        return summary

    def _coerce_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_preview(self, value, max_len: int = 200) -> str:
        try:
            text = repr(value)
        except Exception:
            text = str(type(value))
        return text[:max_len]

    def get_s_parameter(self, port_i: int = 1, port_j: int = 1, *, max_points: Optional[int] = None) -> dict:
        """读取 S 参数（便捷方法）。"""
        item_path = f"1D Results\\S-Parameters\\S{port_i},{port_j}"
        return self.read_result(item_path, max_points=max_points)

    def get_farfield_plot(self, preferred_cut: Optional[str] = None) -> dict:
        """读取一个可绘图的远场 1D 切线结果。"""
        if self._uses_subprocess:
            return self._run_subprocess("get_farfield_plot", cst_path=self._cst_path, preferred_cut=preferred_cut)
        if self._result_module is None:
            return {"success": False, "message": "未打开结果文件，请先调用 open()"}

        try:
            farfield = self._collect_farfield_items(list(self._result_module.get_tree_items()))
        except Exception as exc:
            return {"success": False, "message": f"列出远场结果失败: {exc}"}

        plot_items = farfield.get("plot_items") or []
        if not plot_items:
            return {
                "success": False,
                "message": farfield.get("message", "未发现远场结果"),
                "type": "farfield_monitor_only" if farfield.get("raw_monitor_items") else "farfield_not_found",
                "farfield": farfield,
            }

        selected_item = None
        if preferred_cut:
            preferred_cut_norm = preferred_cut.lower()
            for item in plot_items:
                if preferred_cut_norm in item.lower():
                    selected_item = item
                    break

        if selected_item is None:
            for preferred in self.FARFIELD_PREFERRED_CUTS:
                for item in plot_items:
                    if preferred.lower() in item.lower():
                        selected_item = item
                        break
                if selected_item is not None:
                    break

        if selected_item is None:
            selected_item = plot_items[0]

        result = self.read_result(selected_item)
        if result.get("success"):
            result["farfield"] = farfield
            result["selected_item"] = selected_item
        return result

    def build_farfield_cut_result(self, item_path: str, read_result: dict, cut_type: str = "phi", cut_value_deg: float = 0.0) -> dict:
        """将 1D farfield 结果标准化为可绘图的 cut payload。"""
        if not read_result.get("success"):
            return read_result

        plot_data = read_result.get("plot_data") or []
        if not isinstance(plot_data, list) or not plot_data:
            return {
                **read_result,
                "success": False,
                "message": f"远场结果项 `{item_path}` 不包含可直接绘制的 1D 曲线数据。",
            }

        normalized_points = []
        for point in plot_data:
            angle = self._coerce_float(point.get("x"))
            gain = self._coerce_float(point.get("y"))
            if angle is None or gain is None:
                continue
            normalized_points.append({"angle_deg": angle, "gain_dbi": gain})

        if not normalized_points:
            return {
                **read_result,
                "success": False,
                "message": f"远场结果项 `{item_path}` 中没有可转换的角度/增益数据。",
            }

        metadata = self._extract_farfield_metadata(item_path)
        effective_cut_type = (cut_type or metadata.get("cut_type") or "phi").lower()
        if effective_cut_type not in {"theta", "phi"}:
            effective_cut_type = "phi"

        explicit_cut_value = self._coerce_float(cut_value_deg)
        effective_cut_value = explicit_cut_value if explicit_cut_value is not None else metadata.get("cut_value_deg", 0.0)
        frequency_ghz = metadata.get("frequency_ghz")

        peak_point = max(normalized_points, key=lambda p: p["gain_dbi"])
        angle_values = [p["angle_deg"] for p in normalized_points]
        cut_symbol = "φ" if effective_cut_type == "phi" else "θ"
        title = f"Farfield {cut_symbol}={effective_cut_value:.1f}°"
        if frequency_ghz is not None:
            title += f" @ {frequency_ghz:.4f} GHz"

        # E-2: 主瓣指标（HPBW / 前后比 / 副瓣电平）
        from cst_agent_workbench.results.summary import analyze_farfield_cut
        beam_metrics = analyze_farfield_cut(normalized_points)

        return {
            "success": True,
            "message": f"已读取 Farfield {effective_cut_type} cut: {item_path}",
            "item": item_path,
            "type": "farfield_cut",
            "result_kind": "farfield_cut",
            "cut_type": effective_cut_type,
            "cut_value_deg": effective_cut_value,
            "frequency_ghz": frequency_ghz,
            "x_key": "angle_deg",
            "y_key": "gain_dbi",
            "x_label": "Angle (deg)",
            "y_label": "Gain (dBi)",
            "title": title,
            "points": len(normalized_points),
            "plot_data": normalized_points,
            "summary": {
                "peak_gain_dbi": peak_point["gain_dbi"],
                "peak_angle_deg": peak_point["angle_deg"],
                "angle_range": [min(angle_values), max(angle_values)],
                "points": len(normalized_points),
                "hpbw_deg": beam_metrics["hpbw_deg"],
                "front_to_back_db": beam_metrics["front_to_back_db"],
                "sidelobe_level_db": beam_metrics["sidelobe_level_db"],
            },
        }

    def _extract_farfield_metadata(self, item_path: str) -> dict:
        text = item_path or ""
        lower_text = text.lower()

        frequency_ghz = None
        cut_type = None
        cut_value_deg = None

        freq_match = re.search(r"f\s*=\s*([-+]?\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
        if freq_match:
            frequency_ghz = self._coerce_float(freq_match.group(1))

        phi_match = re.search(r"phi\s*=\s*([-+]?\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
        theta_match = re.search(r"theta\s*=\s*([-+]?\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)

        if phi_match and not theta_match:
            cut_type = "phi"
            cut_value_deg = self._coerce_float(phi_match.group(1))
        elif theta_match and not phi_match:
            cut_type = "theta"
            cut_value_deg = self._coerce_float(theta_match.group(1))
        elif "phi" in lower_text and "theta" not in lower_text:
            cut_type = "phi"
        elif "theta" in lower_text and "phi" not in lower_text:
            cut_type = "theta"

        return {
            "frequency_ghz": frequency_ghz,
            "cut_type": cut_type,
            "cut_value_deg": cut_value_deg,
        }

    def close(self):
        """关闭结果文件。"""
        self._project_file = None
        self._result_module = None
        self._cst_path = ""
