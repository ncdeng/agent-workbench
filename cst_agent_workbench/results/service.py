import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

from cst_agent_workbench.results.contracts import ResultKind


def _missing_project_path() -> dict:
    return {
        "success": False,
        "message": "当前未连接 CST 或未找到工程文件路径。请先连接 CST 并打开工程。",
    }


def _has_open_results(reader) -> bool:
    return getattr(reader, "_result_module", None) is not None


def _build_farfield_export_dir(project_path: str) -> str:
    project_root, _ = os.path.splitext(project_path)
    export_dir = os.path.join(project_root, "Export", "Farfield")
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def _sanitize_farfield_filename(item_path: str) -> str:
    safe = (item_path or "farfield").replace("/", "_").replace("\\", "_")
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", ".", " ", "(", ")", "[", "]", "="} else "_" for ch in safe)
    safe = "_".join(part for part in safe.split())
    return safe[:180] or "farfield"


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ascii_export_points(file_path: str) -> list[dict]:
    points = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            normalized = line.replace(",", ".")
            parts = normalized.split()
            numeric_values = []
            for token in parts:
                try:
                    numeric_values.append(float(token))
                except ValueError:
                    continue
            if len(numeric_values) < 3:
                continue
            theta_deg = numeric_values[0]
            phi_deg = numeric_values[1]
            value = numeric_values[2]
            points.append({"theta_deg": theta_deg, "phi_deg": phi_deg, "value": value})
    return points


def _build_points_from_numeric_payload(payload: dict) -> list[dict]:
    theta_values = payload.get("theta") or []
    phi_values = payload.get("phi") or []
    value_values = payload.get("value") or []
    if not theta_values or not phi_values or not value_values:
        return []
    if not (len(theta_values) == len(phi_values) == len(value_values)):
        return []

    points = []
    for theta_deg, phi_deg, value in zip(theta_values, phi_values, value_values):
        theta_num = _coerce_float(theta_deg)
        phi_num = _coerce_float(phi_deg)
        value_num = _coerce_float(value)
        if theta_num is None or phi_num is None or value_num is None:
            continue
        points.append({"theta_deg": theta_num, "phi_deg": phi_num, "value": value_num})
    return points


def _extract_export_cut(points: list[dict], cut_type: str, cut_value_deg: float) -> list[dict]:
    normalized_cut_type = (cut_type or "phi").lower()
    target = float(cut_value_deg)
    fixed_key = "phi_deg" if normalized_cut_type == "phi" else "theta_deg"
    sweep_key = "theta_deg" if normalized_cut_type == "phi" else "phi_deg"
    grouped: dict[float, list[dict]] = defaultdict(list)
    for point in points:
        fixed = _coerce_float(point.get(fixed_key))
        sweep = _coerce_float(point.get(sweep_key))
        value = _coerce_float(point.get("value"))
        if fixed is None or sweep is None or value is None:
            continue
        grouped[round(fixed, 6)].append({"angle_deg": sweep, "gain_dbi": value})
    if not grouped:
        return []
    best_key = min(grouped.keys(), key=lambda key: abs(key - target))
    cut_points = sorted(grouped[best_key], key=lambda item: item["angle_deg"])

    # 对 phi cut：拼接对称面（phi+180°）构成完整 360° 圆
    if normalized_cut_type == "phi":
        opposite = target + 180.0
        if opposite >= 360.0:
            opposite -= 360.0
        opp_key = min(grouped.keys(), key=lambda key: abs(key - opposite))
        if abs(opp_key - opposite) < 10.0 and opp_key != best_key:
            opp_points = sorted(grouped[opp_key], key=lambda item: item["angle_deg"], reverse=True)
            # opposite side: angle maps to 180+theta (continuing around circle)
            extended = [{"angle_deg": 180.0 + (180.0 - p["angle_deg"]), "gain_dbi": p["gain_dbi"]} for p in opp_points if p["angle_deg"] > 0]
            cut_points = cut_points + extended

    return cut_points


