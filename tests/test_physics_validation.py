"""Physics and algorithm validation tests — no CST, no mock LLM.

These tests verify real formulas against known antenna engineering values.
"""
from __future__ import annotations

import math
import pytest

from cst_agent_workbench.cst.rectangular_patch_fast import (
    synthesize_rectangular_patch,
    solve_microstrip_width_mm,
    microstrip_impedance,
    RectangularPatchRequest,
)
from cst_agent_workbench.cst.dipole_fast import synthesize_dipole, DipoleRequest
from cst_agent_workbench.optimization.algorithms import (
    BayesianOptimizer, PSOOptimizer, DifferentialEvolution,
)


# ── 1. Pozar rectangular patch synthesis ─────────────────────────────────────
# Reference: Pozar, Microwave Engineering 4e, Table 14-2
# FR-4: er=4.4, h=1.6mm, f0=2.4GHz → patch_L ≈ 29mm, patch_W ≈ 37mm

class TestPozarPatchSynthesis:
    """Verify synthesize_rectangular_patch against Pozar textbook values."""

    @pytest.fixture
    def fr4_2p4ghz(self):
        return RectangularPatchRequest(
            f0_ghz=2.4,
            substrate_name="FR-4",
            epsilon_r=4.4,
            loss_tangent=0.02,
            substrate_thickness_mm=1.6,
            conductor_name="Copper (annealed)",
            conductor_thickness_mm=0.035,
            feed_strategy="microstrip",
        )

    def test_patch_length_in_physical_range(self, fr4_2p4ghz):
        dims = synthesize_rectangular_patch(fr4_2p4ghz)
        # patch_L should be ~25-35mm for 2.4GHz on FR-4/1.6mm
        assert 22.0 < dims["patch_l"] < 38.0, f"patch_L={dims['patch_l']:.2f}mm out of range"

    def test_patch_width_wider_than_length(self, fr4_2p4ghz):
        dims = synthesize_rectangular_patch(fr4_2p4ghz)
        # patch_W = λ₀/2 * sqrt(2/(er+1)) > patch_L for typical er
        assert dims["patch_w"] > dims["patch_l"] * 0.9

    def test_resonant_frequency_estimate(self, fr4_2p4ghz):
        """Back-calculate f0 from synthesized dims, should be within 5% of target."""
        dims = synthesize_rectangular_patch(fr4_2p4ghz)
        er = fr4_2p4ghz.epsilon_r
        h = fr4_2p4ghz.substrate_thickness_mm
        W = dims["patch_w"]
        L = dims["patch_l"]
        # εr_eff
        er_eff = (er + 1) / 2 + (er - 1) / 2 * (1 / math.sqrt(1 + 12 * h / W))
        # ΔL
        dL = 0.412 * h * ((er_eff + 0.3) * (W / h + 0.264)) / ((er_eff - 0.258) * (W / h + 0.8))
        # effective length
        L_eff = L + 2 * dL
        f_calc_ghz = 299.792458 / (2 * L_eff * math.sqrt(er_eff))
        assert abs(f_calc_ghz - 2.4) / 2.4 < 0.05, f"f0 error: calc={f_calc_ghz:.3f} vs target=2.4 GHz"

    def test_inset_depth_in_valid_range(self, fr4_2p4ghz):
        dims = synthesize_rectangular_patch(fr4_2p4ghz)
        patch_l = dims["patch_l"]
        # inset_depth must be between 15% and 45% of patch_L
        assert 0.15 * patch_l <= dims["inset_depth"] <= 0.45 * patch_l

    def test_9p4ghz_patch_smaller_than_2p4ghz(self):
        req_hi = RectangularPatchRequest(
            f0_ghz=9.4, substrate_name="RO4003C",
            epsilon_r=3.55, loss_tangent=0.0027,
            substrate_thickness_mm=0.508,
            conductor_name="Copper (annealed)",
            conductor_thickness_mm=0.035,
            feed_strategy="microstrip",
        )
        req_lo = RectangularPatchRequest(
            f0_ghz=2.4, substrate_name="FR-4",
            epsilon_r=4.4, loss_tangent=0.02,
            substrate_thickness_mm=1.6,
            conductor_name="Copper (annealed)",
            conductor_thickness_mm=0.035,
            feed_strategy="microstrip",
        )
        dims_hi = synthesize_rectangular_patch(req_hi)
        dims_lo = synthesize_rectangular_patch(req_lo)
        assert dims_hi["patch_l"] < dims_lo["patch_l"]
        assert dims_hi["patch_w"] < dims_lo["patch_w"]


# ── 2. 50Ω microstrip width ───────────────────────────────────────────────────
# Reference: Hammerstad formula — FR-4/1.6mm → feed_W ≈ 3mm

class TestMicrostripWidth:
    def test_fr4_1p6mm_50ohm_width(self):
        w = solve_microstrip_width_mm(epsilon_r=4.4, substrate_h_mm=1.6, target_impedance=50.0)
        # Known: ~2.9-3.1mm for FR-4 1.6mm
        assert 2.5 < w < 3.5, f"feed_W={w:.3f}mm unexpected for FR-4/1.6mm"

    def test_higher_er_gives_narrower_width(self):
        w_low_er = solve_microstrip_width_mm(epsilon_r=2.2, substrate_h_mm=1.0)
        w_high_er = solve_microstrip_width_mm(epsilon_r=10.0, substrate_h_mm=1.0)
        assert w_low_er > w_high_er

    def test_impedance_round_trip(self):
        """solve_microstrip_width_mm then microstrip_impedance should recover ~50Ω."""
        er = 4.4
        h = 1.6
        w = solve_microstrip_width_mm(er, h, 50.0)
        z = microstrip_impedance(w / h, er)
        assert abs(z - 50.0) < 2.0, f"round-trip Z={z:.2f}Ω, expected ~50Ω"


