import json
from pathlib import Path

import numpy as np

from navila_orca.contracts import (
    EpisodeSpec,
    NavigationGuardDecision,
    RenderFrame,
    RobotState,
)
from navila_orca.routeproof import (
    ApprovedRoute,
    FlagFileObstructionDetector,
    LocalFacilitiesTicketStore,
    RoutePlan,
    RouteProofAgent,
    RouteProofStatus,
    load_route_plan,
)
from navila_orca.runner import NavigationRunner


def _state(step_id: int, position=(0.0, 0.0, 0.0)) -> RobotState:
    return RobotState(
        step_id=step_id,
        sim_time_s=step_id * 0.02,
        root_pos_world=np.asarray(position, dtype=np.float64),
        root_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        body_ang_vel=np.zeros(3),
        base_rpy=np.zeros(3),
        joint_pos=np.zeros(12),
        joint_vel=np.zeros(12),
        last_raw_action=np.zeros(12),
    )


class FakePhysics:
    control_dt = 0.02

    def __init__(self):
        self.state = _state(0)
        self.qpos_batch = np.zeros((1, 19))
        self.command = None

    def reset(self, episode):
        self.state = _state(0, episode.start_position)
        return self.state

    def set_velocity_command(self, command):
        self.command = command

    def step(self):
        self.state = _state(self.state.step_id + 1, self.state.root_pos_world)
        return self.state

    def close(self):
        pass


class FakeRenderer:
    def render(self, state, qpos_batch=None):
        rgb = np.full((12, 12, 3), state.step_id % 255, dtype=np.uint8)
        return RenderFrame(
            state.step_id,
            state.sim_time_s,
            "ego",
            rgb,
            str(state.step_id),
        )

    def close(self):
        pass


class ScriptedVLM:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.instructions = []

    def infer(self, images, instruction):
        self.instructions.append(instruction)
        return next(self.outputs)


class RouteAwareDetector:
    def __init__(self, blocked_routes):
        self.blocked_routes = set(blocked_routes)

    def detect(self, images, *, route_id):
        blocked = route_id in self.blocked_routes
        return NavigationGuardDecision(
            blocked=blocked,
            obstacle_label="delivery cart" if blocked else "unknown",
            confidence=1.0 if blocked else None,
            reason="Accessible width is blocked" if blocked else "Path is clear",
            metadata={"route_id": route_id},
        )


class BlockOnSecondInspectionDetector:
    def __init__(self):
        self.inspections = 0

    def detect(self, images, *, route_id):
        self.inspections += 1
        blocked = self.inspections == 2
        return NavigationGuardDecision(
            blocked=blocked,
            obstacle_label="chair" if blocked else "unknown",
            confidence=1.0 if blocked else None,
            reason="Chair entered the path" if blocked else "Path is clear",
            metadata={"route_id": route_id},
        )


def _episode():
    return EpisodeSpec(
        episode_id="routeproof-test",
        scene_id="synthetic",
        instruction="unused base instruction",
        start_position=np.array([0.0, 0.0, 0.0]),
        start_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        goal_position=np.array([1.0, 0.0, 0.0]),
        goal_radius=0.1,
        reference_path=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        gt_locations=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
    )


def _plan():
    return RoutePlan(
        destination="Classroom 2-1",
        routes=(
            ApprovedRoute("route-a", "Follow the main corridor.", priority=1),
            ApprovedRoute("route-b", "Follow the side corridor.", priority=2),
        ),
    )


def _runner(agent, vlm):
    return NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        instruction_provider=agent.current_instruction,
        navigation_guard=agent,
    )


def test_routeproof_reroutes_after_blockage_then_verifies(tmp_path):
    vlm = ScriptedVLM(["stop"])
    agent = RouteProofAgent(
        _plan(),
        RouteAwareDetector({"route-a"}),
        LocalFacilitiesTicketStore(tmp_path),
    )

    result = agent.verify(_runner(agent, vlm), _episode())

    assert result.status is RouteProofStatus.VERIFIED
    assert result.verified_route_id == "route-b"
    assert [attempt.outcome for attempt in result.attempts] == [
        "blocked",
        "verified",
    ]
    assert vlm.instructions == ["Follow the side corridor."]
    assert result.attempts[0].evidence_path is not None
    assert Path(result.attempts[0].evidence_path).is_file()


def test_no_safe_route_creates_ticket_with_evidence(tmp_path):
    vlm = ScriptedVLM(["stop"])
    agent = RouteProofAgent(
        _plan(),
        RouteAwareDetector({"route-a", "route-b"}),
        LocalFacilitiesTicketStore(tmp_path),
    )

    result = agent.verify(_runner(agent, vlm), _episode())

    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert result.ticket is not None
    ticket_path = Path(result.ticket.ticket_path)
    assert ticket_path.is_file()
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    assert ticket["status"] == "OPEN"
    assert ticket["category"] == "ACCESSIBLE_ROUTE_BLOCKED"
    assert len(ticket["attempts"]) == 2
    assert len(ticket["evidence"]) == 2
    assert vlm.instructions == []


def test_guard_interrupts_an_active_motion_chunk(tmp_path):
    detector = BlockOnSecondInspectionDetector()
    vlm = ScriptedVLM(["move forward 75 cm"])
    agent = RouteProofAgent(
        RoutePlan(
            destination="Classroom 2-1",
            routes=(ApprovedRoute("route-a", "Follow the main corridor."),),
        ),
        detector,
        LocalFacilitiesTicketStore(tmp_path),
    )
    runner = _runner(agent, vlm)

    result = agent.verify(runner, _episode())

    assert result.status is RouteProofStatus.NO_SAFE_ROUTE
    assert result.last_run.termination_reason == "route_blocked"
    assert result.last_run.control_steps == 25
    assert result.last_run.guard_decision is not None
    assert result.last_run.guard_decision.obstacle_label == "chair"
    assert runner.physics.command.stop is False
    assert runner.physics.command.vx == 0.0
    assert runner.physics.command.duration_s == 0.0


def test_flag_file_emits_one_blockage_per_file_version(tmp_path):
    flag = tmp_path / "blocked.json"
    detector = FlagFileObstructionDetector(flag)
    images = [RenderFrame(0, 0.0, "ego", np.zeros((4, 4, 3), dtype=np.uint8)).to_pil()]

    assert detector.detect(images, route_id="route-a").blocked is False
    flag.write_text('{"obstacle":"chair"}', encoding="utf-8")
    first = detector.detect(images, route_id="route-a")
    repeated = detector.detect(images, route_id="route-b")
    flag.write_text('{"obstacle":"cart","event":2}', encoding="utf-8")
    second = detector.detect(images, route_id="route-b")

    assert first.blocked is True
    assert first.obstacle_label == "chair"
    assert repeated.blocked is False
    assert second.blocked is True
    assert second.obstacle_label == "cart"


def test_load_route_plan_sorts_priority_and_validates_instructions(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text(
        json.dumps(
            {
                "destination": "Library",
                "routes": [
                    {"id": "second", "priority": 2, "instruction": "Route two"},
                    {"id": "first", "priority": 1, "instruction": "Route one"},
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = load_route_plan(path)

    assert [route.route_id for route in plan.routes] == ["first", "second"]
