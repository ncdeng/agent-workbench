from benchmarks.cst_dipole_mesh_convergence import evaluate_convergence


def test_mesh_convergence_uses_medium_to_fine_delta():
    cases = [
        {"lines_per_wavelength": 10, "min_freq_ghz": 5.70, "target_s11_db": -14.0, "curve_sha256": "a"},
        {"lines_per_wavelength": 15, "min_freq_ghz": 5.77, "target_s11_db": -15.5, "curve_sha256": "b"},
        {"lines_per_wavelength": 20, "min_freq_ghz": 5.78, "target_s11_db": -15.8, "curve_sha256": "c"},
    ]

    result = evaluate_convergence(cases)

    assert result["medium_lines_per_wavelength"] == 15
    assert result["fine_lines_per_wavelength"] == 20
    assert result["converged"] is True


def test_mesh_convergence_rejects_large_s11_delta():
    cases = [
        {"lines_per_wavelength": 15, "min_freq_ghz": 5.77, "target_s11_db": -14.0, "curve_sha256": "a"},
        {"lines_per_wavelength": 20, "min_freq_ghz": 5.78, "target_s11_db": -16.0, "curve_sha256": "b"},
    ]

    assert evaluate_convergence(cases)["converged"] is False


def test_mesh_convergence_is_inconclusive_for_identical_curves_without_mesh_signature():
    cases = [
        {"lines_per_wavelength": 15, "min_freq_ghz": 5.77, "target_s11_db": -15.8, "curve_sha256": "same"},
        {"lines_per_wavelength": 20, "min_freq_ghz": 5.77, "target_s11_db": -15.8, "curve_sha256": "same"},
    ]

    result = evaluate_convergence(cases)

    assert result["tolerance_met"] is True
    assert result["conclusive"] is False
    assert result["status"] == "inconclusive"
    assert result["converged"] is False


def test_mesh_convergence_can_be_conclusive_with_realized_signatures():
    cases = [
        {"lines_per_wavelength": 15, "min_freq_ghz": 5.77, "target_s11_db": -15.7, "curve_sha256": "same", "mesh_signature": {"total_cells": 1000}},
        {"lines_per_wavelength": 20, "min_freq_ghz": 5.77, "target_s11_db": -15.8, "curve_sha256": "same", "mesh_signature": {"total_cells": 1600}},
    ]

    result = evaluate_convergence(cases)

    assert result["mesh_realization_verified"] is True
    assert result["conclusive"] is True
    assert result["converged"] is True
