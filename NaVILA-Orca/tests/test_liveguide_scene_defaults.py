"""UNIT TESTS: production agent/runner, fake hardware and scripted operator/VLM.

These exercise packaged scene-authored instructions and real flag-file adapters.
No OrcaLab, remote inference, measured localization or physical safety is proven.
The scripted operator declares arrival; fake motion distances do not imply nodes.
"""

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from navila_orca.live_guide import (
    ApprovedRouteSession, FlagFileProgressSource, load_route_catalog,
)
from navila_orca.routeproof import (
    FlagFileObstructionDetector, LocalFacilitiesTicketStore, RouteProofAgent,
    RouteProofStatus, load_route_plan,
)
from navila_orca.runner import NavigationRunner
from test_routeproof_live_guide import MovingPhysics
from test_runner import FakeRenderer, ScriptedVLM, _episode


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def defaults():
    return (
        load_route_plan(EXAMPLES / "liveguide_routes.json", require_instructions=False),
        load_route_catalog(EXAMPLES / "liveguide_catalog.json"),
    )


def test_default_plan_and_catalog_share_human_dummy_destination():
    plan, catalog = defaults()
    assert plan.destination == "human-dummy"
    assert [(r.route_id, r.priority) for r in plan.routes] == [
        ("main-corridor", 1), ("side-corridor", 2),
    ]
    assert {r.route_id for r in catalog.routes} == {r.route_id for r in plan.routes}
    assert all(r.destination == plan.destination for r in catalog.routes)
    assert catalog.metadata["data_status"] == "synthetic_not_surveyed"
    assert all(r.accessibility_metadata["data_status"] == "synthetic_not_surveyed"
               for r in catalog.routes)


@pytest.mark.parametrize("route_id,nodes", [
    ("main-corridor", ("robot-start", "red-bucket", "human-dummy")),
    ("side-corridor", ("red-bucket", "silver-bucket", "fire-extinguisher",
                       "blue-bin", "human-dummy")),
])
def test_default_route_starts_at_its_actual_entry_and_keeps_landmark_order(route_id, nodes):
    _, catalog = defaults()
    route = next(r for r in catalog.routes if r.route_id == route_id)
    assert (route.segments[0].start_node, *(s.end_node for s in route.segments)) == nodes
    assert all(left.end_node == right.start_node
               for left, right in zip(route.segments, route.segments[1:]))
    assert route.segments[-1].end_node == route.destination
    assert "side-start-to-red-bucket" not in {s.segment_id for s in route.segments}


def test_default_first_instruction_explicitly_stops_at_red_bucket():
    _, catalog = defaults()
    instruction = catalog.routes[0].segments[0].instruction()
    assert "the red bucket" in instruction
    assert "Keep one robot-width to its right" in instruction
    assert instruction.endswith("Stop there before proceeding to the next segment.")


class ScriptedSceneOperator:
    """Unit-test human input, through the production file-confirmation adapter."""

    def __init__(self, directory, physics, *, block_at_junction=False, refuse_side=False):
        self.physics = physics
        self.source = FlagFileProgressSource(directory / "progress.json")
        self.blockage_path = directory / "blocked.json"
        self.block_at_junction = block_at_junction
        self.refuse_side = refuse_side
        self.requests = []
        self.confirmed = []
        self.wait_states = []
        self.poll_states = []
        self.junction_state = None
        self.blocked_state = None

    def report_blockage(self):
        self.blocked_state = self.physics.state
        self.blockage_path.write_text(json.dumps({
            "route_id": "main-corridor", "obstacle": "unit-test barrier", "event": 1,
        }), encoding="utf-8")

    def wait(self, request, *, timeout_s, on_poll):
        self.requests.append(request)
        self.wait_states.append(self.physics.state)
        is_junction_stop = (request.route_id == "main-corridor"
                            and request.kind == "complete"
                            and request.segment_id == "main-start-to-red-bucket")
        if is_junction_stop:
            self.junction_state = self.physics.state

        def poll():
            self.poll_states.append((self.physics.state, self.physics.command))
            if is_junction_stop and self.block_at_junction:
                # First traversal has stopped; next segment is not released.
                # Do not confirm completion: blockage wins at this boundary.
                self.report_blockage()
            on_poll()
            if not (self.refuse_side and request.route_id == "side-corridor"):
                event = {**asdict(request), "event_id": f"unit-event-{len(self.requests)}",
                         "source": "unit-test scripted operator"}
                self.source.path.write_text(json.dumps(event), encoding="utf-8")

        event = self.source.wait(request, timeout_s=timeout_s, on_poll=poll)
        self.confirmed.append(event.request)
        return event


class BeforeJunctionRenderer(FakeRenderer):
    """Fake camera reports a barrier during movement, not node arrival."""

    def __init__(self, operator):
        super().__init__()
        self.operator = operator

    def render(self, state, qpos_batch=None):
        if state.step_id > 0 and self.operator.blocked_state is None:
            self.operator.report_blockage()
        return super().render(state, qpos_batch)


def scene_mission(tmp_path, outputs, *, block_at_junction=False, before_junction=False):
    plan, catalog = defaults()
    physics = MovingPhysics()
    operator = ScriptedSceneOperator(tmp_path, physics, block_at_junction=block_at_junction,
                                     refuse_side=before_junction)
    guide = ApprovedRouteSession(catalog, frozenset(r.route_id for r in plan.routes))
    agent = RouteProofAgent(
        plan, FlagFileObstructionDetector(operator.blockage_path),
        LocalFacilitiesTicketStore(tmp_path / "tickets"), guide=guide,
        progress_source=operator, confirmation_timeout_s=0.3,
    )
    renderer = BeforeJunctionRenderer(operator) if before_junction else FakeRenderer()
    vlm = ScriptedVLM(outputs)
    runner = NavigationRunner(
        physics, renderer, vlm, scene_fidelity=False, max_decisions=20, max_control_steps=200,
        instruction_provider=agent.current_instruction, navigation_guard=agent,
    )
    return agent, runner, operator, vlm


