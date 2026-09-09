from dataclasses import replace
from types import SimpleNamespace
import json

import numpy as np
import pytest

from navila_orca.contracts import NavigationGuardDecision, PhysicsStep
from navila_orca.live_guide import ApprovedRouteSession, ProgressConfirmation
from navila_orca.routeproof import (
    ApprovedRoute, AlwaysClearDetector, LocalFacilitiesTicketStore, RoutePlan,
    RouteProofAgent, RouteProofStatus,
)
from navila_orca.runner import NavigationRunner
from test_live_guide_session import catalog
from test_runner import FakePhysics, FakeRenderer, ScriptedVLM, _episode


class MovingPhysics(FakePhysics):
    def __init__(self):
        super().__init__()
        self.resets = 0

    def reset(self, episode):
        self.resets += 1
        state = super().reset(episode)
        self.state = replace(state, root_quat_wxyz=episode.start_quat_wxyz)
        self._sync_qpos()
        return self.state

    def step(self):
        state = super().step()
        yaw = 0.7 + state.step_id * 0.01
        self.state = replace(state, root_quat_wxyz=np.array([np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)]))
        self._sync_qpos()
        return self.state

    def _sync_qpos(self):
        self.qpos_batch[0, :3] = self.state.root_pos_world
        self.qpos_batch[0, 3:7] = self.state.root_quat_wxyz


class OperatorEvents:
    """Explicit scripted confirmations solely for deterministic tests."""
    def __init__(self, physics, callback=None):
        self.physics = physics
        self.callback = callback
        self.requests = []
        self.entry_states = []

    def wait(self, request, *, timeout_s, on_poll):
        assert timeout_s > 0
        assert self.physics.command.vx == self.physics.command.wz == 0
        self.requests.append(request)
        if request.kind == "entry":
            self.entry_states.append(self.physics.state)
        on_poll()
        if self.callback:
            self.callback(request)
        return ProgressConfirmation(request, f"operator-event-{len(self.requests)}", "test operator")


def setup(tmp_path, outputs, *, detector=None, decisions=20, steps=200, callback=None, renderer=None):
    physics = MovingPhysics()
    guide = ApprovedRouteSession(catalog(), frozenset({"a", "b"}))
    events = OperatorEvents(physics, callback)
    agent = RouteProofAgent(RoutePlan("goal", (ApprovedRoute("a"), ApprovedRoute("b"))),
                            detector or AlwaysClearDetector(), LocalFacilitiesTicketStore(tmp_path),
                            guide=guide, progress_source=events)
    vlm = ScriptedVLM(outputs)
    runner = NavigationRunner(physics, renderer or FakeRenderer(), vlm, scene_fidelity=False,
                              max_decisions=decisions, max_control_steps=steps,
                              instruction_provider=agent.current_instruction, navigation_guard=agent)
    return agent, runner, events, vlm


def test_routeproof_selects_ids_and_requires_each_segment_confirmation(tmp_path):
    agent, runner, events, vlm = setup(tmp_path, ["move forward 25 cm", "stop", "stop"])
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.VERIFIED
    assert result.verified_route_id == "a"
    assert runner.physics.resets == 1
    assert [(r.kind, r.segment_id) for r in events.requests] == [("entry", "a1"), ("complete", "a1"), ("complete", "a2")]
    assert [r[1] for r in vlm.requests] == [catalog().routes[0].segments[i].instruction() for i in (0, 0, 1)]
    assert result.attempts[0].decisions == 3
    assert all(s["confirmed"] for s in result.attempts[0].segments)


class BlockAfterMovement:
    def __init__(self, physics):
        self.physics = physics
        self.blocked_state = None

    def detect(self, images, *, route_id):
        blocked = route_id == "a" and self.physics.state.step_id >= 25
        if blocked:
            self.blocked_state = self.physics.state
        return NavigationGuardDecision(blocked, reason="operator reports blocked junction")


