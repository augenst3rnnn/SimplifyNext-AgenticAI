"""Small in-process-capable gRPC render protocol used for pipeline smoke tests.

This module exercises a *real* gRPC transport and the same ``render`` contract
as :class:`OrcaLabRenderBridge`.  It is intentionally not a fake production
renderer: it does not emulate OrcaStudio, 3DGS, collision geometry, or camera
latency.  A programmable callback supplies RGB so navigation/control plumbing
can be tested before those external assets exist.

The protocol uses gRPC generic handlers to keep this bootstrap package free of
generated protobuf files.  Requests are JSON; responses are a JSON header plus
raw RGB bytes.
"""

from __future__ import annotations

import argparse
from concurrent import futures
from dataclasses import dataclass
import json
import struct
import threading
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np


SERVICE = "navila_orca.render.RenderService"
METHOD = f"/{SERVICE}/Render"
_HEADER_LENGTH = struct.Struct("!I")
_MAX_MESSAGE_BYTES = 128 * 1024 * 1024


class GrpcRenderError(RuntimeError):
    """A render request failed at or across the gRPC boundary."""


class StaleGrpcFrameError(GrpcRenderError):
    """The server returned a non-increasing frame token."""


@dataclass
class WireRobotState:
    """Fallback callback state when the public contracts module is unavailable."""

    step_id: int
    sim_time_s: float
    root_pos_world: np.ndarray
    root_quat_wxyz: np.ndarray
    body_ang_vel: np.ndarray
    base_rpy: np.ndarray
    joint_pos: np.ndarray
    joint_vel: np.ndarray
    last_raw_action: np.ndarray


def _contracts() -> tuple[type, type]:
    try:
        from navila_orca.contracts import RenderFrame, RobotState

        return RobotState, RenderFrame
    except ImportError:
        return WireRobotState, SimpleNamespace


