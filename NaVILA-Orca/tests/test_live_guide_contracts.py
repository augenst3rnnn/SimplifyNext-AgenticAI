import pytest

from navila_orca.live_guide import (
    MissionLimits,
    MissionRequest,
    MobilityConstraints,
    RouteEdge,
    RouteMap,
    RouteNode,
)


def test_route_map_keeps_robot_and_person_passability_distinct():
    edge = RouteEdge(
        edge_id="entrance-to-lobby",
        start="entrance",
        end="lobby",
        distance_m=4.0,
        instruction="Continue from the entrance to the lobby.",
        robot_passable=True,
        person_passable=False,
    )
    route_map = RouteMap(
        map_id="campus-demo",
        nodes=(RouteNode("entrance", "Entrance"), RouteNode("lobby", "Lobby")),
        edges=(edge,),
    )

    assert route_map.edges[0].robot_passable is True
    assert route_map.edges[0].person_passable is False


def test_route_map_rejects_duplicate_ids_and_unknown_endpoints():
    with pytest.raises(ValueError, match="node IDs must be unique"):
        RouteMap(
            map_id="duplicate",
            nodes=(RouteNode("same", "One"), RouteNode("same", "Two")),
            edges=(),
        )

    with pytest.raises(ValueError, match="unknown endpoint"):
        RouteMap(
            map_id="bad-edge",
            nodes=(RouteNode("known", "Known"),),
            edges=(
                RouteEdge(
                    edge_id="missing",
                    start="known",
                    end="unknown",
                    distance_m=1.0,
                    instruction="Continue.",
                ),
            ),
        )


def test_mission_and_safety_limits_validate_at_the_boundary():
    constraints = MobilityConstraints(
        require_step_free=True,
        min_clear_width_m=0.9,
        max_slope_ratio=0.08,
        prohibited_surfaces=frozenset({"gravel"}),
    )
    request = MissionRequest(" entrance ", " classroom ", constraints)
    limits = MissionLimits(max_steps=100, timeout_s=120.0, max_observation_age_s=0.5)

    assert request.current_location == "entrance"
    assert request.destination == "classroom"
    assert limits.max_steps == 100

    with pytest.raises(ValueError, match="max_steps"):
        MissionLimits(max_steps=0, timeout_s=120.0, max_observation_age_s=0.5)


def test_level_only_zero_slope_constraint_is_valid():
    constraints = MobilityConstraints(max_slope_ratio=0.0)

    assert constraints.max_slope_ratio == 0.0
