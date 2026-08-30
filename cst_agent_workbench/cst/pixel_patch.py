"""像素化贴片天线 — N×M 二值网格结构。

馈电方式：同轴探针馈电（coaxial probe feed），馈点固定在阵列中心，
优化像素开关状态使 S11 在目标频率处最小。

参考：Aalto University pixel antenna thesis; EDAboard CST probe-fed modeling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np


@dataclass
class PixelPatchConfig:
    f0_ghz: float
    epsilon_r: float = 4.4
    substrate_thickness_mm: float = 1.6
    n_rows: int = 8
    n_cols: int = 8
    cell_size_mm: float = 2.0
    probe_radius_mm: float = 0.5
    conductor_thickness_mm: float = 0.035
    loss_tangent: float = 0.02
    substrate_name: str = "FR-4 (lossy)"

    @property
    def grid_size_mm(self):
        return (self.n_rows * self.cell_size_mm, self.n_cols * self.cell_size_mm)

    @property
    def feed_row(self) -> int:
        return self.n_rows // 2

    @property
    def feed_col(self) -> int:
        return self.n_cols // 2

    @property
    def feed_x_mm(self) -> float:
        return (self.feed_col + 0.5) * self.cell_size_mm

    @property
    def feed_y_mm(self) -> float:
        return (self.n_rows - 1 - self.feed_row + 0.5) * self.cell_size_mm


def pixel_grid_to_vba(grid: np.ndarray, config: PixelPatchConfig) -> str:
    """将 N×M 二值矩阵转换为完整的 CST VBA 建模代码。

    结构层参数（substrate_h、copper_t、total_w、total_l、feed_x、feed_y）
    通过 store_parameter 参数化，可在 CST 参数编辑器中修改。
    像素坐标保持数值（优化变量，不需手动编辑）。
    """
    from cst_agent_workbench.cst.primitives import (
        create_material, create_brick, set_boundary,
        set_frequency_range, create_discrete_port, create_farfield_monitor,
        store_parameter, set_units,
    )

    cs = config.cell_size_mm
    h = config.substrate_thickness_mm
    t = config.conductor_thickness_mm
    n_rows, n_cols = grid.shape
    total_w = round(n_cols * cs, 4)
    total_l = round(n_rows * cs, 4)
    f0 = config.f0_ghz
    fmin = str(round(f0 * 0.7, 3))
    fmax = str(round(f0 * 1.3, 3))
    fx = round(config.feed_x_mm, 4)
    fy = round(config.feed_y_mm, 4)

    snippets: List[str] = []

    # 1. 单位
    _, units_vba = set_units("mm", "GHz", "ns")
    snippets.append(units_vba)

    # 2. 参数化关键结构尺寸（可在 CST 参数编辑器中修改）
    for name, value in [
        ("substrate_h", str(h)),
        ("copper_t", str(t)),
        ("total_w", str(total_w)),
        ("total_l", str(total_l)),
        ("feed_x", str(fx)),
        ("feed_y", str(fy)),
        ("cell_size", str(cs)),
        ("f0", str(f0)),
    ]:
        _, p_vba = store_parameter(name, value)
        snippets.append(p_vba)

    # 3. 基板材料
    _, mat_vba = create_material(
        config.substrate_name,
        epsilon=config.epsilon_r,
        tand=config.loss_tangent,
        tand_freq=f0,
    )
    snippets.append(mat_vba)

    # 4. 接地板（PEC，z: -copper_t → 0）
    _, gnd_vba = create_brick(
        name="ground", component="ground_plane", material="PEC",
        xmin="0", xmax="total_w",
        ymin="0", ymax="total_l",
        zmin="-copper_t", zmax="0",
    )
    snippets.append(gnd_vba)

    # 5. 基板（z: 0 → substrate_h）
    _, sub_vba = create_brick(
        name="substrate", component="substrate", material=config.substrate_name,
        xmin="0", xmax="total_w",
        ymin="0", ymax="total_l",
        zmin="0", zmax="substrate_h",
    )
    snippets.append(sub_vba)

    # 6. 像素导体阵列（z 用参数，xy 用数值，馈点像素强制为导体）
    for i in range(n_rows):
        for j in range(n_cols):
            is_feed = (i == config.feed_row and j == config.feed_col)
            if grid[i, j] == 1 or is_feed:
                x_min = round(j * cs, 4)
                x_max = round(x_min + cs, 4)
                y_min = round((n_rows - 1 - i) * cs, 4)
                y_max = round(y_min + cs, 4)
                name = "feed_pixel" if is_feed else f"pixel_{i}_{j}"
                _, pix_vba = create_brick(
                    name=name, component="pixel_patch", material="PEC",
                    xmin=str(x_min), xmax=str(x_max),
                    ymin=str(y_min), ymax=str(y_max),
                    zmin="substrate_h", zmax="substrate_h+copper_t",
                )
                snippets.append(pix_vba)

    # 7. 离散端口（P1 在接地板顶面 z=0，P2 在基板顶面 z=substrate_h）
    # 离散端口本身模拟同轴探针内导体，无需额外圆柱体。
    _, port_vba = create_discrete_port(
        port_number=1,
        p1_x=str(fx), p1_y=str(fy), p1_z="0",
        p2_x=str(fx), p2_y=str(fy), p2_z=str(h),
        impedance="50",
    )
    snippets.append(port_vba)

    # 8. 边界条件
    _, bnd_vba = set_boundary(
        xmin="expanded open", xmax="expanded open",
        ymin="expanded open", ymax="expanded open",
        zmin="expanded open", zmax="expanded open",
    )
    snippets.append(bnd_vba)

    # 9. 频率范围
    _, freq_vba = set_frequency_range(fmin, fmax)
    snippets.append(freq_vba)

    # 10. 远场监视器
    _, ff_vba = create_farfield_monitor(
        name=f"farfield (f={f0})",
        frequency=str(f0),
    )
    snippets.append(ff_vba)

    return "\n\n".join(snippets)


def random_initial_grid(config: PixelPatchConfig) -> np.ndarray:
    """生成随机 N×M 二值矩阵。中心 4×4 区域和馈点强制为 1。"""
    rng = np.random.default_rng()
    grid = rng.integers(0, 2, size=(config.n_rows, config.n_cols))
    row_start = max(0, config.n_rows // 2 - 2)
    col_start = max(0, config.n_cols // 2 - 2)
    grid[row_start:row_start + 4, col_start:col_start + 4] = 1
    grid[config.feed_row, config.feed_col] = 1
    return grid


def encode_grid(grid: np.ndarray) -> Dict[str, float]:
    """把 N×M 矩阵编码为 {p_i_j: 0.0/1.0, ...} 供 DE 算法使用。

    键名带下划线分隔：f"p_{i}{j}" 在网格 ≥10 行/列时会歧义
    （i=1,j=11 与 i=11,j=1 都是 p_111），造成静默键冲突。
    """
    params: Dict[str, float] = {}
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            params[f"p_{i}_{j}"] = float(grid[i, j])
    return params


def decode_grid(params: Dict[str, float], n_rows: int, n_cols: int) -> np.ndarray:
    """从 DE 参数 dict 恢复 N×M 矩阵（值 >0.5 为 1）。"""
    grid = np.zeros((n_rows, n_cols), dtype=np.int32)
    for i in range(n_rows):
        for j in range(n_cols):
            key = f"p_{i}_{j}"
            if key in params:
                grid[i, j] = 1 if params[key] > 0.5 else 0
    return grid


def get_pixel_param_bounds(config: PixelPatchConfig) -> Dict[str, tuple]:
    """返回所有像素参数的连续边界，供 DE 算法使用。"""
    return {f"p_{i}_{j}": (0.0, 1.0)
            for i in range(config.n_rows)
            for j in range(config.n_cols)}
