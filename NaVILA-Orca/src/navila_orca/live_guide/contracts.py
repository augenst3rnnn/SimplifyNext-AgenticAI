"""Typed route-map, mission, and safety contracts for live guidance."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from string import Formatter
from typing import Any, Mapping


def _required_text(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _positive_optional(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be positive and finite when provided")
    return number


def _non_negative_optional(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be non-negative and finite when provided")
    return number


@dataclass(frozen=True, slots=True)
class RouteNode:
    """A named location at which a live-guidance route may start or stop."""

    node_id: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _required_text(self.node_id, "node_id"))
        object.__setattr__(self, "name", _required_text(self.name, "node name"))


@dataclass(frozen=True, slots=True)
class RouteEdge:
    """One directed map segment with explicit robot and person feasibility."""

    edge_id: str
    start: str
    end: str
    distance_m: float
    instruction: str
    robot_passable: bool = True
    person_passable: bool = True
    step_free: bool = False
    clear_width_m: float | None = None
    slope_ratio: float | None = None
    surface: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge_id", _required_text(self.edge_id, "edge_id"))
        object.__setattr__(self, "start", _required_text(self.start, "edge start"))
        object.__setattr__(self, "end", _required_text(self.end, "edge end"))
        distance = float(self.distance_m)
        if not math.isfinite(distance) or distance <= 0.0:
            raise ValueError("distance_m must be positive and finite")
        object.__setattr__(self, "distance_m", distance)
        object.__setattr__(
            self, "instruction", _required_text(self.instruction, "edge instruction")
        )
        if not isinstance(self.robot_passable, bool):
            raise TypeError("robot_passable must be a bool")
        if not isinstance(self.person_passable, bool):
            raise TypeError("person_passable must be a bool")
        if not isinstance(self.step_free, bool):
            raise TypeError("step_free must be a bool")
        object.__setattr__(
            self,
            "clear_width_m",
            _positive_optional(self.clear_width_m, "clear_width_m"),
        )
        if self.slope_ratio is not None:
            slope = float(self.slope_ratio)
            if not math.isfinite(slope) or slope < 0.0:
                raise ValueError("slope_ratio must be non-negative and finite")
            object.__setattr__(self, "slope_ratio", slope)
        surface = str(self.surface).strip().lower() or "unknown"
        object.__setattr__(self, "surface", surface)


@dataclass(frozen=True, slots=True)
class RouteMap:
    """A validated directed graph used only for live route guidance."""

    map_id: str
    nodes: tuple[RouteNode, ...]
    edges: tuple[RouteEdge, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "map_id", _required_text(self.map_id, "map_id"))
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        if not self.nodes:
            raise ValueError("route map must contain at least one node")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node IDs must be unique")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("edge IDs must be unique")
        known_nodes = set(node_ids)
        for edge in self.edges:
            if edge.start not in known_nodes or edge.end not in known_nodes:
                raise ValueError(
                    f"edge {edge.edge_id!r} has an unknown endpoint: "
                    f"{edge.start!r} -> {edge.end!r}"
                )
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class MobilityConstraints:
    """Hard person-mobility constraints; no score or preference is inferred."""

    require_step_free: bool = False
    min_clear_width_m: float | None = None
    max_slope_ratio: float | None = None
    prohibited_surfaces: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.require_step_free, bool):
            raise TypeError("require_step_free must be a bool")
        object.__setattr__(
            self,
            "min_clear_width_m",
            _positive_optional(self.min_clear_width_m, "min_clear_width_m"),
        )
        object.__setattr__(
            self,
            "max_slope_ratio",
            _non_negative_optional(self.max_slope_ratio, "max_slope_ratio"),
        )
        surfaces = frozenset(
            _required_text(value, "prohibited surface").lower()
            for value in self.prohibited_surfaces
        )
        object.__setattr__(self, "prohibited_surfaces", surfaces)


@dataclass(frozen=True, slots=True)
class MissionRequest:
    """One person's current location, destination, and hard constraints."""

    current_location: str
    destination: str
    constraints: MobilityConstraints = field(default_factory=MobilityConstraints)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "current_location",
            _required_text(self.current_location, "current_location"),
        )
        object.__setattr__(
            self, "destination", _required_text(self.destination, "destination")
        )
        if not isinstance(self.constraints, MobilityConstraints):
            raise TypeError("constraints must be MobilityConstraints")


@dataclass(frozen=True, slots=True)
class MissionLimits:
    """Fail-closed execution limits for one guidance mission."""

    max_steps: int
    timeout_s: float
    max_observation_age_s: float

    def __post_init__(self) -> None:
        if isinstance(self.max_steps, bool) or int(self.max_steps) != self.max_steps:
            raise ValueError("max_steps must be a positive integer")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        object.__setattr__(self, "max_steps", int(self.max_steps))
        for name in ("timeout_s", "max_observation_age_s"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class RouteSegment:
    """Authored local instruction data, independent of robot observations."""

    segment_id: str
    start_node: str
    end_node: str
    landmark: str
    direction: str
    instruction_template: str = "{direction} toward {landmark}. Stop at {end_node}."

    def __post_init__(self) -> None:
        for name in ("segment_id", "start_node", "end_node", "landmark", "direction",
                     "instruction_template"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        allowed = {"start_node", "end_node", "landmark", "direction"}
        for _, name, spec, conversion in Formatter().parse(self.instruction_template):
            if name is not None and (name not in allowed or spec or conversion):
                raise ValueError("instruction template contains an unsupported field")

    def instruction(self) -> str:
        return self.instruction_template.format(
            start_node=self.start_node, end_node=self.end_node,
            landmark=self.landmark, direction=self.direction,
        )


@dataclass(frozen=True, slots=True)
class GuidedRoute:
    """An explicit ordered route, never a path inferred by LiveGuide."""

    route_id: str
    destination: str
    segments: tuple[RouteSegment, ...]
    accessibility_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.route_id, str) or not isinstance(self.destination, str):
            raise ValueError("route_id and destination must be text")
        object.__setattr__(self, "route_id", _required_text(self.route_id, "route_id"))
        object.__setattr__(self, "destination", _required_text(self.destination, "destination"))
        object.__setattr__(self, "segments", tuple(self.segments))
        if not self.segments or not all(isinstance(s, RouteSegment) for s in self.segments):
            raise ValueError("guided route requires structured segments")
        if len({s.segment_id for s in self.segments}) != len(self.segments):
            raise ValueError("segment IDs must be unique within a route")
        if any(a.end_node != b.start_node for a, b in zip(self.segments, self.segments[1:])):
            raise ValueError("route segments must be contiguous")
        if self.segments[-1].end_node != self.destination:
            raise ValueError("last segment must end at the route destination")
        object.__setattr__(self, "accessibility_metadata", dict(self.accessibility_metadata))


@dataclass(frozen=True, slots=True)
class RouteCatalog:
    nodes: tuple[RouteNode, ...]
    routes: tuple[GuidedRoute, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "routes", tuple(self.routes))
        nodes = {node.node_id for node in self.nodes}
        if not nodes or len(nodes) != len(self.nodes):
            raise ValueError("catalog requires unique nodes")
        if not self.routes or len({r.route_id for r in self.routes}) != len(self.routes):
            raise ValueError("catalog requires unique routes")
        for route in self.routes:
            if any(s.start_node not in nodes or s.end_node not in nodes for s in route.segments):
                raise ValueError("route segment has an unknown endpoint")
        object.__setattr__(self, "metadata", dict(self.metadata))