def _build_exported_farfield_result(item_path: str, cut_type: str, cut_value_deg: float, export_result: dict) -> dict:
    output_path = export_result.get("output_path", "")
    if not output_path or not os.path.exists(output_path):
        return {
            "success": False,
            "message": f"Farfield ASCII 导出完成，但未找到导出文件: {output_path}",
            "type": "farfield_export_missing",
            "item": item_path,
            "export": export_result,
        }

    points = _parse_ascii_export_points(output_path)
    if not points:
        return {
            "success": False,
            "message": f"Farfield ASCII 导出文件为空或无法解析: {output_path}",
            "type": "farfield_export_parse_failed",
            "item": item_path,
            "export": export_result,
        }

    cut_points = _extract_export_cut(points, cut_type, cut_value_deg)
    if not cut_points:
        return {
            "success": False,
            "message": f"Farfield ASCII 导出成功，但无法从导出数据中提取 {cut_type}={cut_value_deg:.1f}° cut。",
            "type": "farfield_export_cut_missing",
            "item": item_path,
            "export": export_result,
        }

    peak_point = max(cut_points, key=lambda p: p["gain_dbi"])
    angle_values = [p["angle_deg"] for p in cut_points]
    cut_symbol = "φ" if (cut_type or "phi").lower() == "phi" else "θ"
    freq_match = None
    try:
        import re
        freq_match = re.search(r"f\s*=\s*([-+]?\d+(?:\.\d+)?)", item_path or "", flags=re.IGNORECASE)
    except Exception:
        freq_match = None
    frequency_ghz = float(freq_match.group(1)) if freq_match else None
    title = f"Farfield {cut_symbol}={float(cut_value_deg):.1f}°"
    if frequency_ghz is not None:
        title += f" @ {frequency_ghz:.4f} GHz"

    return {
        "success": True,
        "message": f"已通过 CST 在线导出读取 Farfield {(cut_type or 'phi').lower()} cut: {item_path}",
        "item": item_path,
        "type": "farfield_cut",
        "result_kind": "farfield_cut",
        "cut_type": (cut_type or "phi").lower(),
        "cut_value_deg": float(cut_value_deg),
        "frequency_ghz": frequency_ghz,
        "x_key": "angle_deg",
        "y_key": "gain_dbi",
        "x_label": "Angle (deg)",
        "y_label": "Gain (dBi)",
        "title": title,
        "points": len(cut_points),
        "plot_data": cut_points,
        "summary": {
            "peak_gain_dbi": peak_point["gain_dbi"],
            "peak_angle_deg": peak_point["angle_deg"],
            "angle_range": [min(angle_values), max(angle_values)],
            "points": len(cut_points),
        },
        "export_backend": "cst_ascii_export",
        "export_path": output_path,
        "export": export_result,
    }


