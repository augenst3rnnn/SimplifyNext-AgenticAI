"""Fail-closed validation for actions proposed during live guidance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math

from ..contracts import VelocityCommand
from .contracts import MissionLimits


class ActionName(str, Enum):
    STOP = "stop"
    MOVE_FORWARD = "move_forward"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"


class SafetyStopReason(str, Enum):
    EMERGENCY_STOP = "emergency_stop"
    COMMUNICATION_LOSS = "communication_loss"
    STALE_OBSERVATION = "stale_observation"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    ACTION_NOT_ALLOWED = "action_not_allowed"
    ACTION_OUT_OF_BOUNDS = "action_out_of_bounds"


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """Untrusted high-level action awaiting safety validation."""

    name: str
    linear_mps: float = 0.0
    angular_rad_s: float = 0.0
    duration_s: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name).strip().lower())
        for field_name in ("linear_mps", "angular_rad_s", "duration_s"):
            raw_value = getattr(self, field_name)
            try:
                value = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                value = math.nan
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class SafeAction:
    """An allowlisted bounded action, or the validator's zero stop action."""

    name: ActionName
    linear_mps: float = 0.0
    angular_rad_s: float = 0.0
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class SafetySnapshot:
    """Safety-relevant mission and observation state at one decision."""

    mission_step: int
    elapsed_s: float
    now_s: float
    observation_timestamp_s: float
    communication_ok: bool
    emergency_stop_requested: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.mission_step, bool)
            or int(self.mission_step) != self.mission_step
            or self.mission_step < 0
        ):
            raise ValueError("mission_step must be a non-negative integer")
        object.__setattr__(self, "mission_step", int(self.mission_step))
        for field_name in ("elapsed_s", "now_s", "observation_timestamp_s"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, value)
        if self.elapsed_s < 0.0:
            raise ValueError("elapsed_s must be non-negative")
        if not isinstance(self.communication_ok, bool):
            raise TypeError("communication_ok must be a bool")
        if not isinstance(self.emergency_stop_requested, bool):
            raise TypeError("emergency_stop_requested must be a bool")


@dataclass(frozen=True, slots=True)
class ActionLimits:
    """Allowlist, motion bounds, and mission safety limits."""

    mission: MissionLimits
    max_linear_speed_mps: float
    max_angular_speed_rad_s: float
    max_action_duration_s: float
    allowed_actions: frozenset[ActionName] = field(
        default_factory=lambda: frozenset(ActionName)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.mission, MissionLimits):
            raise TypeError("mission must be MissionLimits")
        for field_name in (
            "max_linear_speed_mps",
            "max_angular_speed_rad_s",
            "max_action_duration_s",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{field_name} must be positive and finite")
            object.__setattr__(self, field_name, value)
        try:
            allowed = frozenset(ActionName(value) for value in self.allowed_actions)
        except ValueError as exc:
            raise ValueError("allowed_actions contains an unknown action") from exc
        if ActionName.STOP not in allowed:
            raise ValueError("allowed_actions must include stop")
        object.__setattr__(self, "allowed_actions", allowed)


@dataclass(frozen=True, slots=True)
class ActionValidation:
    """Safety decision containing the only action safe to execute next."""

    accepted: bool
    action: SafeAction
    reason: SafetyStopReason | None = None


class ActionValidator:
    """Apply safety stops before validating action vocabulary and bounds."""

    def __init__(self, limits: ActionLimits) -> None:
        if not isinstance(limits, ActionLimits):
            raise TypeError("limits must be ActionLimits")
        self.limits = limits

    def validate(
        self,
        proposal: ProposedAction,
        snapshot: SafetySnapshot,
    ) -> ActionValidation:
        if not isinstance(proposal, ProposedAction):
            raise TypeError("proposal must be a ProposedAction")
        if not isinstance(snapshot, SafetySnapshot):
            raise TypeError("snapshot must be a SafetySnapshot")

        safety_reason = self._safety_stop(snapshot)
        if safety_reason is not None:
            return _stopped(safety_reason)

        try:
            action_name = ActionName(proposal.name)
        except ValueError:
            return _stopped(SafetyStopReason.ACTION_NOT_ALLOWED)
        if action_name not in self.limits.allowed_actions:
            return _stopped(SafetyStopReason.ACTION_NOT_ALLOWED)
        if not self._within_bounds(action_name, proposal):
            return _stopped(SafetyStopReason.ACTION_OUT_OF_BOUNDS)
        return ActionValidation(
            accepted=True,
            action=SafeAction(
                action_name,
                proposal.linear_mps,
                proposal.angular_rad_s,
                proposal.duration_s,
            ),
        )

    def _safety_stop(self, snapshot: SafetySnapshot) -> SafetyStopReason | None:
        if snapshot.emergency_stop_requested:
            return SafetyStopReason.EMERGENCY_STOP
        if not snapshot.communication_ok:
            return SafetyStopReason.COMMUNICATION_LOSS
        observation_age = snapshot.now_s - snapshot.observation_timestamp_s
        if (
            observation_age < 0.0
            or observation_age > self.limits.mission.max_observation_age_s
        ):
            return SafetyStopReason.STALE_OBSERVATION
        if snapshot.mission_step >= self.limits.mission.max_steps:
            return SafetyStopReason.MAX_STEPS
        if snapshot.elapsed_s >= self.limits.mission.timeout_s:
            return SafetyStopReason.TIMEOUT
        return None

    def _within_bounds(self, name: ActionName, proposal: ProposedAction) -> bool:
        values = (
            proposal.linear_mps,
            proposal.angular_rad_s,
            proposal.duration_s,
        )
        if not all(math.isfinite(value) for value in values):
            return False
        if name is ActionName.STOP:
            return values == (0.0, 0.0, 0.0)
        if not 0.0 < proposal.duration_s <= self.limits.max_action_duration_s:
            return False
        if name is ActionName.MOVE_FORWARD:
            return (
                0.0 < proposal.linear_mps <= self.limits.max_linear_speed_mps
                and proposal.angular_rad_s == 0.0
            )
        if proposal.linear_mps != 0.0:
            return False
        if name is ActionName.TURN_LEFT:
            return 0.0 < proposal.angular_rad_s <= self.limits.max_angular_speed_rad_s
        return (
            -self.limits.max_angular_speed_rad_s
            <= proposal.angular_rad_s
            < 0.0
        )


def _stopped(reason: SafetyStopReason) -> ActionValidation:
    return ActionValidation(False, SafeAction(ActionName.STOP), reason)


def to_velocity_command(decision: ActionValidation) -> VelocityCommand:
    """Adapt a validated live-guide action to the existing runner contract."""

    if not isinstance(decision, ActionValidation):
        raise TypeError("decision must be an ActionValidation")
    action = decision.action
    return VelocityCommand(
        vx=action.linear_mps,
        vy=0.0,
        wz=action.angular_rad_s,
        duration_s=action.duration_s,
        stop=action.name is ActionName.STOP,
    )
