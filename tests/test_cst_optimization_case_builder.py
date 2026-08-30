from pathlib import Path

import pytest

from benchmarks.cst_optimization_case_builder import build_solved_case


def test_case_builder_rejects_unsafe_case_id_before_live_access(tmp_path):
    with pytest.raises(ValueError):
        build_solved_case(
            source_project=Path("D:/missing/source.cst"),
            case_root=Path("D:/cases"),
            case_id="../bad",
            patch_l=10.7,
            target_freq_ghz=9.4,
            environment={},
        )
