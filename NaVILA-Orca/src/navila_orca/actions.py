"""Strict parsing of NaVILA's textual high-level action vocabulary."""

from __future__ import annotations

import re

from .contracts import VelocityCommand


class ActionParseError(ValueError):
    """Raised when a VLM response cannot be mapped to one safe action."""


class AmbiguousActionError(ActionParseError):
    """Raised when a response contains more than one action phrase."""


_STOP_RE = re.compile(r"\bstop\b", re.IGNORECASE)
_MOVE_RE = re.compile(
    r"\bmove\s+forward(?:\s+by)?\s+(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>cm|centimet(?:er|re)s?)\b",
    re.IGNORECASE,
)
_TURN_RE = re.compile(
    r"\bturn\s+(?P<direction>left|right)(?:\s+by)?\s+(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?:-|\s)?degrees?\b",
    re.IGNORECASE,
)

_FORWARD_DURATIONS = {25.0: 0.5, 50.0: 1.0, 75.0: 1.5}
_TURN_DURATIONS = {15.0: 0.5, 30.0: 1.0, 45.0: 1.5}


def _canonical_amount(raw: str, allowed: dict[float, float], action_name: str) -> float:
    amount = float(raw)
    for candidate in allowed:
        if amount == candidate:
            return candidate
    choices = ", ".join(f"{value:g}" for value in allowed)
    raise ActionParseError(
        f"unsupported {action_name} amount {amount:g}; expected one of {choices}"
    )


def parse_velocity_command(text: str) -> VelocityCommand:
    """Parse exactly one canonical NaVILA action.

    Unlike the original benchmark helper, malformed or unrecognized output is
    never converted to a silent forward command.  The caller must decide how to
    recover from :class:`ActionParseError`.
    """

    if not isinstance(text, str) or not text.strip():
        raise ActionParseError("VLM response is empty")

    matches: list[tuple[str, re.Match[str]]] = []
    matches.extend(("stop", match) for match in _STOP_RE.finditer(text))
    matches.extend(("move", match) for match in _MOVE_RE.finditer(text))
    matches.extend(("turn", match) for match in _TURN_RE.finditer(text))
    matches.sort(key=lambda item: item[1].start())

    if not matches:
        raise ActionParseError(
            f"no canonical NaVILA action found in response: {text!r}"
        )
    if len(matches) != 1:
        descriptions = ", ".join(match.group(0) for _, match in matches)
        raise AmbiguousActionError(
            f"expected one action, found {len(matches)}: {descriptions}"
        )

    kind, match = matches[0]
    if kind == "stop":
        return VelocityCommand(0.0, 0.0, 0.0, 0.0, stop=True)
    if kind == "move":
        amount = _canonical_amount(match.group("amount"), _FORWARD_DURATIONS, "forward")
        return VelocityCommand(0.5, 0.0, 0.0, _FORWARD_DURATIONS[amount])

    amount = _canonical_amount(match.group("amount"), _TURN_DURATIONS, "turn")
    direction = 1.0 if match.group("direction").lower() == "left" else -1.0
    return VelocityCommand(
        0.0, 0.0, direction * 3.141592653589793 / 6.0, _TURN_DURATIONS[amount]
    )