def _build_numeric_farfield_result(item_path: str, cut_type: str, cut_value_deg: float, numeric_result: dict) -> dict:
    points = _build_points_from_numeric_payload(numeric_result)
    if not points:
        return {
            "success": False,
            "message": "Farfield GetList 数值结果为空或字段不完整。",
            "type": "farfield_getlist_parse_failed",
            "item": item_path,
            "export": numeric_result,
        }

    cut_points = _extract_export_cut(points, cut_type, cut_value_deg)
    if not cut_points:
        return {
            "success": False,
            "message": f"Farfield GetList 数值提取成功，但无法从导出数据中提取 {cut_type}={cut_value_deg:.1f}° cut。",
            "type": "farfield_getlist_cut_missing",
            "item": item_path,
            "export": numeric_result,
        }

    peak_point = max(cut_points, key=lambda p: p["gain_dbi"])
    angle_values = [p["angle_deg"] for p in cut_points]
    cut_symbol = "φ" if (cut_type or "phi").lower() == "phi" else "θ"
    freq_match = None
    try:
        import re
        freq_match = re.search(r"f\s*=\s*([-+]?\d+(?:\.\d+)?)", item_path or "", flags=re.IGNORECASE)
    except Exception:
        freq_match = None
    frequency_ghz = float(freq_match.group(1)) if freq_match else None
    title = f"Farfield {cut_symbol}={float(cut_value_deg):.1f}°"
    if frequency_ghz is not None:
        title += f" @ {frequency_ghz:.4f} GHz"

    return {
        "success": True,
        "message": f"已通过 CST GetList 数值提取读取 Farfield {(cut_type or 'phi').lower()} cut: {item_path}",
        "item": item_path,
        "type": "farfield_cut",
        "result_kind": "farfield_cut",
        "cut_type": (cut_type or "phi").lower(),
        "cut_value_deg": float(cut_value_deg),
        "frequency_ghz": frequency_ghz,
        "x_key": "angle_deg",
        "y_key": "gain_dbi",
        "x_label": "Angle (deg)",
        "y_label": "Gain (dBi)",
        "title": title,
        "points": len(cut_points),
        "plot_data": cut_points,
        "summary": {
            "peak_gain_dbi": peak_point["gain_dbi"],
            "peak_angle_deg": peak_point["angle_deg"],
            "angle_range": [min(angle_values), max(angle_values)],
            "points": len(cut_points),
        },
        "export_backend": "cst_farfield_get_list",
        "export_path": numeric_result.get("output_path", ""),
        "export": numeric_result,
    }


def _find_template_exported_farfield(project_path: str, item_path: str) -> str:
    """查找 Template Based Post-Processing 或 fast path 自动导出的 farfield ASCII 文件。"""
    if not project_path:
        return ""
    project_root, _ = os.path.splitext(project_path)
    export_dir = os.path.join(project_root, "Export", "Farfield")
    if not os.path.isdir(export_dir):
        return ""
    leaf = item_path.replace("/", "\\").rstrip("\\").split("\\")[-1]
    # Direct match: leaf.txt
    candidate = os.path.join(export_dir, leaf + ".txt")
    if os.path.exists(candidate):
        return candidate
    # Sanitized match: _sanitize_farfield_filename(item_path).txt
    sanitized = _sanitize_farfield_filename(item_path) + ".txt"
    candidate2 = os.path.join(export_dir, sanitized)
    if os.path.exists(candidate2):
        return candidate2
    # Scan directory for any .txt whose stem matches leaf or sanitized
    leaf_lower = leaf.lower()
    san_lower = sanitized.lower()
    for fname in os.listdir(export_dir):
        if fname.lower().endswith(".txt"):
            stem_lower = fname[:-4].lower()
            if stem_lower == leaf_lower or stem_lower == san_lower[:-4]:
                return os.path.join(export_dir, fname)
    return ""


def _read_template_exported_farfield(project_path: str, item_path: str, cut_type: str, cut_value_deg: float) -> dict:
    """直接读取 Template Based Post-Processing 导出的 farfield 文件，无需 CST 在线操作。"""
    found_path = _find_template_exported_farfield(project_path, item_path)
    if not found_path:
        return {"success": False, "message": "未找到模板自动导出文件"}
    export_result = {"success": True, "output_path": found_path, "item_path": item_path}
    built = _build_exported_farfield_result(item_path, cut_type, cut_value_deg, export_result)
    if built.get("success"):
        built["export_backend"] = "cst_template_post_processing"
    return built


