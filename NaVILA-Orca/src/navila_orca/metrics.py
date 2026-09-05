"""Engine-neutral navigation measures used by the local OrcaLab scenario."""

from __future__ import annotations

from typing import TypeAlias

import numpy as np

from .contracts import EpisodeSpec


MetricValue: TypeAlias = float | bool


class NavigationMetrics:
    """Track the six benchmark metrics from robot root positions."""

    def __init__(
        self,
        episode: EpisodeSpec,
        initial_position: np.ndarray,
        *,
        scene_fidelity: bool,
    ) -> None:
        self.episode = episode
        self.scene_fidelity = bool(scene_fidelity)
        self._gt = np.asarray(episode.gt_locations, dtype=np.float64)
        self._remaining_path = np.zeros(len(self._gt), dtype=np.float64)
        if len(self._gt) > 1:
            segments = np.linalg.norm(np.diff(self._gt, axis=0), axis=1)
            self._remaining_path[:-1] = np.cumsum(segments[::-1])[::-1]

        self._previous_position = self._position(initial_position)
        self.path_length = 0.0
        self.distance_to_goal = self._distance_to_goal(self._previous_position)
        self._start_distance = self.distance_to_goal
        self.success = 0.0
        self.spl = 0.0
        self.oracle_navigation_error = self.distance_to_goal
        self.oracle_success = float(self.distance_to_goal < self.episode.goal_radius)

    @staticmethod
    def _position(position: np.ndarray) -> np.ndarray:
        value = np.asarray(position, dtype=np.float64)
        if value.shape != (3,) or not np.all(np.isfinite(value)):
            raise ValueError(
                f"position must be finite with shape (3,), got {value.shape}"
            )
        return value.copy()

    def _distance_to_goal(self, position: np.ndarray) -> float:
        distances = np.linalg.norm(self._gt - position[None, :], axis=1)
        closest_index = int(np.argmin(distances))
        return float(distances[closest_index] + self._remaining_path[closest_index])

    def update(self, position: np.ndarray, *, stop_called: bool = False) -> None:
        current = self._position(position)
        step_distance = float(np.linalg.norm(current - self._previous_position))
        self.path_length += step_distance
        self._previous_position = current
        self.distance_to_goal = self._distance_to_goal(current)
        self.success = float(
            bool(stop_called) and self.distance_to_goal < self.episode.goal_radius
        )
        denominator = max(self._start_distance, self.path_length)
        self.spl = self.success * (
            self._start_distance / denominator if denominator > 0.0 else 1.0
        )
        self.oracle_navigation_error = min(
            self.oracle_navigation_error, self.distance_to_goal
        )
        self.oracle_success = float(
            bool(self.oracle_success)
            or self.distance_to_goal < self.episode.goal_radius
        )

    def as_dict(self) -> dict[str, MetricValue]:
        return {
            "path_length": float(self.path_length),
            "distance_to_goal": float(self.distance_to_goal),
            "success": float(self.success),
            "spl": float(self.spl),
            "oracle_navigation_error": float(self.oracle_navigation_error),
            "oracle_success": float(self.oracle_success),
            "scene_fidelity": self.scene_fidelity,
        }