def _array_field(state: Any, name: str, size: int) -> list[float]:
    value = getattr(state, name, np.zeros(size, dtype=np.float64))
    array = np.asarray(value, dtype=np.float64).reshape(-1)
    if array.size != size:
        raise ValueError(f"state.{name} must contain {size} values, got {array.size}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"state.{name} contains NaN or infinity")
    return array.tolist()


def _encode_request(state: Any, qpos_batch: np.ndarray | None) -> bytes:
    document: dict[str, Any] = {
        "step_id": int(state.step_id),
        "sim_time_s": float(state.sim_time_s),
        "root_pos_world": _array_field(state, "root_pos_world", 3),
        "root_quat_wxyz": _array_field(state, "root_quat_wxyz", 4),
        "body_ang_vel": _array_field(state, "body_ang_vel", 3),
        "base_rpy": _array_field(state, "base_rpy", 3),
        "joint_pos": _array_field(state, "joint_pos", 12),
        "joint_vel": _array_field(state, "joint_vel", 12),
        "last_raw_action": _array_field(state, "last_raw_action", 12),
    }
    if not np.isfinite(document["sim_time_s"]):
        raise ValueError("state.sim_time_s must be finite")
    if qpos_batch is not None:
        qpos = np.asarray(qpos_batch, dtype=np.float64)
        if qpos.ndim == 1:
            qpos = qpos[None, :]
        if qpos.ndim != 2 or not np.all(np.isfinite(qpos)):
            raise ValueError("qpos_batch must be a finite rank-two array")
        document["qpos_batch"] = qpos.tolist()
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _decode_request(payload: bytes) -> tuple[Any, np.ndarray | None]:
    document = json.loads(payload.decode("utf-8"))
    robot_state_type, _ = _contracts()
    kwargs = {
        "step_id": int(document["step_id"]),
        "sim_time_s": float(document["sim_time_s"]),
        "root_pos_world": np.asarray(document["root_pos_world"], dtype=np.float64),
        "root_quat_wxyz": np.asarray(document["root_quat_wxyz"], dtype=np.float64),
        "body_ang_vel": np.asarray(document["body_ang_vel"], dtype=np.float64),
        "base_rpy": np.asarray(document["base_rpy"], dtype=np.float64),
        "joint_pos": np.asarray(document["joint_pos"], dtype=np.float64),
        "joint_vel": np.asarray(document["joint_vel"], dtype=np.float64),
        "last_raw_action": np.asarray(document["last_raw_action"], dtype=np.float64),
    }
    state = robot_state_type(**kwargs)
    qpos_value = document.get("qpos_batch")
    qpos = None if qpos_value is None else np.asarray(qpos_value, dtype=np.float64)
    return state, qpos


def _encode_response(
    *,
    rgb: np.ndarray,
    frame_id: int,
    step_id: int,
    sim_time_s: float,
    camera_id: str,
) -> bytes:
    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"renderer must return HxWx3 RGB, got {image.shape}")
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    image = np.ascontiguousarray(image)
    header = json.dumps(
        {
            "shape": list(image.shape),
            "dtype": "uint8",
            "frame_id": int(frame_id),
            "step_id": int(step_id),
            "sim_time_s": float(sim_time_s),
            "camera_id": str(camera_id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return _HEADER_LENGTH.pack(len(header)) + header + image.tobytes()


def _decode_response(payload: bytes) -> dict[str, Any]:
    if len(payload) < _HEADER_LENGTH.size:
        raise GrpcRenderError("truncated render response")
    (header_size,) = _HEADER_LENGTH.unpack_from(payload)
    header_end = _HEADER_LENGTH.size + header_size
    if header_end > len(payload):
        raise GrpcRenderError("truncated render response header")
    header = json.loads(payload[_HEADER_LENGTH.size : header_end].decode("utf-8"))
    shape = tuple(int(value) for value in header["shape"])
    if len(shape) != 3 or shape[2] != 3:
        raise GrpcRenderError(f"invalid remote RGB shape {shape}")
    expected = int(np.prod(shape))
    raw = payload[header_end:]
    if len(raw) != expected:
        raise GrpcRenderError(
            f"remote RGB has {len(raw)} bytes; expected {expected} for {shape}"
        )
    header["rgb"] = np.frombuffer(raw, dtype=np.uint8).reshape(shape).copy()
    return header


class ProgrammableRenderServer:
    """Actual gRPC server whose callback maps state/qpos to an RGB image.

    The callback signature is ``callback(state, qpos_batch)``.  It may return
    either an HxWx3 array or a RenderFrame-like object. The transport server
    always assigns its own strictly increasing integer token; a callback's
    engine-specific string ``frame_id`` is deliberately not used as a wire
    synchronization token.
    """

    def __init__(
        self,
        callback: Callable[[Any, np.ndarray | None], Any],
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        camera_id: str = "programmable",
        max_workers: int = 2,
    ) -> None:
        if not host:
            raise ValueError("host must be explicit")
        if not (0 <= int(port) <= 65535):
            raise ValueError("port must be in [0, 65535]")
        self.callback = callback
        self.host = host
        self.requested_port = int(port)
        self.camera_id = camera_id
        self.max_workers = int(max_workers)
        self._server: Any | None = None
        self._port: int | None = None
        self._frame_id = 0
        self._lock = threading.Lock()

    @property
    def address(self) -> str:
        if self._port is None:
            raise RuntimeError("server has not been started")
        return f"{self.host}:{self._port}"

    @property
    def frame_count(self) -> int:
        """Number of state-bearing render RPCs served in this process."""

        return self._frame_id

    def start(self) -> "ProgrammableRenderServer":
        if self._server is not None:
            return self
        try:
            import grpc
        except ImportError as exc:
            raise GrpcRenderError("grpcio is required for the render bridge") from exc

        options = (
            ("grpc.max_receive_message_length", _MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", _MAX_MESSAGE_BYTES),
        )
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=self.max_workers), options=options
        )
        method_handler = grpc.unary_unary_rpc_method_handler(
            self._handle_render,
            request_deserializer=lambda value: value,
            response_serializer=lambda value: value,
        )
        server.add_generic_rpc_handlers(
            (grpc.method_handlers_generic_handler(SERVICE, {"Render": method_handler}),)
        )
        port = server.add_insecure_port(f"{self.host}:{self.requested_port}")
        if port == 0:
            raise GrpcRenderError(
                f"could not bind gRPC render server to {self.host}:{self.requested_port}"
            )
        server.start()
        self._server = server
        self._port = int(port)
        return self

    def stop(self, grace_s: float = 0.0) -> None:
        server = self._server
        self._server = None
        self._port = None
        if server is not None:
            event = server.stop(float(grace_s))
            event.wait(timeout=max(1.0, float(grace_s) + 1.0))

    close = stop

    def __enter__(self) -> "ProgrammableRenderServer":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def _handle_render(self, payload: bytes, context: Any) -> bytes:
        import grpc

        try:
            state, qpos = _decode_request(payload)
            result = self.callback(state, qpos)
            if hasattr(result, "rgb"):
                rgb = result.rgb
                step_id = int(result.step_id)
                sim_time_s = float(result.sim_time_s)
                camera_id = str(result.camera_id)
            else:
                rgb = result
                step_id = int(state.step_id)
                sim_time_s = float(state.sim_time_s)
                camera_id = self.camera_id
            with self._lock:
                self._frame_id += 1
                frame_id = self._frame_id
            return _encode_response(
                rgb=rgb,
                frame_id=frame_id,
                step_id=step_id,
                sim_time_s=sim_time_s,
                camera_id=camera_id,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception as exc:
            context.abort(grpc.StatusCode.INTERNAL, f"render callback failed: {exc}")
        raise AssertionError("grpc context.abort unexpectedly returned")


class GrpcRenderBridge:
    """RenderBridge-compatible client for :class:`ProgrammableRenderServer`."""

    def __init__(self, *, address: str, timeout_s: float = 5.0) -> None:
        if not address or ":" not in address:
            raise ValueError("address must be an explicit 'host:port'")
        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be positive")
        try:
            import grpc
        except ImportError as exc:
            raise GrpcRenderError("grpcio is required for the render bridge") from exc

        options = (
            ("grpc.max_receive_message_length", _MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", _MAX_MESSAGE_BYTES),
        )
        self.address = address
        self.timeout_s = float(timeout_s)
        self._grpc = grpc
        self._channel = grpc.insecure_channel(address, options=options)
        self._rpc = self._channel.unary_unary(
            METHOD,
            request_serializer=lambda value: value,
            response_deserializer=lambda value: value,
        )
        self._last_frame_id = -1
        self._latest_frame: Any | None = None

    def _request_frame(self, state: Any, qpos_batch: np.ndarray | None = None) -> Any:
        try:
            response = self._rpc(
                _encode_request(state, qpos_batch), timeout=self.timeout_s
            )
        except self._grpc.RpcError as exc:
            raise GrpcRenderError(
                f"render RPC to {self.address} failed: "
                f"{exc.code().name}: {exc.details()}"
            ) from exc
        decoded = _decode_response(response)
        remote_step = int(decoded["step_id"])
        requested_step = int(state.step_id)
        if remote_step != requested_step:
            raise GrpcRenderError(
                f"remote frame belongs to step {remote_step}; requested {requested_step}"
            )
        remote_time = float(decoded["sim_time_s"])
        requested_time = float(state.sim_time_s)
        if not np.isclose(remote_time, requested_time, rtol=0.0, atol=1.0e-9):
            raise GrpcRenderError(
                f"remote frame time {remote_time} does not match requested "
                f"{requested_time}"
            )
        frame_id = int(decoded["frame_id"])
        if frame_id <= self._last_frame_id:
            raise StaleGrpcFrameError(
                f"remote frame token {frame_id} is not newer than {self._last_frame_id}"
            )
        self._last_frame_id = frame_id
        _, render_frame_type = _contracts()
        return render_frame_type(
            step_id=remote_step,
            sim_time_s=remote_time,
            camera_id=str(decoded["camera_id"]),
            rgb=decoded["rgb"],
            frame_id=str(frame_id),
        )

    def push_state(self, state: Any, qpos_batch: np.ndarray | None = None) -> None:
        """Push a pose over real gRPC and cache the returned diagnostic frame."""

        self._latest_frame = self._request_frame(state, qpos_batch)

    def capture(self, state: Any, qpos_batch: np.ndarray | None = None) -> Any:
        """Return the frame associated with ``state``, pushing it if necessary."""

        if self._latest_frame is None or int(self._latest_frame.step_id) != int(
            state.step_id
        ):
            self.push_state(state, qpos_batch)
        return self._latest_frame

    def render(self, state: Any, qpos_batch: np.ndarray | None = None) -> Any:
        """Compatibility path combining :meth:`push_state` and :meth:`capture`."""

        self.push_state(state, qpos_batch)
        return self.capture(state, qpos_batch)

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> "GrpcRenderBridge":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def server_main(argv: list[str] | None = None) -> None:
    """Run a diagnostic gradient server; useful only for transport smoke tests."""

    parser = argparse.ArgumentParser(
        description="Programmable NaVILA-Orca gRPC smoke renderer (not OrcaStudio)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50061)
    parser.add_argument("--height", type=int, default=64)
    parser.add_argument("--width", type=int, default=96)
    args = parser.parse_args(argv)

    def gradient(state: Any, _qpos: np.ndarray | None) -> np.ndarray:
        image = np.zeros((args.height, args.width, 3), dtype=np.uint8)
        image[..., 0] = int(state.step_id) % 256
        image[..., 1] = np.arange(args.width, dtype=np.uint8)[None, :]
        image[..., 2] = np.arange(args.height, dtype=np.uint8)[:, None]
        return image

    server = ProgrammableRenderServer(gradient, host=args.host, port=args.port).start()
    print(f"diagnostic render server listening on {server.address}")
    print("This validates gRPC plumbing only; it is not an OrcaStudio renderer.")
    try:
        server._server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    server_main()
