"""Explicit operator confirmation boundary; no autonomous localization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import time
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class ProgressRequest:
    request_id: str
    activation_id: str
    route_id: str
    segment_id: str
    kind: str
    node_id: str
    step_id: int


@dataclass(frozen=True, slots=True)
class ProgressConfirmation:
    request: ProgressRequest
    event_id: str
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, ProgressRequest):
            raise TypeError("confirmation requires a ProgressRequest")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("confirmation requires an event_id")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("confirmation requires an explicit source")


class ProgressSource(Protocol):
    def wait(self, request: ProgressRequest, *, timeout_s: float,
             on_poll: Callable[[], None]) -> ProgressConfirmation: ...


class FlagFileProgressSource:
    """Publish a request and wait for an exact, explicitly written acknowledgment.

    The request file is informational. Only a separate operator-written event
    file can confirm it. Polling does not step physics or infer node arrival.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.request_path = self.path.with_name(self.path.name + ".request.json")
        self._used_events: set[str] = set()

    def wait(self, request: ProgressRequest, *, timeout_s: float,
             on_poll: Callable[[], None]) -> ProgressConfirmation:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("confirmation timeout must be positive and finite")
        expected = asdict(request)
        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.request_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(expected, indent=2), encoding="utf-8")
        temporary.replace(self.request_path)
        print("LIVEGUIDE_CONFIRMATION_REQUIRED " + json.dumps(expected), flush=True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            on_poll()  # RouteProof can still block a route while stopped.
            if time.monotonic() >= deadline:
                break
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and all(
                    type(raw.get(k)) is type(v) and raw[k] == v for k, v in expected.items()
                ):
                    confirmation = ProgressConfirmation(request, raw.get("event_id"), raw.get("source"))
                    if confirmation.event_id not in self._used_events:
                        self._used_events.add(confirmation.event_id)
                        return confirmation
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                pass  # Missing, partial or stale input is never confirmation.
            time.sleep(0.1)
        raise TimeoutError("explicit route progress confirmation timed out")
