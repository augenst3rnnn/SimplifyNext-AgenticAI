"""Obstruction-detector boundary plus a deterministic demo trigger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from PIL import Image

from ..contracts import NavigationGuardDecision


@runtime_checkable
class ObstructionDetector(Protocol):
    """A perception implementation that can be replaced by a real vision model."""

    def detect(
        self,
        images: Sequence[Image.Image],
        *,
        route_id: str,
    ) -> NavigationGuardDecision: ...


class AlwaysClearDetector:
    """Pass-through detector for exercising normal NaVILA navigation."""

    def detect(
        self,
        images: Sequence[Image.Image],
        *,
        route_id: str,
    ) -> NavigationGuardDecision:
        if not images:
            raise ValueError("obstruction detector requires at least one image")
        return NavigationGuardDecision(
            blocked=False,
            reason="No obstruction reported",
            metadata={"route_id": route_id, "detector": "always-clear"},
        )


class FlagFileObstructionDetector:
    """Edge-triggered blockage switch for an end-to-end demonstration.

    Each new or modified version of the flag reports one blockage. Leaving the
    file unchanged allows the next approved route to run. Modify it again to
    block another route. Replace this class with a camera/depth detector for
    production.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._last_consumed_signature: tuple[int, int, str] | None = None

    def detect(
        self,
        images: Sequence[Image.Image],
        *,
        route_id: str,
    ) -> NavigationGuardDecision:
        if not images:
            raise ValueError("obstruction detector requires at least one image")
        if not self.path.exists():
            return NavigationGuardDecision(
                blocked=False,
                reason="No demo obstruction reported",
                metadata={"route_id": route_id, "detector": "flag-file"},
            )

        raw_text = self.path.read_text(encoding="utf-8").strip()
        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size, raw_text)
        if signature == self._last_consumed_signature:
            return NavigationGuardDecision(
                blocked=False,
                reason="Demo obstruction event already handled",
                metadata={"route_id": route_id, "detector": "flag-file"},
            )
        payload = self._parse_payload(raw_text)
        expected_route = str(payload.get("route_id", "")).strip()
        if expected_route and expected_route != route_id:
            return NavigationGuardDecision(
                blocked=False,
                reason=f"Demo flag is reserved for route {expected_route}",
                metadata={"route_id": route_id, "detector": "flag-file"},
            )
        blocked = payload.get("blocked", True)
        if not isinstance(blocked, bool):
            raise ValueError("demo flag field 'blocked' must be true or false")
        if not blocked:
            self._last_consumed_signature = signature
            return NavigationGuardDecision(
                blocked=False,
                reason=str(payload.get("reason", "Demo flag reports clear")),
                metadata={"route_id": route_id, "detector": "flag-file"},
            )

        self._last_consumed_signature = signature
        obstacle = str(
            payload.get("obstacle", payload.get("obstacle_label", "temporary barrier"))
        ).strip()
        reason = str(
            payload.get("reason", f"{obstacle or 'obstruction'} blocks the accessible path")
        ).strip()
        confidence_value = payload.get("confidence", 1.0)
        return NavigationGuardDecision(
            blocked=True,
            reason=reason,
            obstacle_label=obstacle or "temporary barrier",
            confidence=float(confidence_value),
            metadata={
                "route_id": route_id,
                "detector": "flag-file",
                "flag_path": str(self.path),
            },
        )

    @staticmethod
    def _parse_payload(raw_text: str) -> dict[str, object]:
        if not raw_text:
            return {}
        try:
            value = json.loads(raw_text)
        except json.JSONDecodeError:
            return {"obstacle": raw_text}
        if isinstance(value, str):
            return {"obstacle": value}
        if not isinstance(value, dict):
            raise ValueError("demo flag must contain a JSON object or obstacle name")
        return value