def test_moving_reroute_preserves_backend_render_anchor_actor_and_pose(tmp_path, monkeypatch):
    from navila_orca.orcalab_runtime.batch_render import OrcaLabBatchRenderer
    from navila_orca.render.orca import OrcaLabRenderBridge

    published = []
    mapped = []
    mapped_times = []
    created = []

    def connect(renderer):
        renderer.combined_model = SimpleNamespace(qpos0=np.zeros(20))
        renderer.combined_model.qpos0[-1] = 42.0  # An unchanged non-robot scene coordinate.
        async def update(qpos, sim_time):
            mapped.append(qpos.copy())
            mapped_times.append(sim_time)
        renderer.gym = SimpleNamespace(update_local_env=update)

    monkeypatch.setattr(OrcaLabBatchRenderer, "_connect", connect)
    monkeypatch.setattr(OrcaLabBatchRenderer, "_publish_actors", lambda _: published.append(True))
    monkeypatch.setattr(OrcaLabBatchRenderer, "_apply_remote_scene_options", lambda _: None)
    monkeypatch.setattr(OrcaLabBatchRenderer, "_build_alignment_report", lambda _: {})
    monkeypatch.setattr(OrcaLabBatchRenderer, "_build_layout", lambda _: SimpleNamespace(
        src_index=np.arange(19), dst_index=np.arange(19)[None, :],
        root_anchor=np.array([[10, -3, .3, np.cos(.5), 0, 0, np.sin(.5)]]),
        root_offset=np.zeros((1, 3)),
    ))

    def factory(**kwargs):
        renderer = OrcaLabBatchRenderer(**kwargs)
        created.append(renderer)
        return renderer

    class Camera:
        def __init__(self, *args):
            self.frame = 0
        def start(self):
            pass
        def stop(self):
            pass
        def get_frame(self, **kwargs):
            self.frame += 1
            return np.zeros((8, 8, 3), dtype=np.uint8), self.frame

    bridge = OrcaLabRenderBridge(orcagym_address="test:1", camera_port=7070,
                                 joint_qpos_addr={}, publish=False, anchor_to_scene=True,
                                 renderer_factory=factory, camera_factory=Camera)
    agent, runner, events, _ = setup(tmp_path, ["move forward 25 cm", "stop"], renderer=bridge)
    detector = BlockAfterMovement(runner.physics)
    agent.detector = detector
    evidence_stop_states = []
    original_save = agent.ticket_store.save_evidence
    def save(**kwargs):
        evidence_stop_states.append(runner.physics.command.vx)
        return original_save(**kwargs)
    monkeypatch.setattr(agent.ticket_store, "save_evidence", save)
    episode = replace(_episode(), start_position=np.array([2., -1., .3]),
                      start_quat_wxyz=np.array([np.cos(.35), 0, 0, np.sin(.35)]))
    try:
        result = agent.verify(runner, episode)
        assert result.status is RouteProofStatus.VERIFIED
        assert [a.outcome for a in result.attempts] == ["blocked", "verified"]
        assert evidence_stop_states == [0.0]
        assert runner.physics.resets == 1
        assert len(created) == 1
        assert published == []
        assert bridge._renderer is created[0]
        np.testing.assert_allclose(events.entry_states[1].root_pos_world, [2.25, -1, .3])
        assert events.entry_states[1] is detector.blocked_state
        np.testing.assert_array_equal(result.last_run.final_state.root_quat_wxyz, detector.blocked_state.root_quat_wxyz)
        np.testing.assert_array_equal(created[0]._source_root_reference[0, :3], episode.start_position)
        assert all(frame[-1] == 42 for frame in mapped)
        final_poses = [frame for frame, stamp in zip(mapped, mapped_times)
                       if stamp == detector.blocked_state.sim_time_s]
        assert len(final_poses) > 1
        assert not np.array_equal(final_poses[0][:7], mapped[0][:7])
        for frame in final_poses[1:]:
            np.testing.assert_array_equal(frame, final_poses[0])
    finally:
        bridge.close()


def test_no_safe_route_is_stopped_and_escalated_after_bounded_attempts(tmp_path):
    from test_routeproof import RouteAwareDetector
    agent, runner, events, vlm = setup(tmp_path, [], detector=RouteAwareDetector({"a", "b"}))
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert [a.route_id for a in result.attempts] == ["a", "b"]
    assert runner.physics.resets == 1
    assert vlm.requests == []
    assert runner.physics.command.vx == runner.physics.command.wz == 0
    ticket = json.loads(open(result.ticket.ticket_path).read())
    assert len(ticket["evidence"]) == 2


