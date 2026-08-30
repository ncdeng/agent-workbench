from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class S11Summary:
    min_s11_db: Optional[float] = None
    min_freq_ghz: Optional[float] = None
    target_s11_db: Optional[float] = None
    target_freq_ghz: Optional[float] = None
    plot_data: Optional[List[Dict[str, float]]] = None
    bands_10db: Optional[List[Tuple[float, float]]] = None
    primary_band: Optional[Tuple[float, float]] = None
    bandwidth_ghz: Optional[float] = None
    bandwidth_pct: Optional[float] = None
    # E-1: 全部局部最小（resonances），每条带 -10dB 带宽。
    # 单谐振 patch 通常只有 1 条；multi-band / coupled-fed 设计可能多条。
    # 谐振点没跑到目标频率时，用这里能看出"实际谐振在哪"。
    resonances: Optional[List[Dict[str, Optional[float]]]] = None
    raw: Optional[Dict[str, Any]] = None

    @property
    def success(self) -> bool:
        return bool(self.plot_data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "min_s11_db": self.min_s11_db,
            "min_freq_ghz": self.min_freq_ghz,
            "target_s11_db": self.target_s11_db,
            "target_freq_ghz": self.target_freq_ghz,
            "plot_data": self.plot_data,
            "bands_10db": self.bands_10db,
            "primary_band": self.primary_band,
            "bandwidth_ghz": self.bandwidth_ghz,
            "bandwidth_pct": self.bandwidth_pct,
            "resonances": self.resonances,
            "raw": self.raw,
        }



def summarize_s11_result(s11_result: Dict[str, Any], target_freq_ghz: float = 0.0) -> S11Summary:
    plot_data = s11_result.get("plot_data") or []
    if not plot_data:
        return S11Summary(raw=s11_result, plot_data=[])

    min_point = min(plot_data, key=lambda item: item.get("s_db", float("inf")))
    target_s11_db = None
    target_freq = target_freq_ghz
    if target_freq_ghz and target_freq_ghz > 0:
        target_s11_db = interpolate_s11_at_freq(plot_data, target_freq_ghz)

    bands_10db = compute_threshold_bands(plot_data, -10.0)
    primary_band = select_primary_band(
        bands_10db,
        target_freq_ghz,
        min_point.get("freq"),
    )
    bandwidth_ghz = None
    bandwidth_pct = None
    if primary_band is not None:
        bandwidth_ghz = primary_band[1] - primary_band[0]
        center = (primary_band[0] + primary_band[1]) / 2
        bandwidth_pct = 100 * bandwidth_ghz / center if center else 0.0

    resonances = find_resonances(plot_data, bands_10db=bands_10db)

    return S11Summary(
        min_s11_db=min_point.get("s_db"),
        min_freq_ghz=min_point.get("freq"),
        target_s11_db=target_s11_db,
        target_freq_ghz=target_freq,
        plot_data=plot_data,
        bands_10db=bands_10db,
        primary_band=primary_band,
        bandwidth_ghz=bandwidth_ghz,
        bandwidth_pct=bandwidth_pct,
        resonances=resonances,
        raw=s11_result,
    )





def interpolate_s11_at_freq(plot_data: list, target_freq_ghz: float) -> Optional[float]:
    if not plot_data or target_freq_ghz <= 0:
        return None

    points = sorted(plot_data, key=lambda item: item.get("freq", 0.0))
    if not points:
        return None

    first = points[0]
    last = points[-1]
    if target_freq_ghz <= first.get("freq", 0.0):
        return first.get("s_db")
    if target_freq_ghz >= last.get("freq", 0.0):
        return last.get("s_db")

    for idx in range(len(points) - 1):
        p1 = points[idx]
        p2 = points[idx + 1]
        f1 = p1.get("freq")
        f2 = p2.get("freq")
        if f1 is None or f2 is None:
            continue
        if f1 <= target_freq_ghz <= f2:
            s1 = p1.get("s_db")
            s2 = p2.get("s_db")
            if s1 is None or s2 is None or f1 == f2:
                return s1
            ratio = (target_freq_ghz - f1) / (f2 - f1)
            return s1 + ratio * (s2 - s1)
    return None


