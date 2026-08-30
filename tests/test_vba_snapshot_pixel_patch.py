"""像素贴片 VBA snapshot 测试。

两个 case 设计：
- all_ones_4x4：所有像素都建 brick（默认主路径）
- checkerboard_4x4：覆盖 grid[i,j]==0 时跳过该像素的分支
"""
import numpy as np

from cst_agent_workbench.cst.pixel_patch import PixelPatchConfig, pixel_grid_to_vba


def _config_4x4():
    return PixelPatchConfig(
        f0_ghz=9.4,
        epsilon_r=4.4,
        substrate_thickness_mm=1.6,
        n_rows=4,
        n_cols=4,
        cell_size_mm=2.0,
        probe_radius_mm=0.5,
        conductor_thickness_mm=0.035,
        loss_tangent=0.02,
        substrate_name="FR-4 (lossy)",
    )


def test_case_all_ones_4x4(snapshot, reset_primitives):
    grid = np.ones((4, 4), dtype=int)
    vba = pixel_grid_to_vba(grid, _config_4x4())
    snapshot(vba)


def test_case_checkerboard_4x4(snapshot, reset_primitives):
    grid = (np.indices((4, 4)).sum(axis=0) % 2).astype(int)
    vba = pixel_grid_to_vba(grid, _config_4x4())
    snapshot(vba)
