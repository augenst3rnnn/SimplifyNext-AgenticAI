"""Execute an approved route; route choice belongs to RouteProof."""

from __future__ import annotations

from uuid import uuid4

from ..contracts import RobotState
from .contracts import GuidedRoute, RouteCatalog, RouteSegment
from .progress import ProgressConfirmation, ProgressRequest


class ApprovedRouteSession:
    """A progress cursor over one explicitly selected approved sequence.

    This object neither owns physics nor calls the graph planner. An entry
    confirmation asserts the robot is already at the route's authored start;
    it never relocates the robot or invents a connecting path.
    """

    def __init__(self, catalog: RouteCatalog, approved_route_ids: frozenset[str]) -> None:
        self._routes = {route.route_id: route for route in catalog.routes}
        self.approved_route_ids = frozenset(approved_route_ids)
        if not self.approved_route_ids or not self.approved_route_ids <= self._routes.keys():
            raise ValueError("every approved route ID must exist in the catalog")
        self.route: GuidedRoute | None = None
        self.segment_index = 0
        self.activation_id = ""
        self._entered = False
        self._pending: ProgressRequest | None = None
        self._used_events: set[str] = set()
        self.state: RobotState | None = None

    def resolve(self, route_id: str) -> GuidedRoute:
        if route_id not in self.approved_route_ids:
            raise ValueError(f"route {route_id!r} is not approved")
        return self._routes[route_id]

    def start_route(self, route_id: str, start_state: RobotState) -> None:
        route = self.resolve(route_id)  # Validate before replacing active state.
        self.route = route
        self.segment_index = 0
        self.activation_id = uuid4().hex
        self._entered = False
        self._pending = None
        self.update_progress(start_state)
        print(f"LIVEGUIDE_ROUTE_STARTED route_id={route_id} activation_id={self.activation_id}", flush=True)

    def switch_route(self, route_id: str, current_state: RobotState) -> None:
        self.start_route(route_id, current_state)
        print(f"LIVEGUIDE_ROUTE_SWITCHED route_id={route_id} step_id={current_state.step_id}", flush=True)

    def current_segment(self) -> RouteSegment | None:
        if self.route is None or self.is_complete():
            return None
        return self.route.segments[self.segment_index]

    def is_complete(self) -> bool:
        return self.route is not None and self.segment_index == len(self.route.segments)

    def get_next_instruction(self) -> str:
        segment = self.current_segment()
        if not self._entered or segment is None or self._pending is not None:
            raise RuntimeError("local instruction requires confirmed route entry and progress")
        return segment.instruction()

    def update_progress(self, state: RobotState) -> None:
        """Record an observation; pose alone never advances the cursor."""
        if not isinstance(state, RobotState):
            raise TypeError("progress observation must be RobotState")
        self.state = state

    def request_confirmation(self, kind: str, state: RobotState) -> ProgressRequest:
        segment = self.current_segment()
        if segment is None or kind not in ("entry", "complete"):
            raise ValueError("confirmation requires an active segment and known kind")
        if (kind == "entry") == self._entered:
            raise ValueError("confirmation kind does not match route progress")
        self.update_progress(state)
        self._pending = ProgressRequest(
            uuid4().hex, self.activation_id, self.route.route_id, segment.segment_id,
            kind, segment.start_node if kind == "entry" else segment.end_node, state.step_id,
        )
        return self._pending

    def confirm(self, confirmation: ProgressConfirmation, state: RobotState) -> None:
        if not isinstance(confirmation, ProgressConfirmation):
            raise ValueError("progress requires an explicit ProgressConfirmation")
        if (self._pending is None or confirmation.request != self._pending
                or confirmation.event_id in self._used_events
                or state.step_id != self._pending.step_id):
            raise ValueError("stale, duplicate or mismatched progress confirmation")
        self.update_progress(state)
        self._used_events.add(confirmation.event_id)
        request = self._pending
        self._pending = None
        if request.kind == "entry":
            self._entered = True
        else:
            self.segment_index += 1
        print(f"LIVEGUIDE_PROGRESS route_id={request.route_id} segment_id={request.segment_id} "
              f"kind={request.kind} step_id={state.step_id} source={confirmation.source!r} "
              f"event_id={confirmation.event_id}", flush=True)
