from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("grpc")

from navila_orca.contracts import RenderFrame, RobotState
from navila_orca.render.grpc_bridge import (
    GrpcRenderBridge,
    GrpcRenderError,
    ProgrammableRenderServer,
    StaleGrpcFrameError,
)


def state(step_id: int = 3) -> RobotState:
    return RobotState(
        step_id=step_id,
        sim_time_s=step_id * 0.02,
        root_pos_world=np.array([1.0, 2.0, 0.35]),
        root_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        body_ang_vel=np.zeros(3),
        base_rpy=np.zeros(3),
        joint_pos=np.arange(12, dtype=np.float64),
        joint_vel=np.zeros(12),
        last_raw_action=np.zeros(12),
    )


def test_real_grpc_round_trip_carries_state_qpos_and_rgb() -> None:
    observed: list[tuple[RobotState, np.ndarray | None]] = []

    def render(request_state, qpos):
        observed.append((request_state, qpos))
        image = np.zeros((5, 7, 3), dtype=np.uint8)
        image[..., 0] = request_state.step_id
        image[..., 1] = int(round(request_state.root_pos_world[0] * 10))
        return image

    qpos = np.arange(19, dtype=np.float64)[None, :]
    with ProgrammableRenderServer(render) as server:
        with GrpcRenderBridge(address=server.address, timeout_s=2.0) as client:
            frame = client.render(state(), qpos_batch=qpos)

    assert isinstance(frame, RenderFrame)
    assert frame.step_id == 3
    assert frame.sim_time_s == pytest.approx(0.06)
    assert frame.camera_id == "programmable"
    assert frame.frame_id == "1"
    assert frame.rgb.shape == (5, 7, 3)
    assert np.all(frame.rgb[..., 0] == 3)
    assert np.all(frame.rgb[..., 1] == 10)
    assert len(observed) == 1
    assert observed[0][0].joint_pos.tolist() == list(range(12))
    np.testing.assert_array_equal(observed[0][1], qpos)


def test_client_rejects_reused_remote_frame_token() -> None:
    def render(request_state, _qpos):
        return RenderFrame(
            step_id=request_state.step_id,
            sim_time_s=request_state.sim_time_s,
            camera_id="fixed-token",
            rgb=np.zeros((2, 3, 3), dtype=np.uint8),
            frame_id="orca:7",
        )

    with ProgrammableRenderServer(render) as server:
        with GrpcRenderBridge(address=server.address, timeout_s=2.0) as client:
            assert client.render(state(1)).frame_id == "1"
            # Simulate a broken/malicious server reusing its transport token.
            server._frame_id = 0
            with pytest.raises(StaleGrpcFrameError, match="not newer"):
                client.render(state(2))


@pytest.mark.parametrize(
    ("wrong_step", "time_offset", "message"),
    [(True, 0.0, "belongs to step"), (False, 0.5, "does not match requested")],
)
def test_client_rejects_frame_for_wrong_physics_state(
    wrong_step: bool, time_offset: float, message: str
) -> None:
    def render(request_state, _qpos):
        return RenderFrame(
            step_id=request_state.step_id + int(wrong_step),
            sim_time_s=request_state.sim_time_s + time_offset,
            camera_id="wrong-state",
            rgb=np.zeros((2, 3, 3), dtype=np.uint8),
            frame_id="arbitrary-engine-token",
        )

    with ProgrammableRenderServer(render) as server:
        with GrpcRenderBridge(address=server.address, timeout_s=2.0) as client:
            with pytest.raises(GrpcRenderError, match=message):
                client.render(state(4))


def test_callback_error_crosses_grpc_boundary() -> None:
    def broken(_state, _qpos):
        raise RuntimeError("deliberate callback failure")

    with ProgrammableRenderServer(broken) as server:
        with GrpcRenderBridge(address=server.address, timeout_s=2.0) as client:
            with pytest.raises(GrpcRenderError, match="deliberate callback failure"):
                client.render(state())
