from typing import Any

import pytest

from navila_orca.live_guide import (
    ActionLimits,
    ActionName,
    ActionValidator,
    MissionLimits,
    ProposedAction,
    SafetySnapshot,
    SafetyStopReason,
    to_velocity_command,
)


def _validator(*, allowed_actions=None):
    kwargs = {}
    if allowed_actions is not None:
        kwargs["allowed_actions"] = frozenset(allowed_actions)
    return ActionValidator(
        ActionLimits(
            mission=MissionLimits(
                max_steps=10,
                timeout_s=60.0,
                max_observation_age_s=1.0,
            ),
            max_linear_speed_mps=0.5,
            max_angular_speed_rad_s=0.6,
            max_action_duration_s=1.5,
            **kwargs,
        )
    )


def _snapshot(**changes):
    values = {
        "mission_step": 2,
        "elapsed_s": 12.0,
        "now_s": 20.0,
        "observation_timestamp_s": 19.5,
        "communication_ok": True,
        "emergency_stop_requested": False,
    }
    values.update(changes)
    return SafetySnapshot(**values)


def test_allowlisted_bounded_action_is_accepted_and_adapts_to_runner_contract():
    decision = _validator().validate(
        ProposedAction("move_forward", linear_mps=0.4, duration_s=1.0),
        _snapshot(),
    )

    assert decision.accepted is True
    assert decision.reason is None
    assert decision.action.name is ActionName.MOVE_FORWARD
    command = to_velocity_command(decision)
    assert (command.vx, command.vy, command.wz, command.duration_s) == pytest.approx(
        (0.4, 0.0, 0.0, 1.0)
    )
    assert command.stop is False


@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        (ProposedAction("sidestep", linear_mps=0.1, duration_s=0.5), SafetyStopReason.ACTION_NOT_ALLOWED),
        (ProposedAction("move_forward", linear_mps=0.7, duration_s=0.5), SafetyStopReason.ACTION_OUT_OF_BOUNDS),
        (ProposedAction("turn_left", angular_rad_s=-0.4, duration_s=0.5), SafetyStopReason.ACTION_OUT_OF_BOUNDS),
    ],
)
def test_unknown_or_out_of_bounds_actions_fail_closed_to_stop(proposal, reason):
    decision = _validator().validate(proposal, _snapshot())

    assert decision.accepted is False
    assert decision.reason is reason
    assert decision.action.name is ActionName.STOP
    assert decision.action.linear_mps == 0.0
    assert decision.action.angular_rad_s == 0.0
    assert decision.action.duration_s == 0.0
    assert to_velocity_command(decision).stop is True


@pytest.mark.parametrize("field_name", ["linear_mps", "angular_rad_s", "duration_s"])
def test_malformed_numeric_fields_fail_closed(field_name):
    values: dict[str, Any] = {
        "linear_mps": 0.2,
        "angular_rad_s": 0.0,
        "duration_s": 0.5,
    }
    values[field_name] = "not-a-number"

    decision = _validator().validate(
        ProposedAction("move_forward", **values),
        _snapshot(),
    )

    assert decision.accepted is False
    assert decision.reason is SafetyStopReason.ACTION_OUT_OF_BOUNDS
    assert decision.action.name is ActionName.STOP
    assert to_velocity_command(decision).stop is True


def test_policy_allowlist_can_disable_an_otherwise_valid_action():
    decision = _validator(allowed_actions={ActionName.STOP, ActionName.MOVE_FORWARD}).validate(
        ProposedAction("turn_right", angular_rad_s=-0.4, duration_s=0.5),
        _snapshot(),
    )

    assert decision.accepted is False
    assert decision.reason is SafetyStopReason.ACTION_NOT_ALLOWED


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"emergency_stop_requested": True}, SafetyStopReason.EMERGENCY_STOP),
        ({"communication_ok": False}, SafetyStopReason.COMMUNICATION_LOSS),
        ({"mission_step": 10}, SafetyStopReason.MAX_STEPS),
        ({"elapsed_s": 60.0}, SafetyStopReason.TIMEOUT),
        (
            {"now_s": 20.0, "observation_timestamp_s": 18.9},
            SafetyStopReason.STALE_OBSERVATION,
        ),
        (
            {"now_s": 20.0, "observation_timestamp_s": 20.1},
            SafetyStopReason.STALE_OBSERVATION,
        ),
    ],
)
def test_mission_safety_conditions_override_motion_with_stop(changes, reason):
    decision = _validator().validate(
        ProposedAction("move_forward", linear_mps=0.4, duration_s=1.0),
        _snapshot(**changes),
    )

    assert decision.accepted is False
    assert decision.reason is reason
    assert to_velocity_command(decision).stop is True
