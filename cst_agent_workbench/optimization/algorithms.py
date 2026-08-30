"""优化算法模块 — 纯数学，不依赖 CST。

统一接口：suggest_next(history, param_bounds) -> Dict[str, float]
- history: List[{"params": {name: value}, "metric": float}]（metric 越大越好，即 S11 取负值传入）
- param_bounds: Dict[str, (min, max)]
- 返回：下一组参数建议 {name: value}
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np


# ---------------------------------------------------------------------------
# 1. 贝叶斯优化（GP-UCB，纯 numpy 实现）
# ---------------------------------------------------------------------------

class BayesianOptimizer:
    """高斯过程代理模型 + UCB 采集函数。适合仿真次数 < 50 的高成本场景。"""

    def __init__(self, kappa: float = 2.0):
        self.kappa = kappa  # 探索/利用权衡

    def suggest_next(
        self,
        history: List[Dict],
        param_bounds: Dict[str, tuple],
        n_candidates: int = 200,
        random_state: int = 42,
    ) -> Dict[str, float]:
        """用 RBF 核 GP 拟合历史，UCB 选最优候选点。"""
        rng = np.random.RandomState(random_state)
        keys = list(param_bounds.keys())
        lows = np.array([param_bounds[k][0] for k in keys], dtype=float)
        highs = np.array([param_bounds[k][1] for k in keys], dtype=float)
        ranges = highs - lows
        ranges = np.where(ranges == 0, 1.0, ranges)  # 避免除零

        # 随机候选点（归一化到 [0,1]）
        candidates_norm = rng.rand(n_candidates, len(keys))
        candidates = lows + candidates_norm * ranges

        valid = [
            h for h in history
            if isinstance(h.get("params"), dict) and h.get("metric") is not None
        ]

        if len(valid) < 2:
            # 历史不足，随机采样
            idx = rng.randint(n_candidates)
            return {k: float(candidates[idx, i]) for i, k in enumerate(keys)}

        # 构建训练集（归一化）
        X_list, y_list = [], []
        for h in valid:
            row = [(float(h["params"].get(k, (lows[i] + highs[i]) / 2)) - lows[i]) / ranges[i]
                   for i, k in enumerate(keys)]
            X_list.append(row)
            y_list.append(float(h["metric"]))
        X = np.array(X_list)  # (n, d)
        y = np.array(y_list)  # (n,)

        # RBF 核：l = 0.5（归一化空间）
        l = 0.5
        sigma_n2 = 0.01

        def rbf_kernel(A: np.ndarray, B: np.ndarray) -> np.ndarray:
            # A: (m, d), B: (n, d) -> (m, n)
            diff = A[:, None, :] - B[None, :, :]  # (m, n, d)
            return np.exp(-np.sum(diff ** 2, axis=-1) / (2 * l ** 2))

        K = rbf_kernel(X, X) + sigma_n2 * np.eye(len(X))
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            # Cholesky 失败则随机
            idx = rng.randint(n_candidates)
            return {k: float(candidates[idx, i]) for i, k in enumerate(keys)}

        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))  # K^{-1} y

        # 候选点预测
        Xs = candidates_norm  # (n_candidates, d)
        Ks = rbf_kernel(Xs, X)  # (n_candidates, n)
        mu = Ks @ alpha
        v = np.linalg.solve(L, Ks.T)  # (n, n_candidates)
        var = 1.0 - np.sum(v ** 2, axis=0)  # (n_candidates,)
        var = np.maximum(var, 0.0)
        sigma = np.sqrt(var)

        ucb = mu + self.kappa * sigma
        best_idx = int(np.argmax(ucb))
        return {k: float(candidates[best_idx, i]) for i, k in enumerate(keys)}


# ---------------------------------------------------------------------------
# 2. 粒子群优化（PSO）
# ---------------------------------------------------------------------------

class PSOOptimizer:
    """粒子群优化。适合 3-6 维、预算 ≥ 30 次的场景。"""

    def __init__(self, n_particles: int = 10, w: float = 0.7, c1: float = 1.5, c2: float = 1.5):
        self.n_particles = n_particles
        self.w = w
        self.c1 = c1
        self.c2 = c2
        self._particles = None
        self._velocities = None
        self._pbest = None
        self._pbest_scores = None
        self._rng = np.random.RandomState(0)
        self._step = 0

    def suggest_next(
        self,
        history: List[Dict],
        param_bounds: Dict[str, tuple],
    ) -> Dict[str, float]:
        """根据历史初始化/更新粒子群，返回下一个评估点。"""
        keys = list(param_bounds.keys())
        d = len(keys)
        lows = np.array([param_bounds[k][0] for k in keys], dtype=float)
        highs = np.array([param_bounds[k][1] for k in keys], dtype=float)
        ranges = highs - lows
        ranges = np.where(ranges == 0, 1.0, ranges)

        # 初始化粒子群
        if self._particles is None:
            self._particles = lows + self._rng.rand(self.n_particles, d) * ranges
            self._velocities = (self._rng.rand(self.n_particles, d) - 0.5) * ranges * 0.1
            self._pbest = self._particles.copy()
            self._pbest_scores = np.full(self.n_particles, -np.inf)

        # 用历史数据同步 pbest / gbest
        valid = [
            h for h in history
            if isinstance(h.get("params"), dict) and h.get("metric") is not None
        ]
        if valid:
            for i, h in enumerate(valid[-self.n_particles:]):
                row = np.array([float(h["params"].get(k, (lows[j] + highs[j]) / 2))
                                for j, k in enumerate(keys)])
                score = float(h["metric"])
                idx = i % self.n_particles
                if score > self._pbest_scores[idx]:
                    self._pbest[idx] = row
                    self._pbest_scores[idx] = score
                    self._particles[idx] = row

        gbest_idx = int(np.argmax(self._pbest_scores))
        gbest = self._pbest[gbest_idx]

        # 更新粒子（一步）
        r1 = self._rng.rand(self.n_particles, d)
        r2 = self._rng.rand(self.n_particles, d)
        self._velocities = (
            self.w * self._velocities
            + self.c1 * r1 * (self._pbest - self._particles)
            + self.c2 * r2 * (gbest - self._particles)
        )
        self._particles = np.clip(self._particles + self._velocities, lows, highs)

        # 返回 gbest 附近的下一个粒子
        next_idx = self._step % self.n_particles
        self._step += 1
        candidate = self._particles[next_idx]
        return {k: float(candidate[i]) for i, k in enumerate(keys)}


# ---------------------------------------------------------------------------
# 3. 差分进化（DE/rand/1/bin）
# ---------------------------------------------------------------------------

class DifferentialEvolution:
    """差分进化。适合高维或离散变量场景，鲁棒性强。"""

    def __init__(self, pop_size: int = 15, F: float = 0.8, CR: float = 0.9):
        self.pop_size = pop_size
        self.F = F
        self.CR = CR
        self._population = None
        self._scores = None
        self._rng = np.random.RandomState(1)
        self._gen = 0

    def suggest_next(
        self,
        history: List[Dict],
        param_bounds: Dict[str, tuple],
    ) -> Dict[str, float]:
        """DE/rand/1/bin 变异+交叉，返回一个候选个体。"""
        keys = list(param_bounds.keys())
        d = len(keys)
        lows = np.array([param_bounds[k][0] for k in keys], dtype=float)
        highs = np.array([param_bounds[k][1] for k in keys], dtype=float)
        ranges = highs - lows
        ranges = np.where(ranges == 0, 1.0, ranges)

        # 初始化种群
        if self._population is None:
            self._population = lows + self._rng.rand(self.pop_size, d) * ranges
            self._scores = np.full(self.pop_size, -np.inf)

        # 用历史数据更新种群得分
        valid = [
            h for h in history
            if isinstance(h.get("params"), dict) and h.get("metric") is not None
        ]
        for i, h in enumerate(valid[-self.pop_size:]):
            row = np.array([float(h["params"].get(k, (lows[j] + highs[j]) / 2))
                            for j, k in enumerate(keys)])
            score = float(h["metric"])
            idx = i % self.pop_size
            if score > self._scores[idx]:
                self._population[idx] = row
                self._scores[idx] = score

        # 选 target 个体
        target_idx = self._gen % self.pop_size
        self._gen += 1
        x_target = self._population[target_idx]

        # 随机选三个不同个体（DE/rand/1）
        idxs = [i for i in range(self.pop_size) if i != target_idx]
        a_idx, b_idx, c_idx = self._rng.choice(idxs, size=3, replace=False)
        mutant = self._population[a_idx] + self.F * (self._population[b_idx] - self._population[c_idx])
        mutant = np.clip(mutant, lows, highs)

        # 二项式交叉
        cross_mask = self._rng.rand(d) < self.CR
        j_rand = self._rng.randint(d)
        cross_mask[j_rand] = True  # 保证至少一维来自 mutant
        trial = np.where(cross_mask, mutant, x_target)

        return {k: float(trial[i]) for i, k in enumerate(keys)}


# ---------------------------------------------------------------------------
# 4. 自动选择函数 & 统一入口
# ---------------------------------------------------------------------------

_BAYESIAN = BayesianOptimizer()
_PSO = PSOOptimizer()
_DE = DifferentialEvolution()


def reset_algorithm_state() -> None:
    """重建 PSO/DE 单例，清空粒子群/种群内部状态。

    这两个优化器为跨调用保留内部状态而做成模块级单例，但状态不应跨
    优化运行/天线设计泄漏；每次新优化会话开始时调用本函数。
    （BayesianOptimizer 无内部状态，无需重建。）
    """
    global _PSO, _DE
    _PSO = PSOOptimizer()
    _DE = DifferentialEvolution()


def auto_select_algorithm(history_len: int, n_params: int) -> str:
    """根据历史数量和参数维度自动推荐算法。

    Returns: 'bayesian' | 'pso' | 'de'
    """
    if history_len < 20:
        return "bayesian"  # 样本少，GP 最高效
    if n_params <= 6:
        return "pso"        # 中等维度
    return "de"             # 高维


def suggest_next_params(
    algorithm: str,
    history: List[Dict],
    param_bounds: Dict[str, tuple],
    **kwargs,
) -> Dict[str, float]:
    """统一入口。algorithm: 'bayesian'|'pso'|'de'|'auto'"""
    if algorithm == "auto":
        n_params = len(param_bounds)
        algorithm = auto_select_algorithm(len(history), n_params)

    if algorithm == "bayesian":
        return _BAYESIAN.suggest_next(history, param_bounds, **kwargs)
    if algorithm == "pso":
        return _PSO.suggest_next(history, param_bounds)
    if algorithm == "de":
        return _DE.suggest_next(history, param_bounds)
    raise ValueError(f"Unknown algorithm: {algorithm!r}. Use 'bayesian', 'pso', 'de', or 'auto'.")
