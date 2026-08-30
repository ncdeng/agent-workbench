from __future__ import annotations

from typing import Protocol

from cst_agent_workbench.optimization.diagnosis import diagnose_s11
from cst_agent_workbench.optimization.models import (
    OptimizationContext,
    OptimizationProposal,
    ParameterUpdate,
)


class OptimizationStrategy(Protocol):
    name: str

    def propose_next_step(self, context: OptimizationContext) -> OptimizationProposal:
        ...


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_history_param(history: list[dict], param_name: str) -> float | None:
    best_metric = None
    best_value = None
    for record in history:
        snapshot = record.get("param_snapshot") or {}
        if param_name not in snapshot:
            continue
        metric = _coerce_float(record.get("metric_value"))
        value = _coerce_float(snapshot.get(param_name))
        if metric is None or value is None:
            continue
        if best_metric is None or metric < best_metric:
            best_metric = metric
            best_value = value
    return best_value


def _recent_rolled_back_param(history: list[dict], param_name: str) -> bool:
    for record in reversed(history[-4:]):
        if not record.get("rolled_back"):
            continue
        if param_name in (record.get("attempted_changed_params") or {}):
            return True
    return False


def _patch_length_updates(params: dict[str, float], patch_l: float, new_patch_l: float, inset_depth: float) -> list[ParameterUpdate]:
    return [ParameterUpdate(name="patch_L", old=patch_l, new=new_patch_l)]


def _feed_width_updates(params: dict[str, float], feed_w: float, new_feed_w: float) -> list[ParameterUpdate]:
    updates = [ParameterUpdate(name="feed_W", old=feed_w, new=new_feed_w)]
    inset_gap = _coerce_float(params.get("inset_gap"))
    notch_w = _coerce_float(params.get("notch_W"))
    if inset_gap is not None and notch_w is not None:
        updates.append(ParameterUpdate(name="notch_W", old=notch_w, new=new_feed_w + 2 * inset_gap))
    return updates