def test_stop_without_confirmation_never_verifies_or_moves_to_next_segment(tmp_path):
    def refuse(request):
        if request.kind == "complete":
            raise TimeoutError("operator has not confirmed arrival")
    agent, runner, events, vlm = setup(tmp_path, ["move forward 25 cm", "stop"], callback=refuse)
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert result.attempts[0].termination_reason == "guidance_failed"
    assert agent.guide.segment_index == 0
    assert runner.physics.resets == 1
    assert runner.physics.command.vx == 0
    assert len(vlm.requests) == 2


@pytest.mark.parametrize("decisions,steps,outputs", [
    (1, 200, ["stop"]),
    (20, 30, ["move forward 25 cm", "stop", "move forward 25 cm"]),
])
def test_segment_changes_do_not_replenish_mission_budgets(tmp_path, decisions, steps, outputs):
    agent, runner, events, vlm = setup(tmp_path, outputs, decisions=decisions, steps=steps)
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert sum(a.decisions for a in result.attempts) <= decisions
    assert sum(a.control_steps for a in result.attempts) <= steps
    assert runner.physics.resets == 1
    assert runner.physics.command.vx == 0


def test_invalid_approval_or_unbounded_mission_rejected_before_reset(tmp_path):
    agent, runner, events, _ = setup(tmp_path, [], decisions=0)
    with pytest.raises(ValueError, match="finite"):
        agent.verify(runner, _episode())
    assert runner.physics.resets == 0
    with pytest.raises(ValueError, match="approval set"):
        RouteProofAgent(RoutePlan("goal", (ApprovedRoute("a"),)), AlwaysClearDetector(),
                        LocalFacilitiesTicketStore(tmp_path),
                        guide=ApprovedRouteSession(catalog(), frozenset({"a", "b"})), progress_source=events)


def test_auto_reset_terminal_world_is_not_used_for_an_alternate(tmp_path):
    agent, runner, _, _ = setup(tmp_path, ["move forward 25 cm"])
    original_step = runner.physics.step
    runner.physics.step = lambda: PhysicsStep(original_step(), terminated=True, info={"auto_reset_state": True})
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert len(result.attempts) == 1
    assert result.last_run.termination_reason == "terminated"
    assert runner.physics.resets == 1


def test_blockage_preempts_a_completion_confirmation_without_motion(tmp_path):
    from test_routeproof import RouteAwareDetector
    detector = RouteAwareDetector(set())
    def event(request):
        if request.kind == "complete" and request.route_id == "a":
            detector.blocked_routes.add("a")
    agent, runner, events, _ = setup(tmp_path, ["stop", "stop"], detector=detector, callback=event)
    result = agent.verify(runner, _episode())
    assert [a.outcome for a in result.attempts] == ["blocked", "verified"]
    assert result.attempts[0].segments[0]["confirmed"] is False
    assert runner.physics.state.step_id == 0


def test_inference_error_after_motion_stops_and_reports_current_pose(tmp_path):
    agent, runner, _, vlm = setup(tmp_path, ["move forward 25 cm", "unsupported motion"])
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert result.last_run.termination_reason == "guidance_failed"
    assert runner.physics.resets == 1
    assert runner.physics.command.vx == runner.physics.command.wz == 0
    assert len(vlm.requests) == 2
    assert result.last_run.control_steps == 25
    assert result.last_run.decisions == 2
    assert result.attempts[0].segments[0]["confirmed"] is False
    np.testing.assert_allclose(result.last_run.final_state.root_pos_world, [.25, 0, 0])


def test_legacy_metric_failure_attempts_each_route_once_without_reset(tmp_path):
    physics = MovingPhysics()
    agent = RouteProofAgent(
        RoutePlan("goal", (ApprovedRoute("a", "First route"), ApprovedRoute("b", "Second route"))),
        AlwaysClearDetector(), LocalFacilitiesTicketStore(tmp_path), require_metric_success=True,
    )
    vlm = ScriptedVLM(["move forward 25 cm", "stop", "stop"])
    runner = NavigationRunner(physics, FakeRenderer(), vlm, scene_fidelity=False,
                              instruction_provider=agent.current_instruction, navigation_guard=agent)
    result = agent.verify(runner, _episode())
    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert [a.route_id for a in result.attempts] == ["a", "b"]
    assert [a.outcome for a in result.attempts] == ["navigation_failed", "navigation_failed"]
    assert physics.resets == 1
    np.testing.assert_allclose(result.last_run.final_state.root_pos_world, [.25, 0, 0])