@dataclass
class OptimizationTargetEvaluation:
    met: bool
    min_s11: Optional[float]
    min_freq: Optional[float]
    at_f0_s11: Optional[float]
    criteria_text: str
    status_text: str
    summary: S11Summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "met": self.met,
            "min_s11": self.min_s11,
            "min_freq": self.min_freq,
            "at_f0_s11": self.at_f0_s11,
            "criteria_text": self.criteria_text,
            "status_text": self.status_text,
        }



def resolve_target_frequency(
    *,
    mode: str,
    configured_target_freq: float,
    parameter_lookup: Optional[Callable[[], Dict[str, Any]]] = None,
) -> float:
    if mode != "at_f0":
        return 0.0
    if configured_target_freq and configured_target_freq > 0:
        return float(configured_target_freq)
    if parameter_lookup is None:
        return 0.0
    try:
        params = parameter_lookup() or {}
        return float(params.get("f0", 0.0))
    except (TypeError, ValueError):
        return 0.0



def format_target_description(
    *,
    mode: str,
    target_db: float,
    effective_target_freq: float,
    configured_target_freq: float = 0.0,
) -> str:
    if mode == "at_f0":
        if effective_target_freq > 0:
            suffix = " (using model f0)" if configured_target_freq <= 0 else ""
            return f"S11 at {effective_target_freq} GHz <= {target_db} dB{suffix}"
        return f"S11 at target frequency <= {target_db} dB (frequency not set)"
    return f"Minimum S11 <= {target_db} dB"



def evaluate_optimization_target(
    s11_result: Dict[str, Any],
    *,
    mode: str,
    target_db: float,
    effective_target_freq: float,
    configured_target_freq: float = 0.0,
) -> OptimizationTargetEvaluation:
    summary = summarize_s11_result(s11_result, effective_target_freq)
    criteria_text = format_target_description(
        mode=mode,
        target_db=target_db,
        effective_target_freq=effective_target_freq,
        configured_target_freq=configured_target_freq,
    )

    if not summary.success:
        return OptimizationTargetEvaluation(
            met=False,
            min_s11=None,
            min_freq=None,
            at_f0_s11=None,
            criteria_text=criteria_text,
            status_text="No data available",
            summary=summary,
        )

    min_s11 = summary.min_s11_db
    min_freq = summary.min_freq_ghz
    at_f0_s11 = summary.target_s11_db if mode == "at_f0" and effective_target_freq > 0 else None

    if mode == "at_f0" and effective_target_freq > 0:
        met = at_f0_s11 is not None and at_f0_s11 <= target_db
        icon = "PASS" if met else "FAIL"
        status_text = (
            f"S11@{effective_target_freq}GHz = {at_f0_s11:.2f} dB | {icon}"
            if at_f0_s11 is not None
            else f"Could not interpolate {effective_target_freq} GHz"
        )
    elif mode == "at_f0":
        met = False
        status_text = "Please set the target frequency (GHz)"
    else:
        met = min_s11 is not None and min_s11 <= target_db
        icon = "PASS" if met else "FAIL"
        status_text = f"Minimum S11 = {min_s11:.2f} dB | {icon}" if min_s11 is not None else "No data available"

    return OptimizationTargetEvaluation(
        met=met,
        min_s11=min_s11,
        min_freq=min_freq,
        at_f0_s11=at_f0_s11,
        criteria_text=criteria_text,
        status_text=status_text,
        summary=summary,
    )



