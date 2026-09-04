"""Load the pre-approved routes that RouteProof is allowed to try."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ApprovedRoute:
    """One accessibility-team-approved route and its NaVILA instruction."""

    route_id: str
    instruction: str
    priority: int = 0
    display_name: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        route_id = str(self.route_id).strip()
        instruction = str(self.instruction).strip()
        if not route_id:
            raise ValueError("route_id must not be empty")
        if not instruction:
            raise ValueError(f"instruction for route {route_id!r} must not be empty")
        object.__setattr__(self, "route_id", route_id)
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "display_name", str(self.display_name).strip())
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Ordered set of approved alternatives for one destination."""

    destination: str
    routes: tuple[ApprovedRoute, ...]
    requester: str = "student"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        destination = str(self.destination).strip()
        if not destination:
            raise ValueError("route-plan destination must not be empty")
        if not self.routes:
            raise ValueError("route plan must contain at least one approved route")
        route_ids = [route.route_id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route IDs must be unique")
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "requester", str(self.requester).strip() or "student")
        object.__setattr__(self, "metadata", dict(self.metadata))


def load_route_plan(path: str | Path) -> RoutePlan:
    """Load and validate a RouteProof JSON route plan."""

    route_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(route_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read route plan {route_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"route plan {route_path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("route plan must be a JSON object")
    route_values = raw.get("routes")
    if not isinstance(route_values, list) or not route_values:
        raise ValueError("route plan field 'routes' must be a non-empty list")

    routes: list[ApprovedRoute] = []
    for index, value in enumerate(route_values):
        if not isinstance(value, dict):
            raise ValueError(f"route {index + 1} must be a JSON object")
        routes.append(
            ApprovedRoute(
                route_id=value.get("id", ""),
                instruction=value.get("instruction", ""),
                priority=value.get("priority", index),
                display_name=value.get("name", ""),
                metadata=value.get("metadata", {}),
            )
        )
    routes.sort(key=lambda route: route.priority)
    return RoutePlan(
        destination=raw.get("destination", ""),
        requester=raw.get("requester", "student"),
        routes=tuple(routes),
        metadata=raw.get("metadata", {}),
    )
