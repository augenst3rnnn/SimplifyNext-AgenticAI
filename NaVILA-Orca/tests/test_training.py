from __future__ import annotations

import pytest

from navila_orca.training import (
    CompatibilityError,
    compatibility_errors,
    ensure_compatible_versions,
)


COMPATIBLE = {
    "mjlab": "1.2.0",
    "mujoco-warp": "3.5.0",
    "rsl-rl-lib": "5.0.1",
    "orca-gym": "26.7.1",
    "orca-lab": "26.7.1",
}


def test_compatible_versions_are_accepted() -> None:
    assert compatibility_errors(COMPATIBLE) == ()
    ensure_compatible_versions(COMPATIBLE)


def test_unverified_orcalab_patch_releases_are_rejected() -> None:
    versions = {
        **COMPATIBLE,
        "rsl-rl-lib": "5.3.2",
        "orca-gym": "26.5.9",
        "orca-lab": "26.5.12",
    }
    with pytest.raises(CompatibilityError, match="orca-gym: found 26.5.9, expected 26.7.1"):
        ensure_compatible_versions(versions)


def test_version_error_reports_all_missing_and_mismatched_packages() -> None:
    versions = {
        "mjlab": "1.5.2",
        "mujoco-warp": "3.10.0",
        "rsl-rl-lib": "6.0.0",
        "orca-gym": "26.6.0",
        "orca-lab": None,
    }

    with pytest.raises(CompatibilityError) as caught:
        ensure_compatible_versions(versions)

    message = str(caught.value)
    assert "mjlab: found 1.5.2, expected 1.2.0" in message
    assert "mujoco-warp: found 3.10.0, expected 3.5.0" in message
    assert "rsl-rl-lib: found 6.0.0, expected 5.x" in message
    assert "orca-gym: found 26.6.0, expected 26.7.1" in message
    assert "orca-lab: not installed (expected 26.7.1)" in message
