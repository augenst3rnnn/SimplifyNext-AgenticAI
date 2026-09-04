import math

import pytest

from navila_orca.actions import (
    ActionParseError,
    AmbiguousActionError,
    parse_velocity_command,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The next action is move forward 25 cm.", (0.5, 0.0, 0.0, 0.5, False)),
        ("Move forward by 50 centimetres", (0.5, 0.0, 0.0, 1.0, False)),
        ("move forward 75cm", (0.5, 0.0, 0.0, 1.5, False)),
        (
            "The next action is turn left 15 degrees.",
            (0.0, 0.0, math.pi / 6, 0.5, False),
        ),
        ("turn left by 30 degree", (0.0, 0.0, math.pi / 6, 1.0, False)),
        ("TURN RIGHT 45-DEGREES", (0.0, 0.0, -math.pi / 6, 1.5, False)),
        ("The next action is stop.", (0.0, 0.0, 0.0, 0.0, True)),
    ],
)
def test_parse_canonical_actions(text, expected):
    command = parse_velocity_command(text)
    assert (command.vx, command.vy, command.wz, command.duration_s) == pytest.approx(
        expected[:-1]
    )
    assert command.stop is expected[-1]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "continue onward",
        "move forward",
        "move forward 30 cm",
        "turn left 20 degrees",
    ],
)
def test_unknown_or_noncanonical_actions_fail_closed(text):
    with pytest.raises(ActionParseError):
        parse_velocity_command(text)


def test_multiple_actions_are_rejected_as_ambiguous():
    with pytest.raises(AmbiguousActionError):
        parse_velocity_command("move forward 25 cm, then stop")
