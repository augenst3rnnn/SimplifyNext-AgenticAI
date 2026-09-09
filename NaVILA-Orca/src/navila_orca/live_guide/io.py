"""JSON loading for live-guide route maps and packaged fixture data."""

from __future__ import annotations

from importlib import resources
import json
from pathlib import Path
from typing import Any

from .contracts import GuidedRoute, RouteCatalog, RouteEdge, RouteMap, RouteNode, RouteSegment


def load_route_map(path: str | Path) -> RouteMap:
    """Load a live-guide route map from a JSON file."""

    route_path = Path(path).expanduser().resolve()
    try:
        content = route_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read route map {route_path}: {exc}") from exc
    return _route_map_from_json(content, source=str(route_path))


def load_example_campus_map() -> RouteMap:
    """Load the packaged synthetic, non-surveyed campus route fixture."""

    fixture = resources.files("navila_orca.live_guide").joinpath(
        "fixtures/example_campus_map.json"
    )
    return _route_map_from_json(
        fixture.read_text(encoding="utf-8"), source="packaged example campus map"
    )


def _route_map_from_json(content: str, *, source: str) -> RouteMap:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"route map {source} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("route map must be a JSON object")
    nodes_raw = _object_list(raw.get("nodes"), "nodes")
    edges_raw = _object_list(raw.get("edges"), "edges")

    nodes = tuple(
        RouteNode(node_id=node.get("id", ""), name=node.get("name", ""))
        for node in nodes_raw
    )
    edges: list[RouteEdge] = []
    for index, edge in enumerate(edges_raw):
        for field_name in ("robot_passable", "person_passable"):
            if field_name not in edge:
                raise ValueError(f"edge {index + 1} must define {field_name}")
        edges.append(
            RouteEdge(
                edge_id=edge.get("id", ""),
                start=edge.get("start", ""),
                end=edge.get("end", ""),
                distance_m=edge.get("distance_m", 0.0),
                instruction=edge.get("instruction", ""),
                robot_passable=edge["robot_passable"],
                person_passable=edge["person_passable"],
                step_free=edge.get("step_free", False),
                clear_width_m=edge.get("clear_width_m"),
                slope_ratio=edge.get("slope_ratio"),
                surface=edge.get("surface", "unknown"),
            )
        )
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("route map metadata must be a JSON object")
    return RouteMap(
        map_id=raw.get("map_id", ""),
        nodes=nodes,
        edges=tuple(edges),
        metadata=metadata,
    )


def _object_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"route map field {field_name!r} must be a list")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"route map field {field_name!r} must contain JSON objects")
    return value


def load_route_catalog(path: str | Path) -> RouteCatalog:
    """Load authored route sequences; membership here does not grant approval."""
    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load route catalog {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("route catalog must be an object")
    nodes = tuple(RouteNode(n.get("id", ""), n.get("name", ""))
                  for n in _object_list(raw.get("nodes"), "nodes"))
    routes = []
    try:
        for route in _object_list(raw.get("routes"), "routes"):
            segments = tuple(RouteSegment(**segment) for segment in
                             _object_list(route.get("segments"), "segments"))
            routes.append(GuidedRoute(route.get("id", ""), route.get("destination", ""),
                                      segments, route.get("accessibility_metadata", {})))
        return RouteCatalog(nodes, tuple(routes), raw.get("metadata", {}))
    except TypeError as exc:
        raise ValueError(f"invalid structured route catalog: {exc}") from exc
