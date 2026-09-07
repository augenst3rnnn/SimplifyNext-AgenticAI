"""Deterministic hard-constraint route planning and live rerouting."""

from __future__ import annotations

from dataclasses import dataclass
import heapq

from .contracts import MissionRequest, MobilityConstraints, RouteEdge, RouteMap


@dataclass(frozen=True, slots=True)
class WaypointInstruction:
    """Instruction for reaching one node on a planned route."""

    sequence: int
    node_id: str
    text: str


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    """The shortest feasible directed path for the active mission."""

    current_location: str
    destination: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    total_distance_m: float
    waypoints: tuple[WaypointInstruction, ...]


@dataclass(frozen=True, slots=True)
class AssistanceFallback:
    """Fail-closed result when no route is confirmed feasible."""

    current_location: str
    destination: str
    message: str = (
        "No confirmed feasible route remains. Keep the robot stopped and "
        "request assistance from a nearby trained person."
    )
    stop_required: bool = True
    request_human_assistance: bool = True


GuidancePlan = PlannedRoute | AssistanceFallback


class LiveGuide:
    """Entry point for one-person campus route planning."""

    def __init__(self, route_map: RouteMap) -> None:
        if not isinstance(route_map, RouteMap):
            raise TypeError("route_map must be a RouteMap")
        self.route_map = route_map
        self._node_ids = frozenset(node.node_id for node in route_map.nodes)
        self._edge_by_id = {edge.edge_id: edge for edge in route_map.edges}

    def start(self, request: MissionRequest) -> LiveGuideMission:
        """Start a mission and compute its initial route or assistance fallback."""

        if not isinstance(request, MissionRequest):
            raise TypeError("request must be a MissionRequest")
        self._validate_node(request.current_location, "current location")
        self._validate_node(request.destination, "destination")
        return LiveGuideMission(self, request)

    def _validate_node(self, node_id: str, role: str) -> None:
        if node_id not in self._node_ids:
            raise ValueError(f"{role} {node_id!r} is not present in the route map")

    def _plan(
        self,
        request: MissionRequest,
        blocked_edge_ids: frozenset[str],
    ) -> GuidancePlan:
        adjacency: dict[str, list[RouteEdge]] = {
            node_id: [] for node_id in self._node_ids
        }
        for edge in self.route_map.edges:
            if edge.edge_id not in blocked_edge_ids and _is_feasible(
                edge, request.constraints
            ):
                adjacency[edge.start].append(edge)
        for edges in adjacency.values():
            edges.sort(key=lambda edge: (edge.edge_id, edge.end))

        start = request.current_location
        destination = request.destination
        # Tuple ordering makes equal-distance choices deterministic by edge IDs.
        frontier: list[tuple[float, tuple[str, ...], str, tuple[str, ...]]] = [
            (0.0, (), start, (start,))
        ]
        best: dict[str, tuple[float, tuple[str, ...]]] = {start: (0.0, ())}

        while frontier:
            distance, edge_ids, node_id, node_ids = heapq.heappop(frontier)
            if best.get(node_id) != (distance, edge_ids):
                continue
            if node_id == destination:
                route_edges = tuple(self._edge_by_id[edge_id] for edge_id in edge_ids)
                waypoints = tuple(
                    WaypointInstruction(index, edge.end, edge.instruction)
                    for index, edge in enumerate(route_edges, start=1)
                )
                return PlannedRoute(
                    current_location=start,
                    destination=destination,
                    node_ids=node_ids,
                    edge_ids=edge_ids,
                    total_distance_m=distance,
                    waypoints=waypoints,
                )

            for edge in adjacency[node_id]:
                candidate_edge_ids = (*edge_ids, edge.edge_id)
                candidate = (distance + edge.distance_m, candidate_edge_ids)
                previous = best.get(edge.end)
                if previous is not None and candidate >= previous:
                    continue
                best[edge.end] = candidate
                heapq.heappush(
                    frontier,
                    (candidate[0], candidate_edge_ids, edge.end, (*node_ids, edge.end)),
                )

        return AssistanceFallback(start, destination)


class LiveGuideMission:
    """Mission state that accumulates blocked segments across reroutes."""

    def __init__(self, guide: LiveGuide, request: MissionRequest) -> None:
        self._guide = guide
        self._request = request
        self._blocked_edge_ids: set[str] = set()
        self._current_plan = guide._plan(request, frozenset())

    @property
    def current_plan(self) -> GuidancePlan:
        return self._current_plan

    @property
    def blocked_edge_ids(self) -> frozenset[str]:
        return frozenset(self._blocked_edge_ids)

    def block_and_reroute(
        self,
        edge_id: str,
        *,
        current_location: str,
    ) -> GuidancePlan:
        """Exclude a blocked edge and replan from the person's current node."""

        edge_id = str(edge_id).strip()
        if edge_id not in self._guide._edge_by_id:
            raise ValueError(f"blocked edge {edge_id!r} is not present in the route map")
        current_location = str(current_location).strip()
        self._guide._validate_node(current_location, "current location")
        self._blocked_edge_ids.add(edge_id)
        self._request = MissionRequest(
            current_location,
            self._request.destination,
            self._request.constraints,
        )
        self._current_plan = self._guide._plan(
            self._request, frozenset(self._blocked_edge_ids)
        )
        return self._current_plan


def _is_feasible(edge: RouteEdge, constraints: MobilityConstraints) -> bool:
    if not edge.robot_passable or not edge.person_passable:
        return False
    if constraints.require_step_free and not edge.step_free:
        return False
    if constraints.min_clear_width_m is not None and (
        edge.clear_width_m is None
        or edge.clear_width_m < constraints.min_clear_width_m
    ):
        return False
    if constraints.max_slope_ratio is not None and (
        edge.slope_ratio is None or edge.slope_ratio > constraints.max_slope_ratio
    ):
        return False
    if edge.surface in constraints.prohibited_surfaces:
        return False
    return True
