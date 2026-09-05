"""Production OrcaLab pose-to-camera render bridge.

Physics remains in MJWarp.  ``OrcaLabBatchRenderer`` mirrors generalized
positions into OrcaStudio through OrcaGym's UpdateLocalEnv RPC, while
``CameraWrapper`` receives the camera's H.264 WebSocket stream.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from navila_orca.render.orca_camera import (
    DEFAULT_CAMERA_ACTOR_NAME,
    DEFAULT_CAMERA_ASSET,
    DEFAULT_CAMERA_MOUNT_POSITION,
    DEFAULT_CAMERA_MOUNT_QUAT_WXYZ,
)

DEFAULT_GO2_ASSET = "assets/e071469a36d3c8aa/unitree_robots/prefabs/go2_usda"


class OrcaDependencyError(RuntimeError):
    """Raised when the optional OrcaLab camera/render stack is unavailable."""


class StaleRenderFrameError(TimeoutError):
    """Raised when OrcaStudio does not publish a frame after the state update."""


def _render_frame_type():
    from navila_orca.contracts import RenderFrame

    return RenderFrame


class OrcaLabRenderBridge:
    """Mirror MJWarp qpos to OrcaLab and return a newly rendered RGB frame.

    ``orcagym_address`` is the OrcaGym gRPC endpoint. ``camera_port`` is the
    OrcaStudio camera WebSocket port consumed by OrcaGym's ``CameraWrapper``.
    The camera wrapper currently connects to localhost, so remote OrcaStudio
    deployments should forward that port locally.

    Factories are injectable solely for deterministic unit tests.  Omitting
    them selects the real OrcaLab and OrcaGym implementations.
    """

    def __init__(
        self,
        *,
        orcagym_address: str,
        camera_port: int,
        joint_qpos_addr: dict[str, int],
        camera_name: str = "navila",
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.005,
        num_envs: int = 1,
        agent_prefix: str = "go2",
        agent_name: str | None = None,
        discover_agents: bool = False,
        asset_path: str = DEFAULT_GO2_ASSET,
        terrain_asset_path: str | None = None,
        publish: bool = True,
        anchor_to_scene: bool = False,
        scene_timestep: float | None = None,
        scene_profile: str = "orca-runtime",
        strict_scene_options: bool = False,
        manual_xml_override: bool = False,
        aligned_xml_output: str | Path | None = None,
        bind_camera: bool = False,
        edit_address: str = "127.0.0.1:50151",
        camera_actor_name: str = DEFAULT_CAMERA_ACTOR_NAME,
        camera_asset_path: str = DEFAULT_CAMERA_ASSET,
        camera_mount_position: Sequence[float] = DEFAULT_CAMERA_MOUNT_POSITION,
        camera_mount_quat_wxyz: Sequence[float] = DEFAULT_CAMERA_MOUNT_QUAT_WXYZ,
        stabilize_camera_horizon: bool = False,
        renderer_factory: Callable[..., Any] | None = None,
        camera_factory: Callable[[str, int], Any] | None = None,
        camera_follower_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not orcagym_address or ":" not in orcagym_address:
            raise ValueError("orcagym_address must be an explicit 'host:port'")
        if not (1 <= int(camera_port) <= 65535):
            raise ValueError("camera_port must be in [1, 65535]")
        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be positive")
        if poll_interval_s <= 0.0:
            raise ValueError("poll_interval_s must be positive")

        self.orcagym_address = orcagym_address
        self.camera_port = int(camera_port)
        self.camera_name = camera_name
        self.timeout_s = float(timeout_s)
        self.poll_interval_s = float(poll_interval_s)
        self.num_envs = int(num_envs)
        self.joint_qpos_addr = dict(joint_qpos_addr)
        self.agent_prefix = agent_prefix
        self.agent_name = None if agent_name is None else str(agent_name)
        self.discover_agents = bool(discover_agents)
        self.asset_path = asset_path
        self.terrain_asset_path = terrain_asset_path
        self.publish = bool(publish)
        self.anchor_to_scene = bool(anchor_to_scene)
        self.scene_timestep = None if scene_timestep is None else float(scene_timestep)
        self.scene_profile = str(scene_profile)
        self.strict_scene_options = bool(strict_scene_options)
        self.manual_xml_override = bool(manual_xml_override)
        self.aligned_xml_output = (
            None
            if aligned_xml_output is None
            else Path(aligned_xml_output).expanduser()
        )
        self.bind_camera = bool(bind_camera)
        self.edit_address = edit_address
        self.camera_actor_name = camera_actor_name
        self.camera_asset_path = camera_asset_path
        self.camera_mount_position = tuple(float(v) for v in camera_mount_position)
        self.camera_mount_quat_wxyz = tuple(float(v) for v in camera_mount_quat_wxyz)
        self.stabilize_camera_horizon = bool(stabilize_camera_horizon)
        if len(self.camera_mount_position) != 3:
            raise ValueError("camera_mount_position must contain X Y Z")
        if len(self.camera_mount_quat_wxyz) != 4:
            raise ValueError("camera_mount_quat_wxyz must contain W X Y Z")
        self._renderer_factory = renderer_factory
        self._camera_factory = camera_factory
        self._camera_follower_factory = camera_follower_factory

        self._renderer: Any | None = None
        self._camera: Any | None = None
        self._camera_follower: Any | None = None
        self._last_frame_id = -1
        self._pending_step_id: int | None = None
        self._pending_baseline = -1

    @property
    def started(self) -> bool:
        return self._renderer is not None and self._camera is not None

    @property
    def alignment_report(self) -> dict[str, Any] | None:
        if self._renderer is None:
            return None
        report = getattr(self._renderer, "alignment_report", None)
        return None if report is None else dict(report)

    def start(self) -> None:
        """Connect the real OrcaLab pose stream and camera stream lazily."""

        if self.started:
            return
        renderer_factory = self._renderer_factory
        camera_factory = self._camera_factory
        follower_factory = self._camera_follower_factory
        try:
            if renderer_factory is None:
                module = importlib.import_module("navila_orca.orcalab_runtime.batch_render")
                renderer_factory = module.OrcaLabBatchRenderer
            if camera_factory is None:
                module = importlib.import_module("orca_gym.sensor.rgbd_camera")
                camera_factory = module.CameraWrapper
            if self.bind_camera and follower_factory is None:
                module = importlib.import_module("navila_orca.render.orca_camera")
                follower_factory = module.OrcaEgoCameraFollower
        except (ImportError, ModuleNotFoundError) as exc:
            raise OrcaDependencyError(
                "OrcaLab rendering dependencies are unavailable. Activate the "
                "local 'orcalab' environment and install this project with `pip install "
                "--no-build-isolation --no-deps -e .`. Install any missing av "
                "or OpenCV package at the versions recorded in README.md. "
                f"Original error: {exc}"
            ) from exc

        try:
            renderer = renderer_factory(
                orcagym_addr=self.orcagym_address,
                num_envs=self.num_envs,
                joint_qpos_addr=self.joint_qpos_addr,
                agent_prefix=self.agent_prefix,
                agent_names=None if self.agent_name is None else [self.agent_name],
                discover_agents=self.discover_agents,
                asset_path=self.asset_path,
                terrain_asset_path=self.terrain_asset_path,
                publish=self.publish,
                anchor_to_scene=self.anchor_to_scene,
                scene_timestep=self.scene_timestep,
                scene_profile=self.scene_profile,
                strict_scene_options=self.strict_scene_options,
                manual_xml_override=self.manual_xml_override,
                aligned_xml_output=self.aligned_xml_output,
            )
            follower = None
            if self.bind_camera:
                assert follower_factory is not None
                follower = follower_factory(
                    edit_address=self.edit_address,
                    color_port=self.camera_port,
                    actor_name=self.camera_actor_name,
                    asset_path=self.camera_asset_path,
                    mount_position=self.camera_mount_position,
                    mount_quat_wxyz=self.camera_mount_quat_wxyz,
                    stabilize_horizon=self.stabilize_camera_horizon,
                    event_loop=getattr(renderer, "loop", None),
                )
                follower.start()
            camera = camera_factory(self.camera_name, self.camera_port)
            if follower is not None and not bool(
                getattr(camera, "pull_capture", False)
            ):
                wait_for_stream = getattr(follower, "wait_for_stream", None)
                if callable(wait_for_stream):
                    wait_for_stream(min(self.timeout_s, 10.0))
            camera.start()
        except Exception:
            camera_candidate = locals().get("camera")
            if camera_candidate is not None:
                try:
                    camera_candidate.stop()
                except Exception:
                    pass
            follower_candidate = locals().get("follower")
            if follower_candidate is not None:
                try:
                    follower_candidate.close()
                except Exception:
                    pass
            candidate = locals().get("renderer")
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            raise

        self._renderer = renderer
        self._camera = camera
        self._camera_follower = follower
        self._last_frame_id = -1
        self._pending_step_id = None
        self._pending_baseline = -1
        if not self.publish:
            agent_names = getattr(renderer, "agent_names", [])
            print(
                "SCENE_REUSE_OK: preserved current OrcaLab layout; "
                f"robot={agent_names}, profile={self.scene_profile}, "
                f"anchor_to_scene={self.anchor_to_scene}"
            )

    def push_state(self, state: Any, qpos_batch: np.ndarray | None = None) -> None:
        """Push generalized positions through OrcaGym ``UpdateLocalEnv``.

        ``RobotState`` deliberately contains only simulator-independent robot
        fields, so production callers normally pass ``backend.qpos_batch`` as
        the second argument.  An object exposing a ``qpos_batch`` attribute is
        also accepted for convenience.
        """

        self._ensure_started()
        if qpos_batch is None:
            qpos_batch = getattr(state, "qpos_batch", None)
        if qpos_batch is None:
            raise ValueError(
                "OrcaLab rendering needs engine qpos; pass backend.qpos_batch "
                "as render(state, qpos_batch)"
            )
        qpos = np.asarray(qpos_batch, dtype=np.float64)
        if qpos.ndim == 1:
            qpos = qpos[None, :]
        if qpos.ndim != 2 or qpos.shape[0] != self.num_envs:
            raise ValueError(
                f"qpos_batch must have shape [{self.num_envs}, nq], got {qpos.shape}"
            )
        if not np.all(np.isfinite(qpos)):
            raise ValueError("qpos_batch contains NaN or infinity")
        if qpos.shape[1] < 7:
            raise ValueError(
                "qpos_batch must contain root [x,y,z,qw,qx,qy,qz] at indices 0:7"
            )

        # Establish the fence only after UpdateLocalEnv returns. A frame that
        # arrives while the RPC is in flight may still depict the previous
        # pose, so accepting merely `> pre_rpc_sequence` would race.
        if self._camera_follower is not None:
            base_position, base_quat = self._rendered_root_pose(qpos)
            # Move the root camera first. UpdateLocalEnv then presents the
            # matching robot pose while OrcaStudio renders the next RGB frame.
            self._camera_follower.update(base_position, base_quat)
        self._renderer.render(qpos.copy(), float(state.sim_time_s))
        if bool(getattr(self._camera, "pull_capture", False)):
            # GetCameraDataPNG is itself a synchronous render request. Do not
            # pull an otherwise unused frame at every 25 Hz pose update; the
            # next capture() call performs one fresh render for this state.
            baseline = self._last_frame_id
        else:
            _possibly_racing_rgb, camera_after = self._camera.get_frame(format="rgb24")
            baseline = max(self._last_frame_id, int(camera_after))
        self._pending_step_id = int(state.step_id)
        self._pending_baseline = baseline

    def capture(self, state: Any, qpos_batch: np.ndarray | None = None) -> Any:
        """Wait for the first camera frame newer than the latest pose push."""

        self._ensure_started()
        if self._pending_step_id != int(state.step_id):
            self.push_state(state, qpos_batch)
        baseline = self._pending_baseline

        deadline = time.monotonic() + self.timeout_s
        while True:
            rgb, frame_id = self._camera.get_frame(format="rgb24")
            frame_id = int(frame_id)
            first_received = getattr(
                self._camera, "is_first_frame_received", lambda: frame_id > 0
            )()
            if first_received and frame_id > baseline:
                break
            if time.monotonic() >= deadline:
                raise StaleRenderFrameError(
                    "OrcaStudio camera did not publish a fresh frame within "
                    f"{self.timeout_s:.3f}s after step {state.step_id}; "
                    f"camera sequence stayed at {frame_id} (required > {baseline})."
                )
            time.sleep(self.poll_interval_s)

        image = np.asarray(rgb)
        if image.ndim != 3 or image.shape[2] != 3:
            raise RuntimeError(f"camera returned invalid RGB shape {image.shape}")
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        image = np.ascontiguousarray(image)
        self._last_frame_id = frame_id
        frame_type = _render_frame_type()
        return frame_type(
            step_id=int(state.step_id),
            sim_time_s=float(state.sim_time_s),
            camera_id=self.camera_name,
            rgb=image,
            frame_id=f"orca:{frame_id}",
        )

    def render(self, state: Any, qpos_batch: np.ndarray | None = None) -> Any:
        """Compatibility path combining :meth:`push_state` and :meth:`capture`."""

        self.push_state(state, qpos_batch)
        return self.capture(state, qpos_batch)

    def close(self) -> None:
        camera, follower, renderer = (
            self._camera,
            self._camera_follower,
            self._renderer,
        )
        self._camera = None
        self._camera_follower = None
        self._renderer = None
        self._last_frame_id = -1
        self._pending_step_id = None
        self._pending_baseline = -1
        # OrcaGym's current CameraWrapper.stop() calls stop() on the main
        # thread's current asyncio loop. OrcaLabBatchRenderer owns that loop,
        # so close it first rather than invalidating it before channel.close().
        try:
            if follower is not None:
                follower.close()
        finally:
            try:
                if renderer is not None:
                    renderer.close()
            finally:
                if camera is not None:
                    camera.stop()

    def __enter__(self) -> "OrcaLabRenderBridge":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if not self.started:
            self.start()

    def _rendered_root_pose(
        self, qpos_batch: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Match the first actor's world pose transformation in the renderer."""

        mapper = getattr(self._renderer, "map_root_pose", None)
        if callable(mapper):
            positions, quaternions = mapper(qpos_batch)
            positions = np.asarray(positions, dtype=np.float64)
            quaternions = np.asarray(quaternions, dtype=np.float64)
            if positions.shape != (self.num_envs, 3) or quaternions.shape != (
                self.num_envs,
                4,
            ):
                raise RuntimeError(
                    "renderer map_root_pose returned invalid shapes: "
                    f"positions={positions.shape}, quaternions={quaternions.shape}"
                )
            return positions[0].copy(), quaternions[0].copy()

        position = np.asarray(qpos_batch[0, :3], dtype=np.float64).copy()
        xy_scale = float(getattr(self._renderer, "root_xy_scale", 1.0))
        position[:2] *= xy_scale

        layout = getattr(self._renderer, "layout", None)
        root_offsets = getattr(layout, "root_offset", None)
        if root_offsets is not None:
            offsets = np.asarray(root_offsets, dtype=np.float64)
            if offsets.ndim != 2 or offsets.shape[0] < 1 or offsets.shape[1] != 3:
                raise RuntimeError(
                    f"renderer root_offset has invalid shape {offsets.shape}"
                )
            position += offsets[0]
        position += np.asarray(
            getattr(self._renderer, "render_root_offset", np.zeros(3)),
            dtype=np.float64,
        )
        quat = np.asarray(qpos_batch[0, 3:7], dtype=np.float64).copy()
        norm = float(np.linalg.norm(quat))
        if not np.isfinite(norm) or norm <= 1.0e-12:
            raise ValueError("qpos root quaternion must have non-zero finite norm")
        quat /= norm
        return position, quat
