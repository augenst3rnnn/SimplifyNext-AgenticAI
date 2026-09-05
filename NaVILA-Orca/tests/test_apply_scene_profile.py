from __future__ import annotations

from types import SimpleNamespace

import pytest

from navila_orca.apply_scene_profile import (
    _assert_applied,
    _selected_options,
)


def test_selected_options_keeps_only_profile_owned_fields() -> None:
    response = SimpleNamespace(
        timestep=0.005,
        integrator=3,
        gravity=(0.0, 0.0, -9.81),
        density=0.0,
        viscosity=0.0,
        wind=(0.0, 0.0, 0.0),
        iterations=10,
        ls_iterations=20,
        noslip_iterations=0,
        ccd_iterations=50,
        sdf_initpoints=40,
        sdf_iterations=10,
        tolerance=1e-8,
        ls_tolerance=1e-2,
        noslip_tolerance=1e-6,
        ccd_tolerance=1e-6,
        solver=99,
    )

    selected = _selected_options(response)

    assert selected["gravity"] == [0.0, 0.0, -9.81]
    assert selected["wind"] == [0.0, 0.0, 0.0]
    assert "solver" not in selected


def test_assert_applied_accepts_float_rounding_and_rejects_mismatch() -> None:
    expected = {"timestep": 0.005, "gravity": (0.0, 0.0, -9.81)}
    _assert_applied(
        {"timestep": 0.0050000001, "gravity": [0.0, 0.0, -9.81]}, expected
    )

    with pytest.raises(RuntimeError, match="iterations"):
        _assert_applied({"iterations": 20}, {"iterations": 10})
