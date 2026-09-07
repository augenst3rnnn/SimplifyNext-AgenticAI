import pytest

from navila_orca.live_guide import (
    AssistanceFallback,
    LiveGuide,
    MissionRequest,
    MobilityConstraints,
    PlannedRoute,
    RouteEdge,
    RouteMap,
    RouteNode,
)


def _campus_map(edges=None):
    nodes = tuple(
        RouteNode(node_id, name)
        for node_id, name in (
            ("entrance", "Campus entrance"),
            ("lift-lobby", "Lift lobby"),
            ("garden", "Covered garden path"),
            ("stairs", "Stairs"),
            ("classroom", "Classroom"),
        )
    )
    default_edges = (
        RouteEdge(
            "robot-only",
            "entrance",
            "classroom",
            1.0,
            "Use the service hatch.",
            robot_passable=True,
            person_passable=False,
            step_free=True,
            clear_width_m=1.2,
            slope_ratio=0.0,
            surface="smooth",
        ),
        RouteEdge(
            "stairs-in",
            "entrance",
            "stairs",
            1.0,
            "Take the stairs.",
            step_free=False,
            clear_width_m=1.2,
            slope_ratio=0.0,
            surface="smooth",
        ),
        RouteEdge(
            "stairs-out",
            "stairs",
            "classroom",
            1.0,
            "Continue from the stairs.",
            step_free=False,
            clear_width_m=1.2,
            slope_ratio=0.0,
            surface="smooth",
        ),
        RouteEdge(
            "narrow-in",
            "entrance",
            "garden",
            2.0,
            "Enter the narrow garden path.",
            step_free=True,
            clear_width_m=0.7,
            slope_ratio=0.02,
            surface="smooth",
        ),
        RouteEdge(
            "garden-out",
            "garden",
            "classroom",
            2.0,
            "Continue to the classroom.",
            step_free=True,
            clear_width_m=1.0,
            slope_ratio=0.02,
            surface="smooth",
        ),
        RouteEdge(
            "lift-in",
            "entrance",
            "lift-lobby",
            3.0,
            "Continue to the lift lobby.",
            step_free=True,
            clear_width_m=1.0,
            slope_ratio=0.03,
            surface="smooth",
        ),
        RouteEdge(
            "lift-out",
            "lift-lobby",
            "classroom",
            3.0,
            "Exit the lift lobby and continue to the classroom.",
            step_free=True,
            clear_width_m=1.0,
            slope_ratio=0.03,
            surface="smooth",
        ),
        RouteEdge(
            "garden-wide-in",
            "entrance",
            "garden",
            5.0,
            "Follow the covered path to the garden junction.",
            step_free=True,
            clear_width_m=1.0,
            slope_ratio=0.03,
            surface="smooth",
        ),
    )
    return RouteMap("campus", nodes, default_edges if edges is None else tuple(edges))


def _request():
    return MissionRequest(
        "entrance",
        "classroom",
        MobilityConstraints(
            require_step_free=True,
            min_clear_width_m=0.9,
            max_slope_ratio=0.08,
            prohibited_surfaces=frozenset({"gravel"}),
        ),
    )


def test_planner_uses_the_shortest_route_that_is_feasible_for_robot_and_person():
    mission = LiveGuide(_campus_map()).start(_request())

    assert isinstance(mission.current_plan, PlannedRoute)
    assert mission.current_plan.edge_ids == ("lift-in", "lift-out")
    assert mission.current_plan.node_ids == ("entrance", "lift-lobby", "classroom")
    assert mission.current_plan.total_distance_m == pytest.approx(6.0)
    assert [waypoint.text for waypoint in mission.current_plan.waypoints] == [
        "Continue to the lift lobby.",
        "Exit the lift lobby and continue to the classroom.",
    ]


def test_equal_length_route_choice_is_deterministic_across_input_order():
    edges = (
        RouteEdge("z1", "entrance", "lift-lobby", 1.0, "Route Z part one."),
        RouteEdge("z2", "lift-lobby", "classroom", 1.0, "Route Z part two."),
        RouteEdge("a1", "entrance", "garden", 1.0, "Route A part one."),
        RouteEdge("a2", "garden", "classroom", 1.0, "Route A part two."),
    )
    unconstrained = MissionRequest("entrance", "classroom")

    forward = LiveGuide(_campus_map(edges)).start(unconstrained).current_plan
    reversed_order = LiveGuide(_campus_map(reversed(edges))).start(
        unconstrained
    ).current_plan

    assert isinstance(forward, PlannedRoute)
    assert isinstance(reversed_order, PlannedRoute)
    assert forward.edge_ids == reversed_order.edge_ids == ("a1", "a2")


def test_blocked_edge_is_excluded_and_mission_reroutes_from_current_location():
    mission = LiveGuide(_campus_map()).start(_request())

    rerouted = mission.block_and_reroute("lift-in", current_location="entrance")

    assert isinstance(rerouted, PlannedRoute)
    assert rerouted.edge_ids == ("garden-wide-in", "garden-out")
    assert mission.blocked_edge_ids == frozenset({"lift-in"})


def test_no_feasible_route_stops_and_requests_human_assistance():
    edges = (
        RouteEdge(
            "robot-corridor",
            "entrance",
            "classroom",
            2.0,
            "Continue through the corridor.",
            robot_passable=True,
            person_passable=False,
        ),
    )

    mission = LiveGuide(_campus_map(edges)).start(_request())

    assert isinstance(mission.current_plan, AssistanceFallback)
    assert mission.current_plan.stop_required is True
    assert mission.current_plan.request_human_assistance is True
    assert mission.current_plan.current_location == "entrance"
    assert mission.current_plan.destination == "classroom"
    assert "No confirmed feasible route" in mission.current_plan.message


def test_unknown_mission_location_is_rejected_instead_of_guessed():
    with pytest.raises(ValueError, match="current location"):
        LiveGuide(_campus_map()).start(MissionRequest("car park", "classroom"))
