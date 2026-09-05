from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from navila_orca.backends.mjlab_go2 import (
    GO2_POLICY_ACTION_ORDER,
    MjlabGo2Backend,
)
from navila_orca.contracts import RobotState, VelocityCommand
from navila_orca.render.orca import OrcaLabRenderBridge, StaleRenderFrameError


def state(step_id: int = 1) -> RobotState:
    return RobotState(
        step_id=step_id,
        sim_time_s=step_id * 0.02,
        root_pos_world=np.zeros(3),
        root_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        body_ang_vel=np.zeros(3),
        base_rpy=np.zeros(3),
        joint_pos=np.zeros(12),
        joint_vel=np.zeros(12),
        last_raw_action=np.zeros(12),
    )


def test_backend_module_import_does_not_import_heavy_stack() -> None:
    package_root = Path(__file__).parents[1] / "src"
    script = (
        "import sys; import navila_orca.backends.mjlab_go2; "
        "assert 'torch' not in sys.modules; "
        "assert 'mjlab' not in sys.modules; "
        "assert 'src.tasks' not in sys.modules"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(package_root)
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_backend_constructor_is_lazy_and_accepts_runner_command() -> None:
    backend = MjlabGo2Backend(checkpoint="/does/not/need/to/exist/yet.pt")
    backend.set_velocity_command(VelocityCommand(0.3, -0.1, 0.2, 0.5))
    assert not backend.started
    np.testing.assert_allclose(backend._velocity_command, [0.3, -0.1, 0.2])


def test_backend_rejects_negative_warmup_steps() -> None:
    with pytest.raises(ValueError, match="warmup_steps"):
        MjlabGo2Backend(warmup_steps=-1)


def test_reset_warmup_runs_zero_command_and_restores_queued_command() -> None:
    backend = MjlabGo2Backend(
        checkpoint="/does/not/need/to/exist/yet.pt", warmup_steps=3
    )
    backend._velocity_command = np.asarray([0.3, -0.1, 0.2], dtype=np.float32)
    backend._obs = "obs-0"
    stepped_commands = []
    apply_calls = []

    class _Env:
        def step(self, action):
            assert action == backend._obs
            stepped_commands.append(backend._velocity_command.copy())
            index = len(stepped_commands)
            return f"obs-{index}", 0.0, False, {}

    backend._env = _Env()
    backend._policy = lambda obs: obs
    backend._torch = SimpleNamespace(inference_mode=nullcontext)
    backend._apply_velocity_command = lambda *, refresh_observation: apply_calls.append(
        (refresh_observation, backend._velocity_command.copy())
    )

    backend._run_zero_velocity_warmup()

    assert len(stepped_commands) == 3
    for command in stepped_commands:
        np.testing.assert_array_equal(command, [0.0, 0.0, 0.0])
    np.testing.assert_allclose(backend._velocity_command, [0.3, -0.1, 0.2])
    assert backend._obs == "obs-3"
    assert backend._step_id == 0
    assert [call[0] for call in apply_calls] == [True, False, False, False, True]
    np.testing.assert_allclose(apply_calls[-1][1], [0.3, -0.1, 0.2])


def test_deterministic_play_removes_training_randomization() -> None:
    events = {
        "reset_base": SimpleNamespace(
            params={"pose_range": {"x": (-0.5, 0.5)}, "velocity_range": {"x": (-1, 1)}}
        ),
        "push_robot": object(),
        "foot_friction": object(),
        "encoder_bias": object(),
        "base_com": object(),
    }
    MjlabGo2Backend._apply_deterministic_play_overrides(SimpleNamespace(events=events))
    assert events == {
        "reset_base": events["reset_base"],
    }
    assert events["reset_base"].params == {
        "pose_range": {},
        "velocity_range": {},
    }


def test_backend_rejects_policy_action_order_drift() -> None:
    class _Manager:
        @staticmethod
        def get_term(name):
            assert name == "joint_pos"
            return SimpleNamespace(target_names=list(reversed(GO2_POLICY_ACTION_ORDER)))

    with pytest.raises(RuntimeError, match="action order"):
        MjlabGo2Backend._assert_go2_action_order(
            SimpleNamespace(action_manager=_Manager())
        )


class _FreshCamera:
    def __init__(self, _name: str, _port: int, shared: dict[str, int]):
        self.shared = shared
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def is_first_frame_received(self):
        return self.shared["frame"] > 0

    def get_frame(self, format="rgb24"):
        assert format == "rgb24"
        image = np.full((3, 4, 3), self.shared["frame"], dtype=np.uint8)
        return image, self.shared["frame"]


class _Renderer:
    def __init__(self, shared: dict[str, int], **kwargs):
        self.shared = shared
        self.kwargs = kwargs
        self.rendered = []
        self.closed = False

    def render(self, qpos, sim_time):
        self.rendered.append((qpos.copy(), sim_time))
        self.shared["frame"] += 1
        self.shared["render_returned"] = True

    def close(self):
        self.closed = True


def test_orca_bridge_requires_a_post_update_camera_frame() -> None:
    shared = {"frame": 4, "render_returned": False, "post_return_reads": 0}
    renderer_instances = []

    class PostReturnCamera(_FreshCamera):
        def get_frame(self, format="rgb24"):
            if self.shared["render_returned"]:
                self.shared["post_return_reads"] += 1
                # First post-return read establishes the fence. Only the next
                # camera publication is eligible for this pose.
                if self.shared["post_return_reads"] == 2:
                    self.shared["frame"] += 1
            return super().get_frame(format=format)

    def renderer_factory(**kwargs):
        renderer = _Renderer(shared, **kwargs)
        renderer_instances.append(renderer)
        return renderer

    bridge = OrcaLabRenderBridge(
        orcagym_address="127.0.0.1:50051",
        camera_port=7070,
        joint_qpos_addr={"FR_hip_joint": 7},
        renderer_factory=renderer_factory,
        camera_factory=lambda name, port: PostReturnCamera(name, port, shared),
        timeout_s=0.1,
    )
    qpos = np.zeros((1, 19), dtype=np.float64)
    frame = bridge.render(state(), qpos_batch=qpos)
    assert frame.frame_id == "orca:6"
    assert np.all(frame.rgb == 6)
    np.testing.assert_array_equal(renderer_instances[0].rendered[0][0], qpos)
    bridge.close()
    assert renderer_instances[0].closed


def test_orca_bridge_times_out_instead_of_reusing_stale_image() -> None:
    shared = {"frame": 2}

    class StaticRenderer(_Renderer):
        def render(self, qpos, sim_time):
            self.rendered.append((qpos.copy(), sim_time))

    bridge = OrcaLabRenderBridge(
        orcagym_address="127.0.0.1:50051",
        camera_port=7070,
        joint_qpos_addr={"FR_hip_joint": 7},
        renderer_factory=lambda **kwargs: StaticRenderer(shared, **kwargs),
        camera_factory=lambda name, port: _FreshCamera(name, port, shared),
        timeout_s=0.02,
        poll_interval_s=0.001,
    )
    with pytest.raises(StaleRenderFrameError, match="fresh frame"):
        bridge.render(state(), qpos_batch=np.zeros((1, 19)))
    bridge.close()


def test_orca_bridge_binds_camera_before_pose_update_and_closes_in_order() -> None:
    events = []
    shared = {"frame": 3, "reads_after_render": 0, "rendered": False}

    class OrderedRenderer(_Renderer):
        def __init__(self, shared, **kwargs):
            events.append("renderer:init")
            super().__init__(shared, **kwargs)
            self.root_xy_scale = 1.0
            self.render_root_offset = np.array([0.5, 0.0, 0.0])
            self.layout = type(
                "Layout", (), {"root_offset": np.array([[1.0, 2.0, 0.0]])}
            )()

        def render(self, qpos, sim_time):
            events.append("renderer:render")
            self.rendered.append((qpos.copy(), sim_time))
            shared["rendered"] = True

        def map_root_pose(self, qpos):
            events.append("renderer:map_root_pose")
            return (
                np.array([[9.0, 8.0, qpos[0, 2]]]),
                np.array(qpos[:, 3:7], copy=True),
            )

        def close(self):
            events.append("renderer:close")
            super().close()

    class OrderedFollower:
        def __init__(self, **kwargs):
            events.append("follower:init")
            self.kwargs = kwargs
            self.pose = None

        def start(self):
            events.append("follower:start")

        def update(self, position, quat):
            events.append("follower:update")
            self.pose = (np.asarray(position), np.asarray(quat))

        def close(self):
            events.append("follower:close")

    class OrderedCamera(_FreshCamera):
        def __init__(self, name, port, shared):
            events.append("camera:init")
            super().__init__(name, port, shared)

        def start(self):
            events.append("camera:start")
            super().start()

        def get_frame(self, format="rgb24"):
            if shared["rendered"]:
                shared["reads_after_render"] += 1
                if shared["reads_after_render"] == 2:
                    shared["frame"] += 1
            return super().get_frame(format=format)

        def stop(self):
            events.append("camera:stop")
            super().stop()

    followers = []

    def follower_factory(**kwargs):
        follower = OrderedFollower(**kwargs)
        followers.append(follower)
        return follower

    bridge = OrcaLabRenderBridge(
        orcagym_address="127.0.0.1:50051",
        camera_port=7070,
        joint_qpos_addr={"FR_hip_joint": 7},
        renderer_factory=lambda **kwargs: OrderedRenderer(shared, **kwargs),
        camera_factory=lambda name, port: OrderedCamera(name, port, shared),
        camera_follower_factory=follower_factory,
        bind_camera=True,
        timeout_s=0.1,
    )
    qpos = np.zeros((1, 19), dtype=np.float64)
    qpos[0, :7] = [0.25, 0.5, 0.4, 1.0, 0.0, 0.0, 0.0]
    frame = bridge.render(state(), qpos_batch=qpos)
    assert frame.frame_id == "orca:4"
    assert events.index("follower:start") < events.index("camera:start")
    assert events.index("renderer:map_root_pose") < events.index("follower:update")
    assert events.index("follower:update") < events.index("renderer:render")
    np.testing.assert_allclose(followers[0].pose[0], [9.0, 8.0, 0.4])

    bridge.close()
    assert events.index("follower:close") < events.index("renderer:close")
    assert events.index("renderer:close") < events.index("camera:stop")


def test_real_mjwarp_go2_one_step_when_explicitly_enabled() -> None:
    if os.environ.get("NAVILA_ORCA_RUN_GPU") != "1":
        pytest.skip("set NAVILA_ORCA_RUN_GPU=1 in the orcalab env for the GPU smoke")
    backend = MjlabGo2Backend()
    try:
        first = backend.reset(None)
        backend.set_velocity_command(VelocityCommand(0.2, 0.0, 0.0, 0.5))
        second = backend.step()
        assert second.state.step_id == first.step_id + 1
        assert isinstance(second.reward, float)
        assert backend.qpos_batch.shape[0] == 1
        assert len(backend.joint_qpos_addr) == 12
        report = backend.alignment_report
        assert report["policy_action_order"] == list(GO2_POLICY_ACTION_ORDER)
        assert report["mujoco"]["ground"]["friction"] == pytest.approx(
            [1.0, 0.005, 0.0001]
        )
        json.dumps(report)
    finally:
        backend.close()