def _export_farfield_from_cst_online(reader, cst, item_path: str, cut_type: str, cut_value_deg: float) -> dict:
    if cst is None or not hasattr(cst, "export_farfield_ascii"):
        return {
            "success": False,
            "message": "当前运行时没有可用的 CST 在线 farfield 导出能力。",
            "type": "farfield_export_unavailable",
            "item": item_path,
        }
    if not getattr(cst, "project_path", ""):
        return _missing_project_path()

    export_dir = _build_farfield_export_dir(cst.project_path)
    file_stem = _sanitize_farfield_filename(item_path)
    numeric_output_path = os.path.join(export_dir, f"{file_stem}.getlist.txt")
    if hasattr(cst, "get_farfield_numeric"):
        numeric_result = cst.get_farfield_numeric(
            item_path=item_path,
            output_path=numeric_output_path,
            theta_step_deg=5.0,
            phi_step_deg=5.0,
            plot_mode="gain",
            use_db=True,
            timeout=120,
        )
        if numeric_result.get("success"):
            built_result = _build_numeric_farfield_result(item_path, cut_type, cut_value_deg, numeric_result)
            if built_result.get("success"):
                return built_result
        elif numeric_result.get("message"):
            numeric_failure_message = numeric_result.get("message", "")
        else:
            numeric_failure_message = ""
    else:
        numeric_failure_message = ""

    output_path = os.path.join(export_dir, f"{file_stem}.txt")
    export_result = cst.export_farfield_ascii(
        item_path=item_path,
        output_path=output_path,
        theta_step_deg=5.0,
        phi_step_deg=5.0,
        plot_mode="gain",
        use_db=True,
        timeout=120,
    )
    if not export_result.get("success"):
        message = f"CST 在线导出 farfield 失败：{export_result.get('message', '')}"
        if numeric_failure_message:
            message = f"GetList 数值提取失败（{numeric_failure_message}）；{message}"
        return {
            "success": False,
            "message": message,
            "type": "farfield_export_failed",
            "item": item_path,
            "export": export_result,
        }
    return _build_exported_farfield_result(item_path, cut_type, cut_value_deg, export_result)


def open_project_results(reader, project_path: str) -> dict:
    if not project_path:
        return _missing_project_path()
    return reader.open(project_path)


def ensure_project_results_open(reader, project_path: Optional[str]) -> dict:
    if project_path:
        return open_project_results(reader, project_path)
    if _has_open_results(reader):
        return {"success": True, "message": "已使用当前打开的结果文件"}
    return _missing_project_path()