def format_round_status(check: Dict[str, Any], opt: Any = None) -> str:
    lines = []
    if opt:
        lines.append(f"**第{opt.round}轮结果**")
    lines.append(f"- 达标判据: {check['criteria_text']}")
    lines.append(f"- 判定结果: {check['status_text']}")
    if check.get("min_s11") is not None:
        lines.append(f"- 最小 S11: {check['min_s11']:.2f} dB @ {check['min_freq']:.4f} GHz")
    if check.get("at_f0_s11") is not None:
        lines.append(f"- 目标频率处 S11: {check['at_f0_s11']:.2f} dB")
    if opt and getattr(opt, "best_metric_value", None) is not None:
        mode_label = "目标频率处 S11" if opt.target_mode == "at_f0" else "最小 S11"
        lines.append(f"- 历史最佳 ({mode_label}): {opt.best_metric_value:.2f} dB (第{opt.best_round}轮)")
    return "\n".join(lines)



def build_s11_snapshot_text(summary: S11Summary) -> str:
    if not summary.success:
        return "无可用 S11 摘要。"
    parts = [
        f"minS11={summary.min_s11_db:.2f}dB@{summary.min_freq_ghz:.3f}GHz"
        if summary.min_s11_db is not None and summary.min_freq_ghz is not None else "",
    ]
    if summary.target_s11_db is not None and summary.target_freq_ghz is not None:
        parts.append(f"target={summary.target_s11_db:.2f}dB@{summary.target_freq_ghz:.3f}GHz")
    if summary.bandwidth_ghz is not None and summary.bandwidth_pct is not None:
        parts.append(f"BW10dB={summary.bandwidth_ghz:.4f}GHz({summary.bandwidth_pct:.2f}%)")
    return " | ".join(part for part in parts if part)



def compute_threshold_bands(plot_data: list, threshold_db: float = -10.0) -> list:
    if not plot_data:
        return []

    points = sorted(plot_data, key=lambda item: item["freq"])
    bands = []
    inside = points[0]["s_db"] <= threshold_db
    start_freq = points[0]["freq"] if inside else None

    for idx in range(len(points) - 1):
        p1 = points[idx]
        p2 = points[idx + 1]
        below_1 = p1["s_db"] <= threshold_db
        below_2 = p2["s_db"] <= threshold_db

        if below_1 == below_2:
            continue

        cross_freq = interpolate_crossing_freq(p1["freq"], p1["s_db"], p2["freq"], p2["s_db"], threshold_db)
        if below_1 and not below_2:
            bands.append((start_freq if start_freq is not None else p1["freq"], cross_freq))
            start_freq = None
            inside = False
        elif (not below_1) and below_2:
            start_freq = cross_freq
            inside = True

    if inside and start_freq is not None:
        bands.append((start_freq, points[-1]["freq"]))

    return [(start_freq, end_freq) for start_freq, end_freq in bands if end_freq > start_freq]



def select_primary_band(bands: list, target_freq: float, min_freq: Optional[float]) -> Optional[tuple]:
    if not bands:
        return None
    if target_freq > 0:
        for band in bands:
            if band[0] <= target_freq <= band[1]:
                return band
    if min_freq is not None:
        for band in bands:
            if band[0] <= min_freq <= band[1]:
                return band
    return max(bands, key=lambda band: band[1] - band[0])



def interpolate_crossing_freq(freq1: float, s1: float, freq2: float, s2: float, threshold_db: float) -> float:
    if freq1 == freq2 or s1 == s2:
        return freq1
    ratio = (threshold_db - s1) / (s2 - s1)
    return freq1 + ratio * (freq2 - freq1)


