"""MJLab/MJWarp Go2 locomotion backend.

The heavyweight simulator stack is imported only when :meth:`reset` (or
:meth:`start`) is first called.  This keeps the rest of navila-orca usable in a
plain CPU Python environment.
"""

from __future__ import annotations

from dataclasses import asdict
import importlib
from pathlib import Path
from typing import Any

import numpy as np

from navila_orca.paths import (
    BUNDLED_GO2_XML,
    DEFAULT_GO2_CHECKPOINT,
    GO2_TASK_PACKAGE,
)


DEFAULT_CHECKPOINT = DEFAULT_GO2_CHECKPOINT

GO2_QPOS_JOINT_ORDER = (
    "FL_hip_joint",
    "FL_thigh_joint",
    "FL_calf_joint",
    "FR_hip_joint",
    "FR_thigh_joint",
    "FR_calf_joint",
    "RL_hip_joint",
    "RL_thigh_joint",
    "RL_calf_joint",
    "RR_hip_joint",
    "RR_thigh_joint",
    "RR_calf_joint",
)

# MJLab resolves the three actuator groups back into the entity's natural joint
# order before routing the checkpoint's 12 actions.  Orca's generated actuator
# section is not used to infer qpos addresses; renderer scatter stays name-based.
GO2_POLICY_ACTION_ORDER = GO2_QPOS_JOINT_ORDER


class MjlabDependencyError(RuntimeError):
    """Raised when the optional MJLab/MJWarp runtime cannot be loaded."""


def _numpy(tensor: Any) -> np.ndarray:
    """Copy a Torch/MJWarp-backed value to ordinary host memory."""

    value = tensor.detach() if hasattr(tensor, "detach") else tensor
    value = value.cpu() if hasattr(value, "cpu") else value
    return np.array(value.numpy() if hasattr(value, "numpy") else value, copy=True)


def _quat_wxyz_to_rpy(quat: np.ndarray) -> np.ndarray:
    """Convert one scalar-first quaternion to roll/pitch/yaw."""

    w, x, y, z = (float(v) for v in quat)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = np.arctan2(sinr_cosp, cosr_cosp)
    sinp = np.clip(2.0 * (w * y - z * x), -1.0, 1.0)
    pitch = np.arcsin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = np.arctan2(siny_cosp, cosy_cosp)
    return np.asarray([roll, pitch, yaw], dtype=np.float64)


def _robot_state_type():
    # Delayed to preserve optional-dependency import semantics and to avoid an
    # import-order dependency while the common contracts module is initialized.
    from navila_orca.contracts import RobotState

    return RobotState


def _physics_step_type():
    from navila_orca.contracts import PhysicsStep

    return PhysicsStep