class HeuristicPatchOptimizationStrategy:
    name = "heuristic"

    def __init__(self, direction_inferer):
        self._direction_inferer = direction_inferer

    def propose_next_step(self, context: OptimizationContext) -> OptimizationProposal:
        params = context.parameters
        target = context.target
        summary = context.s11_summary

        patch_l = params["patch_L"]
        inset_depth = params["inset_depth"]
        feed_w = params["feed_W"]
        substrate_h = _coerce_float(params.get("substrate_h")) or 0.0
        min_freq = summary.min_freq_ghz
        min_s11 = summary.min_s11_db
        target_s11 = summary.target_s11_db
        effective_target_freq = target.target_freq_ghz

        if min_freq is None or target_s11 is None or effective_target_freq <= 0:
            return OptimizationProposal(
                strategy=self.name,
                handled=False,
                reason="",
                message="当前 S11 摘要不完整，无法生成调参建议。",
            )

        freq_error = (min_freq - effective_target_freq) / effective_target_freq
        diagnosis = diagnose_s11(summary, target)

        if diagnosis.recommended_parameter_family == "patch_L":
            default_sign = diagnosis.recommended_delta_sign
            scaled_patch_l = patch_l * (min_freq / effective_target_freq)
            max_delta = patch_l * 0.08
            delta = min(max(abs(scaled_patch_l - patch_l), patch_l * 0.005), max_delta)
            history_text = ""
            if _recent_rolled_back_param(context.history, "patch_L"):
                if feed_w > max(2.5, substrate_h * 2.0):
                    new_feed_w = max(0.5, feed_w * 0.70)
                    return OptimizationProposal(
                        strategy=self.name,
                        reason=(
                            f"{diagnosis.reason} 近期 patch_L 频率校正被回滚，且 feed_W={feed_w:.3f} mm 明显偏宽；"
                            "改为优先减小 feed_W 并保持 inset_depth，用馈线/缺口宽度改善目标频点匹配。"
                        ),
                        updates=_feed_width_updates(params, feed_w, new_feed_w),
                    )
                delta = min(delta, patch_l * 0.01)
                history_text = "；上一轮 patch_L 调整被回滚，本轮保持物理诊断方向但缩小步长"
            new_patch_l = patch_l + default_sign * delta
            return OptimizationProposal(
                strategy=self.name,
                reason=f"{diagnosis.reason}{history_text} 本轮只调整 patch_L，避免同时扰动 inset/feed 匹配结构。",
                updates=_patch_length_updates(params, patch_l, new_patch_l, inset_depth),
            )

        if target_s11 > target.target_db and abs(freq_error) <= 0.02:
            inset_sign = self._direction_inferer(context.history, "inset_depth", default_sign=1)
            if _recent_rolled_back_param(context.history, "inset_depth") and _recent_rolled_back_param(context.history, "feed_W") and abs(freq_error) > 0.003:
                direction_sign = 1 if freq_error > 0 else -1
                scaled_patch_l = patch_l * (min_freq / effective_target_freq)
                delta = min(max(abs(scaled_patch_l - patch_l), patch_l * 0.003), patch_l * 0.02)
                new_patch_l = patch_l + direction_sign * delta
                direction_text = "增大" if new_patch_l > patch_l else "减小"
                return OptimizationProposal(
                    strategy=self.name,
                    reason=(
                        f"inset_depth 与 feed_W 近期尝试均被回滚，当前谐振点 {min_freq:.3f} GHz 仍偏"
                        f"{'高' if freq_error > 0 else '低'}，改为小步{direction_text} patch_L，暂不联动 inset/feed 匹配结构。"
                    ),
                    updates=_patch_length_updates(params, patch_l, new_patch_l, inset_depth),
                )
            if inset_sign != 1:
                feed_step = max(0.03, feed_w * 0.04)
                feed_sign = self._direction_inferer(context.history, "feed_W", default_sign=1)
                new_feed_w = max(0.05, feed_w + feed_sign * feed_step)
                updates = []
                best_inset = _best_history_param(context.history, "inset_depth")
                if best_inset is not None and abs(best_inset - inset_depth) > 1e-12:
                    updates.append(ParameterUpdate(name="inset_depth", old=inset_depth, new=best_inset))
                updates.append(ParameterUpdate(name="feed_W", old=feed_w, new=new_feed_w))
                return OptimizationProposal(
                    strategy=self.name,
                    reason=(
                        f"当前谐振点 {min_freq:.3f} GHz 已接近目标频率，且上一轮 inset_depth 同方向匹配变差，"
                        "先回滚到历史最佳 inset_depth，再微调 feed_W 探索馈线阻抗匹配。"
                    ),
                    updates=updates,
                )
            inset_step = max(0.05, inset_depth * 0.06)
            new_inset = inset_depth + inset_sign * inset_step
            new_inset = min(max(new_inset, patch_l * 0.10), patch_l * 0.48)
            if abs(new_inset - inset_depth) > 1e-12:
                direction_text = "增大" if new_inset > inset_depth else "减小"
                return OptimizationProposal(
                    strategy=self.name,
                    reason=(
                        f"当前谐振点 {min_freq:.3f} GHz 已接近目标频率，但 S11@{effective_target_freq:.3f} GHz = "
                        f"{target_s11:.2f} dB，优先微调 inset_depth（{direction_text}）继续做匹配优化。"
                    ),
                    updates=[ParameterUpdate(name="inset_depth", old=inset_depth, new=new_inset)],
                )

        if (
            target_s11 > target.target_db
            and min_s11 is not None
            and min_s11 <= target.target_db + 3.0
            and abs(freq_error) <= 0.04
        ):
            inset_step = max(0.05, inset_depth * 0.06)
            inset_sign = self._direction_inferer(context.history, "inset_depth", default_sign=1)
            new_inset = inset_depth + inset_sign * inset_step
            new_inset = min(max(new_inset, patch_l * 0.10), patch_l * 0.48)
            if abs(new_inset - inset_depth) > 1e-12:
                return OptimizationProposal(
                    strategy=self.name,
                    reason=(
                        f"已有较深谐振点 {min_s11:.2f} dB @ {min_freq:.3f} GHz 接近目标频率，"
                        f"优先微调 inset_depth 改善 S11@{effective_target_freq:.3f} GHz 匹配。"
                    ),
                    updates=[ParameterUpdate(name="inset_depth", old=inset_depth, new=new_inset)],
                )

        if abs(freq_error) > 0.003:
            default_sign = 1 if freq_error > 0 else -1
            direction_sign = self._direction_inferer(context.history, "patch_L", default_sign=default_sign)
            scaled_patch_l = patch_l * (min_freq / effective_target_freq)
            max_delta = patch_l * 0.08
            delta = min(max(abs(scaled_patch_l - patch_l), patch_l * 0.005), max_delta)
            reversed_by_history = direction_sign != default_sign
            if reversed_by_history:
                delta = min(delta, patch_l * 0.04)
            new_patch_l = patch_l + direction_sign * delta
            direction_text = "增大" if new_patch_l > patch_l else "减小"
            history_text = "；上一轮同参数方向未改善，本轮反向缩步" if reversed_by_history else ""
            return OptimizationProposal(
                strategy=self.name,
                reason=(
                    f"当前谐振点 {min_freq:.3f} GHz 相对目标 {effective_target_freq:.3f} GHz 偏"
                    f"{'高' if freq_error > 0 else '低'}，优先通过{direction_text} patch_L 做一轮频率校正{history_text}，"
                    "暂不联动 inset/feed 匹配结构。"
                ),
                updates=_patch_length_updates(params, patch_l, new_patch_l, inset_depth),
            )

        if target_s11 > target.target_db:
            inset_step = max(0.05, inset_depth * 0.06)
            inset_sign = self._direction_inferer(context.history, "inset_depth", default_sign=1)
            new_inset = inset_depth + inset_sign * inset_step
            new_inset = min(max(new_inset, patch_l * 0.10), patch_l * 0.48)
            if abs(new_inset - inset_depth) > 1e-12:
                return OptimizationProposal(
                    strategy=self.name,
                    reason=(
                        f"当前谐振点已接近目标频率，但 S11@{effective_target_freq:.3f} GHz = "
                        f"{target_s11:.2f} dB，优先微调 inset_depth。"
                    ),
                    updates=[ParameterUpdate(name="inset_depth", old=inset_depth, new=new_inset)],
                )

            feed_step = max(0.03, feed_w * 0.05)
            feed_sign = self._direction_inferer(context.history, "feed_W", default_sign=-1)
            new_feed_w = max(0.05, feed_w + feed_sign * feed_step)
            return OptimizationProposal(
                strategy=self.name,
                reason="inset_depth 已接近可用边界，改为微调 feed_W 以改善匹配。",
                updates=[ParameterUpdate(name="feed_W", old=feed_w, new=new_feed_w)],
            )

        return OptimizationProposal(
            strategy=self.name,
            reason="",
            message=(
                f"程序化优化判断当前结果已满足目标：S11@{effective_target_freq:.3f} GHz = "
                f"{target_s11:.2f} dB。当前轮不做额外调参。"
            ),
        )