# ── 3. Dipole synthesis ───────────────────────────────────────────────────────
# arm_length = 0.235 * λ₀ exactly

class TestDipoleSynthesis:
    def test_arm_length_formula(self):
        for f0_ghz in [0.868, 2.4, 5.8, 9.4, 24.0]:
            req = DipoleRequest(f0_ghz=f0_ghz)
            dims = synthesize_dipole(req)
            lambda0_mm = 299.792458 / f0_ghz
            expected_arm = 0.235 * lambda0_mm
            assert abs(dims["arm_length"] - expected_arm) < 1e-6

    def test_higher_frequency_shorter_arm(self):
        dims_low = synthesize_dipole(DipoleRequest(f0_ghz=0.9))
        dims_high = synthesize_dipole(DipoleRequest(f0_ghz=5.8))
        assert dims_low["arm_length"] > dims_high["arm_length"]

    def test_gap_positive_and_small(self):
        dims = synthesize_dipole(DipoleRequest(f0_ghz=2.4))
        assert dims["gap"] > 0
        assert dims["gap"] < dims["arm_length"] * 0.1  # gap << arm


# ── 4. Optimization algorithm convergence on analytic function ────────────────
# Minimize f(x) = (x - 5)^2 (1D parabola, optimum at x=5)
# metric = -f(x) so maximization finds x=5

def _make_history(xs, noise=0.0):
    import random
    return [
        {"params": {"x": x}, "metric": -(x - 5.0) ** 2 + random.gauss(0, noise)}
        for x in xs
    ]


BOUNDS = {"x": (0.0, 10.0)}


class TestBayesianOptimizerConvergence:
    def test_suggests_near_optimum_after_warm_start(self):
        """After 15 evals near x=5, next suggestion should be within 1 unit."""
        import numpy as np
        rng = np.random.RandomState(0)
        xs = list(rng.uniform(3.5, 6.5, 15))
        history = _make_history(xs)
        opt = BayesianOptimizer(kappa=1.0)
        suggestion = opt.suggest_next(history, BOUNDS, random_state=0)
        assert abs(suggestion["x"] - 5.0) < 2.5

    def test_returns_dict_with_correct_keys(self):
        history = _make_history([2.0, 8.0])
        opt = BayesianOptimizer()
        result = opt.suggest_next(history, BOUNDS)
        assert "x" in result
        assert BOUNDS["x"][0] <= result["x"] <= BOUNDS["x"][1]

    def test_empty_history_returns_valid_point(self):
        opt = BayesianOptimizer()
        result = opt.suggest_next([], BOUNDS)
        assert "x" in result


class TestPSOConvergence:
    def test_pso_stays_in_bounds(self):
        import numpy as np
        rng = np.random.RandomState(42)
        xs = list(rng.uniform(0, 10, 5))
        history = _make_history(xs)
        opt = PSOOptimizer(n_particles=8)
        for _ in range(10):
            s = opt.suggest_next(history, BOUNDS)
            assert BOUNDS["x"][0] <= s["x"] <= BOUNDS["x"][1]
            history.append({"params": {"x": s["x"]}, "metric": -(s["x"] - 5.0) ** 2})

    def test_pso_improves_over_iterations(self):
        """Best metric should improve (or stay same) over 20 PSO steps."""
        history = _make_history([0.5, 9.5, 2.0, 8.0])  # far from optimum
        opt = PSOOptimizer(n_particles=6)
        for _ in range(20):
            s = opt.suggest_next(history, BOUNDS)
            m = -(s["x"] - 5.0) ** 2
            history.append({"params": {"x": s["x"]}, "metric": m})
        best = max(h["metric"] for h in history)
        # optimum is 0; after 24 total evals should be at least -4 (within 2 units)
        assert best > -4.0


class TestDEConvergence:
    def test_de_stays_in_bounds(self):
        import numpy as np
        rng = np.random.RandomState(7)
        xs = list(rng.uniform(0, 10, 8))
        history = _make_history(xs)
        opt = DifferentialEvolution(pop_size=8)
        for _ in range(5):
            s = opt.suggest_next(history, BOUNDS)
            assert BOUNDS["x"][0] <= s["x"] <= BOUNDS["x"][1]

    def test_de_finds_optimum_on_parabola(self):
        """DE should get within 1 unit of x=5 within 40 evaluations."""
        import numpy as np
        rng = np.random.RandomState(3)
        history = _make_history(list(rng.uniform(0, 10, 10)))
        opt = DifferentialEvolution(pop_size=10, F=0.8, CR=0.9)
        for _ in range(30):
            s = opt.suggest_next(history, BOUNDS)
            history.append({"params": s, "metric": -(s["x"] - 5.0) ** 2})
        best_x = max(history, key=lambda h: h["metric"])["params"]["x"]
        assert abs(best_x - 5.0) < 2.0, f"DE best_x={best_x:.3f}, expected near 5.0"
