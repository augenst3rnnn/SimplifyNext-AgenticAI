import json

import pytest

from navila_orca.live_guide import (
    LiveGuide,
    MissionRequest,
    MobilityConstraints,
    PlannedRoute,
    load_example_campus_map,
    load_route_map,
)


def test_packaged_example_campus_map_plans_and_reroutes_one_route():
    route_map = load_example_campus_map()
    request = MissionRequest(
        "north-gate",
        "seminar-room-2",
        MobilityConstraints(
            require_step_free=True,
            min_clear_width_m=0.9,
            max_slope_ratio=0.08,
        ),
    )

    mission = LiveGuide(route_map).start(request)
    initial = mission.current_plan
    assert isinstance(initial, PlannedRoute)
    assert initial.edge_ids == (
        "gate-to-hub",
        "hub-to-lift",
        "lift-to-seminar",
    )

    rerouted = mission.block_and_reroute(
        "gate-to-hub", current_location="north-gate"
    )
    assert isinstance(rerouted, PlannedRoute)
    assert rerouted.edge_ids == (
        "gate-to-covered-walkway",
        "covered-walkway-to-lift",
        "lift-to-seminar",
    )
    assert route_map.metadata["data_status"] == "synthetic_not_surveyed"


def test_json_loader_requires_distinct_robot_and_person_passability(tmp_path):
    route_path = tmp_path / "incomplete-map.json"
    route_path.write_text(
        json.dumps(
            {
                "map_id": "incomplete",
                "nodes": [
                    {"id": "a", "name": "A"},
                    {"id": "b", "name": "B"},
                ],
                "edges": [
                    {
                        "id": "a-b",
                        "start": "a",
                        "end": "b",
                        "distance_m": 1.0,
                        "instruction": "Continue.",
                        "robot_passable": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="person_passable"):
        load_route_map(route_path)
