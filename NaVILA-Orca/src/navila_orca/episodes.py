"""Load project-owned navigation scenarios without IsaacLab datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import EpisodeSpec
from .paths import DEFAULT_DEMO_EPISODE


DEFAULT_SCENARIO = DEFAULT_DEMO_EPISODE


def load_episode(path: str | Path = DEFAULT_SCENARIO) -> EpisodeSpec:
    """Load one explicit scenario JSON supplied with this project."""

    scenario_path = Path(path).expanduser().resolve()
    with scenario_path.open(encoding="utf-8") as stream:
        raw: dict[str, Any] = json.load(stream)
    required = (
        "episode_id",
        "scene_id",
        "instruction",
        "start_position",
        "start_quat_wxyz",
        "goal_position",
        "goal_radius",
        "reference_path",
        "gt_locations",
    )
    missing = tuple(name for name in required if name not in raw)
    if missing:
        raise ValueError(
            f"scenario {scenario_path} is missing required field(s): {', '.join(missing)}"
        )
    return EpisodeSpec(
        episode_id=str(raw["episode_id"]),
        scene_id=str(raw["scene_id"]),
        instruction=str(raw["instruction"]).strip(),
        start_position=_vector(raw["start_position"], 3, "start_position"),
        start_quat_wxyz=_vector(raw["start_quat_wxyz"], 4, "start_quat_wxyz"),
        goal_position=_vector(raw["goal_position"], 3, "goal_position"),
        goal_radius=float(raw["goal_radius"]),
        reference_path=_xyz_array(raw["reference_path"], "reference_path"),
        gt_locations=_xyz_array(raw["gt_locations"], "gt_locations"),
        metadata=dict(raw.get("metadata", {})),
    )


def _vector(value: Any, size: int, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a finite vector of length {size}")
    return array


def _xyz_array(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or array.shape[1:] != (3,) or len(array) == 0:
        raise ValueError(f"{name} must be a non-empty [N, 3] array")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return array