def assert_waits_stopped(operator):
    assert operator.poll_states
    for _, command in operator.poll_states:
        assert command.vx == command.vy == command.wz == command.duration_s == 0


def test_unit_clear_primary_keeps_explicit_entry_and_every_completion(tmp_path):
    agent, runner, operator, vlm = scene_mission(tmp_path, ["move forward 25 cm", "stop", "stop"])
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.VERIFIED
    assert result.destination == "human-dummy"
    assert result.verified_route_id == "main-corridor"
    assert [(r.kind, r.node_id) for r in operator.confirmed] == [
        ("entry", "robot-start"), ("complete", "red-bucket"), ("complete", "human-dummy"),
    ]
    assert all(s["confirmed"] for s in result.attempts[0].segments)
    main = agent.guide.resolve("main-corridor")
    assert [r[1] for r in vlm.requests] == [main.segments[i].instruction() for i in (0, 0, 1)]
    assert runner.physics.resets == 1
    assert_waits_stopped(operator)


def test_unit_reroute_from_pending_red_bucket_stop_preserves_state_without_prefix_replay(tmp_path):
    agent, runner, operator, vlm = scene_mission(
        tmp_path, ["move forward 25 cm", "stop", "stop", "stop", "stop", "stop", "stop"],
        block_at_junction=True,
    )
    physics, renderer = runner.physics, runner.renderer
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.VERIFIED
    assert result.verified_route_id == "side-corridor"
    assert [a.outcome for a in result.attempts] == ["blocked", "verified"]
    assert [(r.route_id, r.kind, r.node_id) for r in operator.requests] == [
        ("main-corridor", "entry", "robot-start"),
        ("main-corridor", "complete", "red-bucket"),
        ("side-corridor", "entry", "red-bucket"),
        ("side-corridor", "complete", "silver-bucket"),
        ("side-corridor", "complete", "fire-extinguisher"),
        ("side-corridor", "complete", "blue-bin"),
        ("side-corridor", "complete", "human-dummy"),
    ]
    assert operator.requests[1] not in operator.confirmed  # Blockage preempts completion.
    assert operator.confirmed == [operator.requests[0], *operator.requests[2:]]
    assert operator.requests[2].segment_id == "side-red-bucket-to-silver-bucket"
    assert operator.requests[0].activation_id != operator.requests[2].activation_id
    assert operator.junction_state is operator.blocked_state is operator.wait_states[2]
    assert operator.junction_state.step_id > 0
    assert operator.junction_state is result.last_run.final_state
    assert runner.physics is physics and runner.renderer is renderer
    assert physics.resets == 1
    np.testing.assert_array_equal(operator.wait_states[2].root_pos_world, operator.junction_state.root_pos_world)
    np.testing.assert_array_equal(operator.wait_states[2].root_quat_wxyz, operator.junction_state.root_quat_wxyz)
    assert_waits_stopped(operator)
    assert physics.command.vx == physics.command.wz == 0
    main, side = agent.guide.resolve("main-corridor"), agent.guide.resolve("side-corridor")
    assert [r[1] for r in vlm.requests] == [main.segments[0].instruction()] * 2 + [
        s.instruction() for s in side.segments
    ]
    assert [s["segment_id"] for s in result.attempts[0].segments] == ["main-start-to-red-bucket"]
    assert result.attempts[0].segments[0]["confirmed"] is False
    assert all(s["confirmed"] for s in result.attempts[1].segments)
    assert Path(result.attempts[0].evidence_path).is_file()


def test_unit_before_junction_block_waits_for_red_bucket_entry_then_times_out(tmp_path, monkeypatch):
    clock = [0.0]

    def advance(seconds):
        clock[0] += seconds

    # Fake only the wait clock; the real file adapter must reject the stale
    # robot-start event and reach its timeout without granting red-bucket entry.
    monkeypatch.setattr("navila_orca.live_guide.progress.time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=advance,
    ))
    agent, runner, operator, vlm = scene_mission(tmp_path, ["move forward 75 cm"], before_junction=True)
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert [a.outcome for a in result.attempts] == ["blocked", "navigation_failed"]
    assert result.attempts[1].termination_reason == "guidance_failed"
    assert [(r.kind, r.node_id) for r in operator.requests] == [
        ("entry", "robot-start"), ("entry", "red-bucket"),
    ]
    assert operator.confirmed == [operator.requests[0]]
    assert json.loads(operator.source.request_path.read_text()) == asdict(operator.requests[1])
    assert json.loads(operator.source.path.read_text())["node_id"] == "robot-start"
    assert clock[0] >= agent.confirmation_timeout_s
    assert operator.junction_state is None
    assert operator.wait_states[1] is operator.blocked_state is result.last_run.final_state
    assert len([s for s, _ in operator.poll_states if s is operator.blocked_state]) > 1
    assert runner.physics.resets == 1
    assert len(vlm.requests) == 1  # No alternate instruction without truthful entry.
    assert result.attempts[1].segments == ()
    assert runner.physics.command.vx == runner.physics.command.wz == 0
    assert_waits_stopped(operator)
    with pytest.raises(RuntimeError, match="confirmed route entry"):
        agent.current_instruction()
    ticket = json.loads(Path(result.ticket.ticket_path).read_text())
    assert ticket["destination"] == "human-dummy"
