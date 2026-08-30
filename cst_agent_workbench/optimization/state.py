class OptimizationState:
    """连续优化运行状态：追踪最佳结果、收敛检测、优化历史、参数快照。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.round = 0
        self.baseline_metric_value = None
        self.best_metric_value = None
        self.best_round = 0
        self.history = []
        self.stagnation_count = 0
        self.target_mode = "at_f0"
        self.target_freq = 0.0
        self.target_db = -10.0
        # PSO/DE 是模块级单例（为了跨轮保留粒子群状态），但不应跨优化会话
        # 泄漏——新会话重置时同步清空，避免用上一个天线的粒子群继续搜索。
        try:
            from cst_agent_workbench.optimization.algorithms import reset_algorithm_state
            reset_algorithm_state()
        except ImportError:
            pass

    @staticmethod
    def _diff_param_snapshot(prev_snapshot: dict, current_snapshot: dict) -> str:
        if not prev_snapshot:
            return "基线"

        changed_items = []
        for name in sorted(current_snapshot):
            prev_value = prev_snapshot.get(name)
            current_value = current_snapshot.get(name)
            if prev_value != current_value:
                if prev_value is None:
                    changed_items.append(f"{name}: (新增) -> {current_value}")
                else:
                    changed_items.append(f"{name}: {prev_value} -> {current_value}")

        for name in sorted(set(prev_snapshot) - set(current_snapshot)):
            changed_items.append(f"{name}: {prev_snapshot[name]} -> (删除)")

        if not changed_items:
            return "无"
        return "; ".join(changed_items)

    def record_baseline(self, check: dict, param_snapshot: dict):
        """记录基线为 round 0，作为优化起点。"""
        if self.target_mode == "at_f0" and check.get("at_f0_s11") is not None:
            metric_value = check["at_f0_s11"]
        else:
            metric_value = check.get("min_s11")

        self.baseline_metric_value = metric_value
        self.best_metric_value = metric_value
        self.best_round = 0

        self.history.append({
            "round": 0,
            "param_snapshot": dict(param_snapshot),
            "changed_params": "基线",
            "min_s11": check.get("min_s11"),
            "min_freq": check.get("min_freq"),
            "at_f0_s11": check.get("at_f0_s11"),
            "criteria_text": check.get("criteria_text", ""),
            "met": check.get("met", False),
            "improved": False,
            "metric_value": metric_value,
            "plot_data": list(check.get("plot_data") or []),
        })

    def record_round(
        self,
        check: dict,
        param_snapshot: dict,
        *,
        strategy: str = "",
        proposal_reason: str = "",
        optimizer_result: dict = None,
    ) -> bool:
        """记录一轮结果（含完整 check 和参数快照），返回是否改进。"""
        self.round += 1
        optimizer_result = optimizer_result or {}
        if self.target_mode == "at_f0" and check.get("at_f0_s11") is not None:
            metric_value = check["at_f0_s11"]
        else:
            metric_value = check.get("min_s11")

        improved = self.best_metric_value is None or (metric_value is not None and metric_value < self.best_metric_value)
        if improved and metric_value is not None:
            self.best_metric_value = metric_value
            self.best_round = self.round
            self.stagnation_count = 0
        else:
            self.stagnation_count += 1

        prev_snapshot = self.history[-1]["param_snapshot"] if self.history else {}
        changed_params = self._diff_param_snapshot(prev_snapshot, param_snapshot)

        self.history.append({
            "round": self.round,
            "param_snapshot": dict(param_snapshot),
            "changed_params": changed_params,
            "strategy": strategy,
            "proposal_reason": proposal_reason,
            "rolled_back": bool(optimizer_result.get("rolled_back")),
            "rollback_reason": optimizer_result.get("rollback_reason", ""),
            "attempted_changed_params": dict(optimizer_result.get("changed_params") or {}),
            "min_s11": check.get("min_s11"),
            "min_freq": check.get("min_freq"),
            "at_f0_s11": check.get("at_f0_s11"),
            "criteria_text": check.get("criteria_text", ""),
            "met": check.get("met", False),
            "improved": improved,
            "metric_value": metric_value,
            "plot_data": list(check.get("plot_data") or []),
        })
        return improved

    def should_stop(self, max_stagnation: int = 3) -> bool:
        return self.stagnation_count >= max_stagnation

    def get_round_record(self, round_num: int):
        """检索指定轮次的记录。"""
        for rec in self.history:
            if rec["round"] == round_num:
                return rec
        return None

    def get_best_record(self):
        """返回当前最佳轮次记录。"""
        return self.get_round_record(self.best_round)

    def get_best_param_snapshot(self) -> dict:
        """返回当前最佳轮次参数快照。"""
        record = self.get_best_record()
        if not record:
            return {}
        return dict(record.get("param_snapshot") or {})

    def get_history_df_data(self) -> list:
        """返回历史数据用于 Gradio Dataframe 渲染。"""
        rows = []
        for rec in self.history:
            strategy = rec.get("strategy", "")
            rows.append([
                rec["round"],
                strategy or "-",
                rec.get("proposal_reason", "-") or "-",
                rec.get("changed_params", "-"),
                f"{rec['min_s11']:.2f}" if rec["min_s11"] is not None else "-",
                f"{rec['min_freq']:.4f}" if rec["min_freq"] is not None else "-",
                f"{rec['at_f0_s11']:.2f}" if rec["at_f0_s11"] is not None else "-",
                "PASS" if rec["met"] else "FAIL",
                "+" if rec["improved"] else "",
                f"{rec['metric_value']:.2f}" if rec["metric_value"] is not None else "-",
            ])
        return rows

    @staticmethod
    def format_history_for_llm(rounds: list, n: int = 3) -> str:
        """把最近 n 轮格式化为 LLM 可读文字。"""
        if not rounds:
            return ""
        recent = [r for r in rounds if r.get("round", 0) > 0]
        recent = recent[-n:]
        if not recent:
            return ""
        lines = ["历史优化记录（最近几轮）:"]
        for rec in recent:
            rn = rec.get("round", "?")
            changed = rec.get("changed_params", "无")
            min_s11 = rec.get("min_s11")
            at_f0 = rec.get("at_f0_s11")
            improved = rec.get("improved", False)
            s11_str = f"{at_f0:.2f} dB" if at_f0 is not None else (f"{min_s11:.2f} dB" if min_s11 is not None else "-")
            tag = "改善" if improved else "未改善"
            lines.append(f"  轮{rn}: 改动={changed} | S11={s11_str} | {tag}")
        return "\n".join(lines)

    @property
    def rounds(self) -> list:
        """history 的别名，供外部按 rounds 引用。"""
        return self.history

    def get_summary_text(self) -> str:
        if not self.history:
            return "尚未开始优化"
        mode_label = "目标频率处 S11" if self.target_mode == "at_f0" else "最小 S11"
        lines = [f"**优化摘要** ({self.round} 轮)"]
        if self.baseline_metric_value is not None:
            lines.append(f"- 基线 {mode_label}: {self.baseline_metric_value:.2f} dB")
        if self.best_metric_value is not None:
            lines.append(f"- 最佳 {mode_label}: {self.best_metric_value:.2f} dB (第 {self.best_round} 轮)")
        latest = self.history[-1]
        if latest.get("strategy"):
            lines.append(f"- 最近策略: {latest['strategy']}")
        if latest.get("proposal_reason"):
            lines.append(f"- 最近策略说明: {latest['proposal_reason']}")
        if self.baseline_metric_value is not None and self.best_metric_value is not None:
            improvement = self.baseline_metric_value - self.best_metric_value
            lines.append(f"- 总改进 ({mode_label}): {improvement:.2f} dB")
        lines.append(f"- 停滞轮数: {self.stagnation_count}")
        return "\n".join(lines)
