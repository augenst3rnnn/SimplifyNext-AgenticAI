"""Offline regressions for camera identity, motion admission, and capture RPCs."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from navila_orca import cli
from navila_orca.contracts import NavigationGuardDecision
from navila_orca.render.orca_camera import OrcaGrpcPngCamera, OrcaMujocoPngCamera
from navila_orca.routeproof import FlagFileObstructionDetector
from navila_orca.runner import NavigationRunner
from test_routeproof_live_guide import MovingPhysics, setup
from test_runner import FakeRenderer, ScriptedVLM, _episode


def _renderer(tmp_path, flags=()):
    args = cli._build_parser().parse_args(["run", "--render-backend", "orcalab", *flags])
    bridge, server = cli._make_renderer(
        args, SimpleNamespace(num_envs=1, joint_qpos_addr={}), tmp_path,
    )
    assert server is None
    return bridge


@pytest.mark.parametrize("flags,actor,asset", [
    ([], "mujococamera1080", "prefabs/mujococamera1080"),
    (["--camera-actor-name", "custom_ego"], "custom_ego", "prefabs/mujococamera1080"),
    (["--camera-asset-path", "prefabs/custom_camera"], "mujococamera1080", "prefabs/custom_camera"),
    (["--camera-actor-name", "navila_ego", "--camera-asset-path", "prefabs/agentcamera"],
     "navila_ego", "prefabs/agentcamera"),
])
def test_mujoco_capture_targets_selected_followed_actor(tmp_path, flags, actor, asset):
    bridge = _renderer(tmp_path, ["--camera-name", "frame_label", *flags])
    camera = bridge._camera_factory(bridge.camera_name, bridge.camera_port)
    assert isinstance(camera, OrcaMujocoPngCamera)
    assert camera.remote_camera_name == bridge.camera_actor_name == actor
    assert bridge.camera_asset_path == asset
    assert camera.name == "frame_label"  # Frame labels are not remote actor identifiers.


@pytest.mark.parametrize("transport,mode", [
    ("grpc-png", "agent-data-png"),
    ("websocket", "agent-data-png"),
    ("websocket", "mujoco-png"),
])
def test_legacy_camera_modes_keep_compatible_stream_defaults(tmp_path, transport, mode):
    bridge = _renderer(tmp_path, ["--camera-transport", transport, "--orcalab-camera-mode", mode])
    assert bridge.camera_actor_name == "navila_ego"
    assert bridge.camera_asset_path == "prefabs/agentcamera"
    if transport == "grpc-png":
        camera = bridge._camera_factory(bridge.camera_name, bridge.camera_port)
        assert type(camera) is OrcaGrpcPngCamera
        # Preserve the legacy GetCameraDataPNG selection in this mode.
        assert camera.remote_camera_name == "AgentCamera"
    else:
        assert bridge._camera_factory is None


def test_no_bind_mujoco_capture_still_honors_explicit_actor(tmp_path):
    bridge = _renderer(tmp_path, ["--no-camera-bind", "--camera-actor-name", "manual_camera"])
    camera = bridge._camera_factory(bridge.camera_name, bridge.camera_port)
    assert bridge.bind_camera is False
    assert camera.remote_camera_name == "manual_camera"


@pytest.mark.parametrize("prior_motion", [False, True])
@pytest.mark.parametrize("returned_action", ["move forward 25 cm", "stop"])
def test_flag_arriving_during_inference_preempts_returned_action(
    tmp_path, prior_motion, returned_action,
):
    flag = tmp_path / "blocked.json"

    def refuse_alternate(request):
        if request.kind == "entry" and request.route_id == "b":
            raise TimeoutError("alternate entry has not been confirmed")

    outputs = (["move forward 25 cm"] if prior_motion else []) + [returned_action]
    agent, runner, events, vlm = setup(
        tmp_path / "artifacts", outputs,
        detector=FlagFileObstructionDetector(flag), callback=refuse_alternate,
    )
    original = vlm.infer
    waiting_commands = []
    event_states = []

    def infer_with_event(images, instruction):
        waiting_commands.append(runner.physics.command)
        if len(vlm.requests) == int(prior_motion):
            event_states.append(runner.physics.state)
            flag.write_text(json.dumps({"route_id": "a", "obstacle": "person", "event": 1}))
        return original(images, instruction)

    vlm.infer = infer_with_event
    result = agent.verify(runner, _episode())
    attempt = result.attempts[0]
    assert attempt.outcome == "blocked"
    assert attempt.control_steps == (25 if prior_motion else 0)
    assert attempt.decisions == len(outputs)
    assert attempt.evidence_path is not None
    assert runner.physics.state is event_states[0]
    assert not any(r.kind == "complete" for r in events.requests)
    assert all(c is not None and c.vx == c.vy == c.wz == 0 for c in waiting_commands)
    assert runner.physics.command.vx == runner.physics.command.wz == 0


@pytest.mark.parametrize("blocked", [False, True])
def test_post_inference_admission_supports_plain_navigation_guard(blocked):
    physics = MovingPhysics()
    observations = []

    class Guard:
        # The existing NavigationGuard API has no force keyword or refresh hook.
        blocked = False

        def inspect(self, images, state, instruction):
            observations.append((state.step_id, self.blocked))
            return NavigationGuardDecision(self.blocked, reason="operator event")

    guard = Guard()
    vlm = ScriptedVLM(["move forward 25 cm"])
    original = vlm.infer

    def infer_with_event(images, instruction):
        guard.blocked = blocked
        return original(images, instruction)

    vlm.infer = infer_with_event
    result = NavigationRunner(
        physics, FakeRenderer(), vlm, scene_fidelity=False,
        max_decisions=1, navigation_guard=guard,
    ).run(_episode())
    assert observations[:2] == [(0, False), (0, blocked)]
    assert result.control_steps == (0 if blocked else 25)
    assert result.termination_reason == ("route_blocked" if blocked else "max_decisions")
    assert result.decisions == 1
    assert physics.command.vx == physics.command.wz == 0
    np.testing.assert_allclose(physics.state.root_pos_world, [0 if blocked else .25, 0, 0])


@pytest.mark.parametrize("camera_type", [OrcaMujocoPngCamera, OrcaGrpcPngCamera])
def test_png_capture_rpc_timeout_cancels_request_before_any_frame(tmp_path, camera_type):
    class Service:
        slow = True
        cancelled = False
        paths = []

        async def capture(self, path):
            self.paths.append(path)
            if self.slow:
                try:
                    await asyncio.sleep(.05)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (2, 2), (10, 20, 30)).save(path)

        async def get_camera_png(self, remote, directory, filename):
            await self.capture(Path(directory) / filename)
            return True

        async def get_camera_data_png(self, remote, directory, index):
            await self.capture(Path(directory) / "color" / f"{remote}_color_{index}.png")
            return SimpleNamespace(has_color=True, transform="test-transform")

        async def destroy_grpc(self):
            pass

    camera = camera_type("label", 7070, edit_address="not-contacted:1",
                         timeout_s=.005, output_dir=str(tmp_path))
    service = Service()
    camera._service = service
    camera._loop = loop = asyncio.new_event_loop()
    try:
        with pytest.raises(TimeoutError):
            camera.get_frame()
        assert service.cancelled
        assert not service.paths[0].exists()
        assert not camera.is_first_frame_received()
        assert not asyncio.all_tasks(loop)
        # A timeout must not poison the loop or reuse the cancelled request ID.
        service.slow = False
        rgb, index = camera.get_frame()
        assert index == 0
        assert service.paths[0] != service.paths[1]
        assert np.all(rgb == [10, 20, 30])
    finally:
        camera.stop()
    assert loop.is_closed()


def test_png_camera_startup_rpc_uses_same_cancellable_timeout(tmp_path):
    class Service:
        cancelled = False

        def init_grpc(self, address):
            pass

        async def aloha(self):
            try:
                await asyncio.sleep(.05)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return True

        async def destroy_grpc(self):
            pass

    service = Service()
    camera = OrcaMujocoPngCamera(
        "label", 7070, edit_address="not-contacted:1", timeout_s=.005,
        output_dir=str(tmp_path), runtime_factory=lambda: SimpleNamespace(service_factory=lambda: service),
    )
    try:
        with pytest.raises(TimeoutError):
            camera.start()
        assert service.cancelled
        assert camera._loop is None
        assert camera._service is None
    finally:
        camera.stop()


def test_frame_based_guard_still_interrupts_an_active_motion_chunk(tmp_path):
    def refuse_alternate(request):
        if request.kind == "entry" and request.route_id == "b":
            raise TimeoutError("alternate entry has not been confirmed")

    agent, runner, _, _ = setup(tmp_path, ["move forward 75 cm"], callback=refuse_alternate)

    class Detector:
        def detect(self, images, *, route_id):
            # Obstruction onset is tied to physics, not inspection call count.
            return NavigationGuardDecision(
                route_id == "a" and runner.physics.state.step_id >= 25,
                reason="chair entered path during motion",
            )

    agent.detector = Detector()
    result = agent.verify(runner, _episode())
    assert result.attempts[0].outcome == "blocked"
    assert result.attempts[0].control_steps == 25
    assert result.attempts[0].decisions == 1
    np.testing.assert_allclose(runner.physics.state.root_pos_world, [.25, 0, 0])
    assert runner.physics.command.vx == runner.physics.command.wz == 0