def diagnose_antenna(
    s11_summary: "S11Summary",
    farfield_metrics: Optional[Dict[str, Optional[float]]] = None,
    target_freq_ghz: float = 0.0,
    target_s11_db: float = -10.0,
) -> Dict[str, Any]:
    """基于 S11Summary（E-1）和 farfield metrics（E-2）做规则式天线诊断。

    输出可被 LLM 进一步包装成自然语言回复，也可直接给 UI 显示。完全规则式
    （不调 LLM），所有判断逻辑在这里集中、可测试、可解释。

    返回：
        ok: bool — 是否所有规则通过
        issues: List[str] — 发现的问题列表
        suggestions: List[str] — 对应的修改建议（与 issues 同序对齐）
        details: Dict — 关键诊断指标（freq_offset_pct、bandwidth_pct 等），
                       便于上层做更细决策
    """
    issues: List[str] = []
    suggestions: List[str] = []
    details: Dict[str, Any] = {}

    # ── S11 部分 ────────────────────────────────────────────────────────
    if not s11_summary.success:
        issues.append("S11 数据缺失，无法判断谐振")
        suggestions.append("检查求解是否完成、结果导出是否成功")
        return {"ok": False, "issues": issues, "suggestions": suggestions, "details": details}

    resonances = s11_summary.resonances or []
    deep_resonances = [r for r in resonances if (r.get("depth_db") or 0) <= target_s11_db]
    details["resonance_count"] = len(resonances)
    details["deep_resonance_count"] = len(deep_resonances)

    if not resonances:
        issues.append("未检测到任何谐振点")
        suggestions.append(
            "检查 VBA：substrate εr / 厚度可能不对；端口位置是否在 patch 边缘；"
            "频率扫描范围是否包含理论谐振点"
        )
    elif not deep_resonances:
        worst = min((r["depth_db"] for r in resonances), default=None)
        issues.append(f"有谐振但深度不达标（最深仅 {worst:.1f} dB，要求 ≤ {target_s11_db} dB）")
        suggestions.append("阻抗匹配较差：调整馈线宽度 feed_W、inset_depth 或 probe_y")

    # 谐振点是否落在目标频率附近
    if target_freq_ghz > 0 and resonances:
        nearest = min(resonances, key=lambda r: abs(r["freq_ghz"] - target_freq_ghz))
        delta_ghz = nearest["freq_ghz"] - target_freq_ghz
        delta_pct = 100.0 * delta_ghz / target_freq_ghz
        details["nearest_resonance_ghz"] = nearest["freq_ghz"]
        details["freq_offset_pct"] = delta_pct
        if abs(delta_pct) > 5.0:
            direction = "偏高" if delta_pct > 0 else "偏低"
            issues.append(
                f"谐振点{direction} {abs(delta_pct):.1f}%（实测 {nearest['freq_ghz']:.3f} GHz "
                f"vs 目标 {target_freq_ghz:.3f} GHz）"
            )
            if delta_pct > 0:
                suggestions.append("patch 偏短，增加 patch_L 或检查有效介电常数")
            else:
                suggestions.append("patch 偏长，减小 patch_L")

    # 带宽
    if s11_summary.bandwidth_pct is not None:
        details["bandwidth_pct"] = s11_summary.bandwidth_pct
        if 0 < s11_summary.bandwidth_pct < 1.0:
            issues.append(f"带宽过窄：{s11_summary.bandwidth_pct:.2f}%")
            suggestions.append("增加基板厚度、或改用 coupled / stacked 多谐振结构")

    # ── farfield 部分 ──────────────────────────────────────────────────
    if farfield_metrics:
        peak_gain = farfield_metrics.get("peak_gain_dbi")
        f2b = farfield_metrics.get("front_to_back_db")
        hpbw = farfield_metrics.get("hpbw_deg")
        sll = farfield_metrics.get("sidelobe_level_db")
        for k, v in (("peak_gain_dbi", peak_gain), ("front_to_back_db", f2b),
                     ("hpbw_deg", hpbw), ("sidelobe_level_db", sll)):
            if v is not None:
                details[k] = v

        if f2b is not None and f2b < 10.0:
            issues.append(f"前后比偏低：{f2b:.1f} dB（一般 patch 应 ≥ 15 dB）")
            suggestions.append("地板可能过小，增大 ground_W / ground_L；或检查 z-min 边界是否为 electric")
        if peak_gain is not None and peak_gain < 4.0:
            issues.append(f"主瓣增益偏低：{peak_gain:.2f} dBi（典型 patch ≥ 5 dBi）")
            suggestions.append("可能由于损耗 / 网格过粗 / 端口失配 / 表面波")
        if sll is not None and sll > -10.0:
            issues.append(f"副瓣电平偏高：{sll:.1f} dB（≥ -10 dB 算严重）")
            suggestions.append("可能存在馈线辐射或地板共振，检查馈线长度和地板尺寸")
        if hpbw is not None and hpbw > 120.0:
            issues.append(f"波束过宽：HPBW={hpbw:.0f}°")
            suggestions.append("天线方向性弱，是否需要阵列或反射器")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "suggestions": suggestions,
        "details": details,
    }


