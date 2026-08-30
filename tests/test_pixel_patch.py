"""像素化贴片天线模块测试。"""

import numpy as np

from cst_agent_workbench.cst.pixel_patch import (
    PixelPatchConfig,
    decode_grid,
    encode_grid,
    pixel_grid_to_vba,
    random_initial_grid,
)


def test_config_grid_size_mm():
    cfg = PixelPatchConfig(f0_ghz=9.4, n_rows=8, n_cols=8, cell_size_mm=2.0)
    assert cfg.grid_size_mm == (16.0, 16.0)

    cfg2 = PixelPatchConfig(f0_ghz=5.8, n_rows=6, n_cols=10, cell_size_mm=3.0)
    assert cfg2.grid_size_mm == (18.0, 30.0)


def test_random_initial_grid_shape_and_center():
    cfg = PixelPatchConfig(f0_ghz=9.4, n_rows=8, n_cols=8)
    grid = random_initial_grid(cfg)

    assert grid.shape == (8, 8)
    assert grid.dtype in (np.int32, np.int64, int)
    assert set(np.unique(grid)).issubset({0, 1})

    # 中心 4×4 区域全为 1
    row_start = (8 - 4) // 2  # 2
    col_start = (8 - 4) // 2  # 2
    center = grid[row_start:row_start + 4, col_start:col_start + 4]
    assert np.all(center == 1), f"中心区域含 0:\n{center}"


def test_encode_decode_roundtrip():
    cfg = PixelPatchConfig(f0_ghz=9.4, n_rows=4, n_cols=4)
    rng = np.random.default_rng(42)
    original = rng.integers(0, 2, size=(4, 4), dtype=np.int32)

    params = encode_grid(original)
    assert len(params) == 16

    recovered = decode_grid(params, n_rows=4, n_cols=4)
    np.testing.assert_array_equal(original, recovered)


def test_pixel_grid_to_vba():
    cfg = PixelPatchConfig(f0_ghz=9.4, n_rows=4, n_cols=4)

    # 全 1 网格 — 应包含 16 个像素 Brick（15 个 pixel_ + 1 个 feed_pixel）
    full_grid = np.ones((4, 4), dtype=np.int32)
    vba = pixel_grid_to_vba(full_grid, cfg)
    assert isinstance(vba, str)
    assert "Brick" in vba
    assert vba.count('.Name "pixel_') == 15  # feed_pixel 单独命名
    assert '"feed_pixel"' in vba  # 馈点像素存在

    # 全 0 网格 — 馈点像素仍强制存在
    zero_grid = np.zeros((4, 4), dtype=np.int32)
    vba_empty = pixel_grid_to_vba(zero_grid, cfg)
    assert isinstance(vba_empty, str)
    assert '"feed_pixel"' in vba_empty  # 馈点像素强制存在
    assert vba.count('.Name "pixel_') + 1 == 16  # 总共 16 个像素位