def list_project_results(
    reader,
    project_path: Optional[str],
    *,
    offset: int = 0,
    limit: int = 50,
    category: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    open_result = ensure_project_results_open(reader, project_path)
    if not open_result.get("success"):
        return open_result
    kwargs = {}
    if offset != 0:
        kwargs["offset"] = offset
    if limit != 50:
        kwargs["limit"] = limit
    if category is not None:
        kwargs["category"] = category
    if query is not None:
        kwargs["query"] = query
    return reader.list_results(**kwargs)


def read_project_result(
    reader,
    project_path: Optional[str],
    item_path: str,
    *,
    max_points: Optional[int] = None,
) -> dict:
    open_result = ensure_project_results_open(reader, project_path)
    if not open_result.get("success"):
        return open_result
    if max_points is None:
        return reader.read_result(item_path)
    return reader.read_result(item_path, max_points=max_points)


def read_s11(
    reader,
    project_path: Optional[str],
    port_i: int = 1,
    port_j: int = 1,
    *,
    max_points: Optional[int] = None,
) -> dict:
    open_result = ensure_project_results_open(reader, project_path)
    if not open_result.get("success"):
        return open_result
    if max_points is None:
        return reader.get_s_parameter(port_i, port_j)
    return reader.get_s_parameter(port_i, port_j, max_points=max_points)


def validate_ascii_export_path(raw_path: str) -> str:
    """Require a new absolute .txt/.csv artifact on D: and refuse overwrite."""

    text = str(raw_path or "").strip().strip('"')
    if not text:
        raise ValueError("结果导出路径不能为空")
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("结果导出路径必须是绝对路径")
    normalized = Path(os.path.normpath(str(path)))
    if normalized.drive.upper() != "D:":
        raise ValueError("结果导出文件必须位于 D 盘")
    if normalized.suffix.lower() not in {".txt", ".csv"}:
        raise ValueError("结果导出文件必须以 .txt 或 .csv 结尾")
    if normalized.exists():
        raise ValueError(f"目标导出文件已存在，拒绝覆盖: {normalized}")
    return str(normalized)


def export_project_result_ascii(
    cst,
    item_path: str,
    output_path: str,
    *,
    timeout: int = 120,
) -> dict:
    """Export one selected result through the official CST ASCIIExport API."""

    if not getattr(cst, "project_path", ""):
        return _missing_project_path()
    try:
        normalized_output = validate_ascii_export_path(output_path)
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc),
            "error_type": "invalid_export_path",
            "result_kind": ResultKind.ARTIFACT_REF.value,
        }
    try:
        Path(normalized_output).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "success": False,
            "message": f"无法创建结果导出目录: {exc}",
            "error_type": "export_directory_error",
            "result_kind": ResultKind.ARTIFACT_REF.value,
        }

    export = cst.export_result_ascii(item_path, normalized_output, timeout=timeout)
    payload = {
        **export,
        "item": item_path.replace("/", "\\").strip(),
        "output_path": normalized_output,
        "result_kind": ResultKind.ARTIFACT_REF.value,
    }
    if payload.get("success"):
        output = Path(normalized_output)
        if not output.is_file() or output.stat().st_size <= 0:
            return {
                **payload,
                "success": False,
                "message": "CST 返回导出成功，但输出文件不存在或为空",
                "error_type": "result_export_missing",
            }
        payload["artifact"] = {
            "path": normalized_output,
            "bytes": output.stat().st_size,
            "format": output.suffix.lower().lstrip("."),
        }
        payload["message"] = f"结果已导出到 D 盘: {normalized_output}"
    return payload


def list_farfield_plot_candidates(reader, project_path: Optional[str]) -> dict:
    open_result = ensure_project_results_open(reader, project_path)
    if not open_result.get("success"):
        return open_result

    farfield_list = reader.list_farfield_results()
    if not farfield_list.get("success"):
        return farfield_list

    items = farfield_list.get("items", [])
    plot_items = farfield_list.get("plot_items") or []
    candidates = []
    for item in plot_items or items:
        lower_item = item.lower()
        if "1d" in lower_item or "theta" in lower_item or "phi" in lower_item:
            candidates.append(item)

    if not candidates:
        candidates = plot_items or items

    return {
        "success": True,
        "message": f"找到 {len(candidates)} 个可尝试绘制的远场结果项",
        "items": candidates,
        "plot_items": plot_items,
        "raw_monitor_items": farfield_list.get("raw_monitor_items", []),
        "total": len(candidates),
    }