def analyze_farfield_cut(plot_data: list) -> Dict[str, Optional[float]]:
    """分析 1D 远场 cut（角度 vs 增益 dBi），输出主瓣指标。

    输入 plot_data：list of {"angle_deg": float, "gain_dbi": float}
    （与 reader.build_farfield_cut_result 的 normalized_points 同形）。

    返回字段（None 表示数据不足以计算）：
        peak_gain_dbi: 主瓣增益峰值
        peak_angle_deg: 主瓣方向
        hpbw_deg: 3dB 波束宽度（左右两侧 gain ≥ peak-3 dB 的角度跨度）
        front_to_back_db: 前后比（peak 与 peak±180° 处增益差值）
        sidelobe_level_db: 最大副瓣相对主瓣的电平（负值，越负越好）；
                           没有副瓣时返回 None
    """
    if not plot_data or len(plot_data) < 3:
        return {
            "peak_gain_dbi": None, "peak_angle_deg": None,
            "hpbw_deg": None, "front_to_back_db": None,
            "sidelobe_level_db": None,
        }

    points = sorted(plot_data, key=lambda p: p.get("angle_deg", 0.0))
    angles = [p["angle_deg"] for p in points]
    gains = [p["gain_dbi"] for p in points]

    peak_idx = max(range(len(gains)), key=lambda i: gains[i])
    peak_gain = gains[peak_idx]
    peak_angle = angles[peak_idx]
    angle_span = angles[-1] - angles[0]
    is_circular = angle_span >= 359.0  # 0..360° 或 -180..180° 都算环绕

    # HPBW：从 peak 向左右扫描，找 gain 跨过 peak - 3 的边界
    threshold = peak_gain - 3.0
    n = len(gains)

    def _crossing(start_idx: int, step: int) -> Optional[float]:
        """从 start_idx 沿 step 方向找 gain 跨过 threshold 的角度。
        is_circular 时支持环绕一圈；否则到边界停止。"""
        max_iter = n if is_circular else n
        i = start_idx
        for _ in range(max_iter):
            i_next = i + step
            if is_circular:
                i_next %= n
            if not is_circular and not (0 <= i_next < n):
                return None
            g1, g2 = gains[i], gains[i_next]
            if g1 >= threshold > g2:
                a1, a2 = angles[i], angles[i_next]
                # 环绕情况：a1 和 a2 可能跨 0/360 边界，处理
                if is_circular and abs(a2 - a1) > 180.0:
                    a2 = a2 + 360.0 if a2 < a1 else a2 - 360.0
                if g1 == g2:
                    return a1
                ratio = (threshold - g1) / (g2 - g1)
                return a1 + ratio * (a2 - a1)
            if i_next == start_idx:
                return None  # 转一圈都没找到
            i = i_next
        return None

    right_edge = _crossing(peak_idx, 1)
    left_edge = _crossing(peak_idx, -1)
    if right_edge is not None and left_edge is not None:
        hpbw = right_edge - left_edge
        # 环绕导致 right < left（比如 right=350, left=10 → 跨 360）
        if hpbw < 0:
            hpbw += 360.0
    else:
        hpbw = None

    # 前后比：peak ± 180° 处增益（角度 mod 360 找最近点）
    back_angle = peak_angle + 180.0
    # Wrap to data's angle span
    angle_span = angles[-1] - angles[0]
    if angle_span >= 359.0:
        back_angle = ((back_angle - angles[0]) % 360.0) + angles[0]
    back_gain = None
    if angles[0] - 1 <= back_angle <= angles[-1] + 1:
        # 找最近的两点线性插值
        for i in range(len(angles) - 1):
            if angles[i] <= back_angle <= angles[i + 1]:
                if angles[i] == angles[i + 1]:
                    back_gain = gains[i]
                else:
                    ratio = (back_angle - angles[i]) / (angles[i + 1] - angles[i])
                    back_gain = gains[i] + ratio * (gains[i + 1] - gains[i])
                break
    f2b = (peak_gain - back_gain) if back_gain is not None else None

    # 副瓣电平：扫所有局部最大，排除主瓣 ±HPBW/2 范围，找下一个最大
    sidelobe_level = None
    if hpbw is not None and hpbw > 0:
        exclusion_half = hpbw / 2.0
        sidelobe_candidates = []
        for i in range(1, len(gains) - 1):
            if (gains[i] >= gains[i - 1] and gains[i] >= gains[i + 1]
                    and abs(angles[i] - peak_angle) > exclusion_half):
                sidelobe_candidates.append(gains[i])
        if sidelobe_candidates:
            sidelobe_level = max(sidelobe_candidates) - peak_gain

    return {
        "peak_gain_dbi": peak_gain,
        "peak_angle_deg": peak_angle,
        "hpbw_deg": hpbw,
        "front_to_back_db": f2b,
        "sidelobe_level_db": sidelobe_level,
    }


