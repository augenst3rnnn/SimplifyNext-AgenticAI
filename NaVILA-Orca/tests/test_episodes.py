from __future__ import annotations

import json

import numpy as np
import pytest

from navila_orca.episodes import load_episode


def _write_scenario(path, *, include_goal: bool = True) -> None:
    payload = {
        "episode_id": "developer-demo",
        "scene_id": "industrial_warehouse",
        "instruction": "  Move toward the blue barrel.  ",
        "start_position": [1, 2, 3],
        "start_quat_wxyz": [1, 0, 0, 0],
        "goal_position": [4, 5, 6],
        "goal_radius": 0.5,
        "reference_path": [[1, 2, 3], [4, 5, 6]],
        "gt_locations": [[1, 2, 3], [2, 3, 4], [4, 5, 6]],
    }
    if not include_goal:
        payload.pop("goal_position")
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_episode_from_local_scenario(tmp_path):
    scenario = tmp_path / "episode.json"
    _write_scenario(scenario)

    episode = load_episode(scenario)

    assert episode.episode_id == "developer-demo"
    assert episode.instruction == "Move toward the blue barrel."
    assert episode.goal_radius == 0.5
    np.testing.assert_allclose(episode.goal_position, [4, 5, 6])


def test_load_episode_requires_goal_position(tmp_path):
    scenario = tmp_path / "episode.json"
    _write_scenario(scenario, include_goal=False)

    with pytest.raises(ValueError, match="goal_position"):
        load_episode(scenario)