def read_farfield_result(reader, project_path: Optional[str], item_path: Optional[str] = None, cut_type: str = "phi", cut_value_deg: float = 0.0, cst=None) -> dict:
    open_result = ensure_project_results_open(reader, project_path)
    if not open_result.get("success"):
        return open_result

    farfield_list = list_farfield_plot_candidates(reader, project_path)
    if not farfield_list.get("success"):
        return farfield_list

    items = farfield_list.get("items", [])
    plot_items = farfield_list.get("plot_items") or []
    raw_monitor_items = farfield_list.get("raw_monitor_items") or []
    if not items:
        # cst.results 找不到 farfield，但可能有模板导出文件
        proj_for_template = getattr(cst, "project_path", "") if cst is not None else ""
        if not proj_for_template:
            proj_for_template = project_path or ""
        if proj_for_template:
            template_result = _read_template_exported_farfield(proj_for_template, item_path or "", cut_type, cut_value_deg)
            if not template_result.get("success"):
                # Try scanning Export/Farfield/ for any .txt
                import re as _re
                project_root, _ = os.path.splitext(proj_for_template)
                export_dir = os.path.join(project_root, "Export", "Farfield")
                if os.path.isdir(export_dir):
                    txts = [f for f in os.listdir(export_dir) if f.endswith(".txt")]
                    if txts:
                        found = os.path.join(export_dir, txts[0])
                        freq_m = _re.search(r"f=([\d.]+)", txts[0])
                        guessed_item = f"Farfields\\farfield (f={freq_m.group(1)}) [1]" if freq_m else txts[0]
                        template_result = _read_template_exported_farfield(proj_for_template, guessed_item, cut_type, cut_value_deg)
                        if not template_result.get("success"):
                            export_result = {"success": True, "output_path": found, "item_path": guessed_item}
                            template_result = _build_exported_farfield_result(guessed_item, cut_type, cut_value_deg, export_result)
                            if template_result.get("success"):
                                template_result["export_backend"] = "cst_template_post_processing"
            if template_result.get("success"):
                return template_result
        return {
            "success": False,
            "message": "当前工程里没有找到 farfield 结果。可能原因：还没有完成求解，或求解前没有创建 farfield monitor。",
            "items": [],
        }

    if not plot_items:
        selected_monitor = item_path if item_path in raw_monitor_items else (raw_monitor_items[0] if raw_monitor_items else None)
        if selected_monitor:
            project_path_for_template = getattr(cst, "project_path", "") if cst is not None else ""
            if not project_path_for_template:
                project_path_for_template = project_path or ""
            template_result = _read_template_exported_farfield(project_path_for_template, selected_monitor, cut_type, cut_value_deg)
            if template_result.get("success"):
                return {
                    **template_result,
                    "farfield_items": items,
                    "raw_monitor_items": raw_monitor_items,
                    "plot_items": plot_items,
                    "selected_item": selected_monitor,
                }
        if selected_monitor and cst is not None:
            exported = _export_farfield_from_cst_online(reader, cst, selected_monitor, cut_type, cut_value_deg)
            if exported.get("success"):
                return {
                    **exported,
                    "farfield_items": items,
                    "raw_monitor_items": raw_monitor_items,
                    "plot_items": plot_items,
                    "selected_item": selected_monitor,
                }
            exported.setdefault(
                "message",
                "检测到远场结果，但当前结果树中只有原始 farfield monitor，且在线导出失败。",
            )
            exported["farfield_items"] = items
            exported["raw_monitor_items"] = raw_monitor_items
            exported["plot_items"] = plot_items
            exported["selected_item"] = selected_monitor
            return exported

        message = "检测到远场结果，但当前结果树中只有原始 farfield monitor，没有可直接绘制的 1D cut。"
        if raw_monitor_items:
            message += " 当前 cst.results 路径无法把这些 monitor 直接转换成方向图；请在 CST GUI 中查看，或确认 Farfields\\1D Results 下已生成 phi/theta 切线结果。"
        return {
            "success": False,
            "message": message,
            "items": items,
            "farfield_items": items,
            "raw_monitor_items": raw_monitor_items,
            "plot_items": plot_items,
            "type": "farfield_monitor_only",
        }

    selected_item = item_path if item_path in plot_items else plot_items[0]
    read_result = reader.read_result(selected_item)
    if not read_result.get("success"):
        return {
            **read_result,
            "farfield_items": items,
            "item": selected_item,
        }

    cut_result = reader.build_farfield_cut_result(
        selected_item,
        read_result,
        cut_type=cut_type,
        cut_value_deg=cut_value_deg,
    )

    if not cut_result.get("success"):
        return {
            **read_result,
            "farfield_items": items,
            "item": selected_item,
            "requested_cut_type": cut_type,
            "requested_cut_value_deg": cut_value_deg,
        }

    return {
        **cut_result,
        "farfield_items": items,
        "item": selected_item,
    }