def find_resonances(
    plot_data: list,
    min_depth_db: float = -3.0,
    min_separation_ghz: float = 0.05,
    bands_10db: Optional[List[Tuple[float, float]]] = None,
) -> List[Dict[str, Optional[float]]]:
    """检测 S11 曲线中所有谐振点（局部最小值）。

    返回每个谐振点的频率、深度、所在 -10dB 带宽信息。multi-band 或谐振漂移到非
    目标频率时，单看 global min 看不出来；这个函数能枚举所有候选谐振，让
    diagnosis / reflection 层做更准的判断。

    参数：
        min_depth_db: 谐振深度阈值，<= 此值才算"真谐振"（默认 -3 dB）
        min_separation_ghz: 两谐振之间最小频率间隔，避免噪声尖刺重复计入
        bands_10db: 预先算好的 -10dB 带宽列表，给每个谐振分配所属带宽；
                    None 时跳过带宽分配
    """
    if not plot_data:
        return []
    points = sorted(plot_data, key=lambda p: p.get("freq", 0.0))
    if len(points) < 3:
        return []

    candidates: List[Tuple[float, float]] = []
    for i in range(1, len(points) - 1):
        prev_db = points[i - 1].get("s_db")
        curr_db = points[i].get("s_db")
        next_db = points[i + 1].get("s_db")
        if prev_db is None or curr_db is None or next_db is None:
            continue
        if curr_db <= prev_db and curr_db <= next_db and curr_db <= min_depth_db:
            candidates.append((points[i]["freq"], curr_db))

    # 去重：按深度（最深优先）扫描，过滤距已选点 < min_separation_ghz 的候选
    candidates.sort(key=lambda r: r[1])
    selected: List[Tuple[float, float]] = []
    for freq, depth in candidates:
        if all(abs(freq - sf) >= min_separation_ghz for sf, _ in selected):
            selected.append((freq, depth))
    selected.sort(key=lambda r: r[0])

    resonances: List[Dict[str, Optional[float]]] = []
    for freq, depth in selected:
        owner_band: Optional[Tuple[float, float]] = None
        if bands_10db:
            for band in bands_10db:
                if band[0] <= freq <= band[1]:
                    owner_band = band
                    break
        bw_ghz = (owner_band[1] - owner_band[0]) if owner_band else None
        bw_pct = (100.0 * bw_ghz / freq) if (bw_ghz is not None and freq > 0) else None
        resonances.append(
            {
                "freq_ghz": freq,
                "depth_db": depth,
                "bandwidth_10db_ghz": bw_ghz,
                "bandwidth_10db_pct": bw_pct,
            }
        )
    return resonances