class MjlabGo2Backend:
    """One-world Go2 policy runner using MJLab's real MJWarp GPU simulator.

    Parameters are intentionally explicit and default to the locally validated
    Unitree Go2 task/checkpoint pair.  Construction is cheap; simulator and
    checkpoint initialization happens on first use.
    """

    def __init__(
        self,
        *,
        task_id: str = "Unitree-Go2-Flat",
        checkpoint: str | Path = DEFAULT_CHECKPOINT,
        device: str = "cuda:0",
        num_envs: int = 1,
        deterministic_play: bool = True,
        warmup_steps: int = 100,
    ) -> None:
        if int(num_envs) < 1:
            raise ValueError("num_envs must be positive")
        if int(warmup_steps) < 0:
            raise ValueError("warmup_steps must be non-negative")
        self.task_id = task_id
        self.checkpoint = Path(checkpoint).expanduser()
        self.device = device
        self.num_envs = int(num_envs)
        self.deterministic_play = bool(deterministic_play)
        self.warmup_steps = int(warmup_steps)

        self._env: Any | None = None
        self._policy: Any | None = None
        self._runner: Any | None = None
        self._obs: Any | None = None
        self._torch: Any | None = None
        self._step_id = 0
        self._velocity_command = np.zeros(3, dtype=np.float32)

    @property
    def started(self) -> bool:
        return self._env is not None

    @property
    def step_dt(self) -> float:
        self._ensure_started()
        return float(self._env.unwrapped.step_dt)

    @property
    def control_dt(self) -> float:
        """Navigation-runner name for MJLab's policy step interval."""

        return self.step_dt

    @property
    def qpos_batch(self) -> np.ndarray:
        """Current MJWarp generalized position as ``[num_envs, nq]``."""

        self._ensure_started()
        return _numpy(self._env.unwrapped.sim.data.qpos)

    @property
    def joint_qpos_addr(self) -> dict[str, int]:
        """Local MuJoCo qpos address for every named Go2 hinge joint."""

        self._ensure_started()
        robot = self._env.unwrapped.scene["robot"]
        addresses = _numpy(robot.data.indexing.joint_q_adr).reshape(-1)
        if len(robot.joint_names) != len(addresses):
            raise RuntimeError(
                "MJLab robot joint metadata is inconsistent: "
                f"{len(robot.joint_names)} names for {len(addresses)} qpos addresses"
            )
        mapping = {
            str(name): int(address)
            for name, address in zip(robot.joint_names, addresses, strict=True)
        }
        missing = sorted(set(GO2_QPOS_JOINT_ORDER).difference(mapping))
        extra = sorted(set(mapping).difference(GO2_QPOS_JOINT_ORDER))
        if missing or extra or len(set(mapping.values())) != len(mapping):
            raise RuntimeError(
                "Go2 local XML does not match the 12-joint locomotion ABI: "
                f"missing={missing}, extra={extra}, qpos_addr={mapping}"
            )
        if any(address < 7 for address in mapping.values()):
            raise RuntimeError(f"Go2 hinge qpos overlaps root qpos[0:7]: {mapping}")
        return mapping

    @property
    def alignment_report(self) -> dict[str, Any]:
        """Return the effective local physics/XML contract used by the policy."""

        self._ensure_started()
        model = self._env.unwrapped.sim.mj_model
        opt = model.opt
        mujoco = importlib.import_module("mujoco")
        terrain_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "terrain")
        if terrain_id < 0:
            raise RuntimeError("ground geom 'terrain' disappeared after startup")
        return {
            "verified": True,
            "task_id": self.task_id,
            "source_xml": str(
                (
                    BUNDLED_GO2_XML
                ).resolve()
            ),
            "checkpoint": str(self.checkpoint.resolve()),
            "deterministic_play": self.deterministic_play,
            "warmup_steps": self.warmup_steps,
            "qpos_root": [0, 7],
            "qpos_joint_order": list(GO2_QPOS_JOINT_ORDER),
            "qpos_joint_addr": self.joint_qpos_addr,
            "policy_action_order": list(GO2_POLICY_ACTION_ORDER),
            "mujoco": {
                "version": mujoco.__version__,
                "timestep": float(opt.timestep),
                "integrator": int(opt.integrator),
                "gravity": [float(value) for value in opt.gravity],
                "density": float(opt.density),
                "viscosity": float(opt.viscosity),
                "iterations": int(opt.iterations),
                "ls_iterations": int(opt.ls_iterations),
                "noslip_iterations": int(opt.noslip_iterations),
                "ccd_iterations": int(opt.ccd_iterations),
                "ground": {
                    "name": "terrain",
                    "friction": model.geom_friction[terrain_id].tolist(),
                    "solref": model.geom_solref[terrain_id].tolist(),
                    "solimp": model.geom_solimp[terrain_id].tolist(),
                    "contype": int(model.geom_contype[terrain_id]),
                    "conaffinity": int(model.geom_conaffinity[terrain_id]),
                    "condim": int(model.geom_condim[terrain_id]),
                },
            },
            "control_dt": self.control_dt,
        }

    def start(self) -> None:
        """Load MJLab, construct MJWarp, and restore the trained actor."""

        if self.started:
            return
        if not self.checkpoint.is_file():
            raise FileNotFoundError(f"Go2 checkpoint does not exist: {self.checkpoint}")

        try:
            torch = importlib.import_module("torch")
            if self.device.startswith("cuda") and not torch.cuda.is_available():
                raise MjlabDependencyError(
                    f"device={self.device!r} requests CUDA, but Torch reports no CUDA device"
                )
            # Register the project-owned Unitree Go2 task with MJLab.
            importlib.import_module(GO2_TASK_PACKAGE)

            from mjlab.envs import ManagerBasedRlEnv
            from mjlab.rl import MjlabOnPolicyRunner, RslRlVecEnvWrapper
            from mjlab.tasks.registry import (
                load_env_cfg,
                load_rl_cfg,
                load_runner_cls,
            )
            from mjlab.utils.torch import configure_torch_backends

            configure_torch_backends()
            env_cfg = load_env_cfg(self.task_id, play=True)
            if self.deterministic_play:
                self._apply_deterministic_play_overrides(env_cfg)
            agent_cfg = load_rl_cfg(self.task_id)
            env_cfg.scene.num_envs = self.num_envs
            raw_env = ManagerBasedRlEnv(
                cfg=env_cfg, device=self.device, render_mode=None
            )
            self._assert_flat_physics_profile(raw_env)
            self._assert_go2_action_order(raw_env)
            env = RslRlVecEnvWrapper(raw_env, clip_actions=agent_cfg.clip_actions)
            runner_cls = load_runner_cls(self.task_id) or MjlabOnPolicyRunner
            runner = runner_cls(env, asdict(agent_cfg), device=self.device)
            runner.load(
                str(self.checkpoint),
                load_cfg={"actor": True},
                strict=True,
                map_location=self.device,
            )
            policy = runner.get_inference_policy(device=self.device)
        except MjlabDependencyError:
            raise
        except (ImportError, ModuleNotFoundError) as exc:
            raise MjlabDependencyError(
                "MJLab Go2 is optional and could not be imported. Activate the "
                "local 'orcalab' environment and ensure mjlab, mujoco-warp, "
                "rsl-rl, and the bundled Go2 task sources are available. "
                f"Original error: {exc}"
            ) from exc
        except Exception:
            # A partially constructed environment owns GPU allocations.
            candidate = locals().get("env")
            if candidate is None:
                candidate = locals().get("raw_env")
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            raise

        self._torch = torch
        self._env = env
        self._runner = runner
        self._policy = policy
        self._obs, _ = env.reset()
        self._step_id = 0
        self._apply_velocity_command(refresh_observation=True)

    def reset(self, episode: Any | None = None) -> Any:
        """Reset all worlds and return the first world's public RobotState."""

        # Episode poses/scenes cannot be applied until converted Orca/MJCF
        # assets exist.  Accept the engine-neutral episode now so this backend
        # already satisfies the core runner contract.
        del episode
        self._ensure_started()
        self._obs, _ = self._env.reset()
        self._step_id = 0
        self._run_zero_velocity_warmup()
        return self._state()

    def _run_zero_velocity_warmup(self) -> None:
        """Settle Go2 after reset using the original NaVILA warm-up protocol.

        The Isaac Lab evaluation wrapper advances the frozen Go2 policy for 100
        steps at zero command before starting navigation.  Keep those simulator
        steps outside the public navigation clock and restore any queued command
        only after the policy, robot state, and observation history have settled.
        """

        requested_command = self._velocity_command.copy()
        self._velocity_command = np.zeros(3, dtype=np.float32)
        try:
            self._apply_velocity_command(refresh_observation=True)
            with self._torch.no_grad():
                for _ in range(self.warmup_steps):
                    self._apply_velocity_command(refresh_observation=False)
                    action = self._policy(self._obs)
                    self._obs, _reward, _done, _extras = self._env.step(action)
        finally:
            self._velocity_command = requested_command
            self._step_id = 0
            self._apply_velocity_command(refresh_observation=True)

    def set_velocity_command(
        self, vx: float | np.ndarray, vy: float | None = None, wz: float | None = None
    ) -> None:
        """Set body-frame ``[vx, vy, yaw_rate]`` for every world.

        Either pass three scalars or one length-three array.
        """

        if vy is None and wz is None:
            if all(hasattr(vx, name) for name in ("vx", "vy", "wz")):
                command = np.asarray([vx.vx, vx.vy, vx.wz], dtype=np.float32)
            else:
                command = np.asarray(vx, dtype=np.float32)
        elif vy is not None and wz is not None:
            command = np.asarray([vx, vy, wz], dtype=np.float32)
        else:
            raise ValueError("provide either [vx, vy, wz] or all three scalars")
        if command.shape != (3,) or not np.all(np.isfinite(command)):
            raise ValueError("velocity command must be a finite length-three vector")
        self._velocity_command = command.copy()
        if self.started:
            self._apply_velocity_command(refresh_observation=True)

    def step(self) -> Any:
        """Run one trained-policy step and preserve termination information.

        MJLab auto-resets finished vector environments before returning. The
        returned state is therefore marked ``auto_reset_state`` on a terminal
        step so the navigation runner does not count the reset teleport as
        travelled path.
        """

        self._ensure_started()
        self._apply_velocity_command(refresh_observation=False)
        with self._torch.no_grad():
            action = self._policy(self._obs)
            self._obs, reward, done, _extras = self._env.step(action)
        self._step_id += 1
        unwrapped = self._env.unwrapped
        terminated_buffer = getattr(unwrapped, "reset_terminated", None)
        truncated_buffer = getattr(unwrapped, "reset_time_outs", None)
        terminated = (
            bool(_numpy(terminated_buffer).reshape(-1)[0])
            if terminated_buffer is not None
            else False
        )
        truncated = (
            bool(_numpy(truncated_buffer).reshape(-1)[0])
            if truncated_buffer is not None
            else False
        )
        wrapper_done = bool(_numpy(done).reshape(-1)[0])
        if wrapper_done and not (terminated or truncated):
            # Preserve fail-safe termination if a future wrapper stops exposing
            # the two underlying buffers separately.
            terminated = True
        step_type = _physics_step_type()
        return step_type(
            state=self._state(),
            reward=float(_numpy(reward).reshape(-1)[0]),
            terminated=terminated,
            truncated=truncated,
            info={"auto_reset_state": terminated or truncated},
        )

    policy_step = step

    @staticmethod
    def _apply_deterministic_play_overrides(env_cfg: Any) -> None:
        """Remove training randomization before the simulator is constructed."""

        events = env_cfg.events
        reset_base = events.get("reset_base")
        if reset_base is None:
            raise RuntimeError("Unitree-Go2-Flat is missing its reset_base event")
        reset_base.params["pose_range"] = {}
        reset_base.params["velocity_range"] = {}
        for name in ("push_robot", "foot_friction", "encoder_bias", "base_com"):
            events.pop(name, None)

    @staticmethod
    def _assert_flat_physics_profile(raw_env: Any) -> None:
        """Fail before policy play if MJLab's effective MuJoCo options drift."""

        mujoco = importlib.import_module("mujoco")
        opt = raw_env.sim.mj_model.opt
        expected = {
            "timestep": 0.005,
            "integrator": int(mujoco.mjtIntegrator.mjINT_IMPLICITFAST),
            "iterations": 10,
            "ls_iterations": 20,
            "ccd_iterations": 50,
            "density": 0.0,
            "viscosity": 0.0,
        }
        actual = {
            "timestep": float(opt.timestep),
            "integrator": int(opt.integrator),
            "iterations": int(opt.iterations),
            "ls_iterations": int(opt.ls_iterations),
            "ccd_iterations": int(opt.ccd_iterations),
            "density": float(opt.density),
            "viscosity": float(opt.viscosity),
        }
        mismatches = []
        for name, wanted in expected.items():
            got = actual[name]
            if isinstance(wanted, float):
                matched = abs(float(got) - wanted) <= 1.0e-9
            else:
                matched = got == wanted
            if not matched:
                mismatches.append(f"{name}={got!r} expected {wanted!r}")
        if not np.allclose(opt.gravity, [0.0, 0.0, -9.81], atol=1.0e-9):
            mismatches.append(
                f"gravity={opt.gravity.tolist()!r} expected [0.0, 0.0, -9.81]"
            )
        terrain_id = mujoco.mj_name2id(
            raw_env.sim.mj_model, mujoco.mjtObj.mjOBJ_GEOM, "terrain"
        )
        if terrain_id < 0:
            mismatches.append("ground geom 'terrain' is missing")
        else:
            model = raw_env.sim.mj_model
            ground_expectations = {
                "friction": (model.geom_friction[terrain_id], [1.0, 0.005, 0.0001]),
                "solref": (model.geom_solref[terrain_id], [0.02, 1.0]),
                "solimp": (
                    model.geom_solimp[terrain_id],
                    [0.9, 0.95, 0.001, 0.5, 2.0],
                ),
            }
            for name, (got, wanted) in ground_expectations.items():
                if not np.allclose(got, wanted, atol=1.0e-9):
                    mismatches.append(
                        f"terrain.{name}={got.tolist()!r} expected {wanted!r}"
                    )
            if int(model.geom_condim[terrain_id]) != 3:
                mismatches.append(
                    f"terrain.condim={int(model.geom_condim[terrain_id])} expected 3"
                )
            if int(model.geom_contype[terrain_id]) != 1:
                mismatches.append(
                    f"terrain.contype={int(model.geom_contype[terrain_id])} expected 1"
                )
            if int(model.geom_conaffinity[terrain_id]) != 1:
                mismatches.append(
                    "terrain.conaffinity="
                    f"{int(model.geom_conaffinity[terrain_id])} expected 1"
                )
        if mismatches:
            raise RuntimeError(
                "Unitree-Go2-Flat MuJoCo profile is not aligned: "
                + "; ".join(mismatches)
            )

    @staticmethod
    def _assert_go2_action_order(raw_env: Any) -> None:
        """Fail before checkpoint play if its 12-action ABI has drifted."""

        action_term = raw_env.action_manager.get_term("joint_pos")
        actual = tuple(str(name) for name in action_term.target_names)
        if actual != GO2_POLICY_ACTION_ORDER:
            raise RuntimeError(
                "Unitree-Go2-Flat policy action order is not aligned: "
                f"actual={actual}, expected={GO2_POLICY_ACTION_ORDER}"
            )

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
        self._env = None
        self._policy = None
        self._runner = None
        self._obs = None
        self._torch = None

    def __enter__(self) -> "MjlabGo2Backend":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if not self.started:
            self.start()

    def _apply_velocity_command(self, *, refresh_observation: bool) -> None:
        term = self._env.unwrapped.command_manager.get_term("twist")
        command = self._torch.as_tensor(
            self._velocity_command, device=self.device, dtype=term.vel_command_b.dtype
        )
        term.vel_command_b[:] = command
        # Prevent CommandManager.compute() from replacing external navigation
        # commands with a newly sampled command.
        term.time_left.fill_(float("inf"))
        if hasattr(term, "is_standing_env"):
            term.is_standing_env.fill_(False)
        if hasattr(term, "is_heading_env"):
            term.is_heading_env.fill_(False)
        if refresh_observation:
            self._obs = self._env.get_observations()

    def _state(self) -> Any:
        robot = self._env.unwrapped.scene["robot"]
        quat = _numpy(robot.data.root_link_quat_w[0]).reshape(4)
        state_type = _robot_state_type()
        return state_type(
            step_id=self._step_id,
            sim_time_s=self._step_id * float(self._env.unwrapped.step_dt),
            root_pos_world=_numpy(robot.data.root_link_pos_w[0]).reshape(3),
            root_quat_wxyz=quat,
            body_ang_vel=_numpy(robot.data.root_link_ang_vel_b[0]).reshape(3),
            base_rpy=_quat_wxyz_to_rpy(quat),
            joint_pos=_numpy(robot.data.joint_pos[0]).reshape(-1),
            joint_vel=_numpy(robot.data.joint_vel[0]).reshape(-1),
            last_raw_action=_numpy(
                self._env.unwrapped.action_manager.action[0]
            ).reshape(-1),
        )
