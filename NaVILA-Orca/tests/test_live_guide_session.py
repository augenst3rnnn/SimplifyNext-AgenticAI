from dataclasses import asdict, replace
import json

import pytest

from navila_orca.live_guide import (
    ApprovedRouteSession, FlagFileProgressSource, GuidedRoute, ProgressConfirmation,
    RouteCatalog, RouteNode, RouteSegment, load_route_catalog,
)
from test_routeproof import _state


def catalog():
    return RouteCatalog(
        tuple(RouteNode(name, name) for name in ("start", "junction", "goal")),
        (
            GuidedRoute("a", "goal", (
                RouteSegment("a1", "start", "junction", "the junction", "Continue"),
                RouteSegment("a2", "junction", "goal", "the blue barrel", "Turn right"),
            )),
            GuidedRoute("b", "goal", (
                RouteSegment("b1", "junction", "goal", "the blue barrel", "Take the approved detour"),
            )),
        ),
    )


def confirm(guide, kind, state, event_id):
    request = guide.request_confirmation(kind, state)
    guide.confirm(ProgressConfirmation(request, event_id, "test operator"), state)


def test_only_explicit_confirmation_advances_and_completes():
    guide = ApprovedRouteSession(catalog(), frozenset({"a"}))
    state = _state(0)
    guide.start_route("a", state)
    with pytest.raises(RuntimeError):
        guide.get_next_instruction()
    confirm(guide, "entry", state, "entry")
    assert "junction" in guide.get_next_instruction()
    guide.update_progress(_state(30, (20, 30, 0)))
    assert guide.segment_index == 0  # Neither displacement nor elapsed ticks prove arrival.
    state = _state(30)
    confirm(guide, "complete", state, "first")
    assert guide.current_segment().segment_id == "a2"
    confirm(guide, "complete", state, "second")
    assert guide.is_complete()
    assert guide.current_segment() is None


def test_unapproved_switch_rejected_without_replacing_active_state():
    guide = ApprovedRouteSession(catalog(), frozenset({"a"}))
    guide.start_route("a", _state(0))
    activation = guide.activation_id
    with pytest.raises(ValueError, match="not approved"):
        guide.switch_route("b", _state(10))
    assert guide.activation_id == activation
    assert guide.route.route_id == "a"


@pytest.mark.parametrize("change", [
    {"route_id": "b"}, {"segment_id": "a2"}, {"step_id": 9},
    {"activation_id": "old"}, {"kind": "complete"}, {"node_id": "goal"},
])
def test_mismatched_confirmations_do_not_grant_entry(change):
    guide = ApprovedRouteSession(catalog(), frozenset({"a"}))
    state = _state(0)
    guide.start_route("a", state)
    request = guide.request_confirmation("entry", state)
    with pytest.raises(ValueError, match="mismatched"):
        guide.confirm(ProgressConfirmation(replace(request, **change), "event", "operator"), state)
    with pytest.raises(RuntimeError):
        guide.get_next_instruction()


def test_duplicate_and_previous_activation_events_rejected():
    guide = ApprovedRouteSession(catalog(), frozenset({"a", "b"}))
    state = _state(10)
    guide.start_route("a", state)
    old = guide.request_confirmation("entry", state)
    guide.confirm(ProgressConfirmation(old, "used", "operator"), state)
    completion = guide.request_confirmation("complete", state)
    with pytest.raises(ValueError):
        guide.confirm(ProgressConfirmation(completion, "used", "operator"), state)
    guide.switch_route("b", state)
    assert guide.state is state
    guide.request_confirmation("entry", state)
    with pytest.raises(ValueError):
        guide.confirm(ProgressConfirmation(old, "fresh", "operator"), state)


def test_catalog_rejects_missing_routes_discontinuity_and_unknown_nodes():
    with pytest.raises(ValueError, match="exist"):
        ApprovedRouteSession(catalog(), frozenset({"invented"}))
    route = catalog().routes[0]
    with pytest.raises(ValueError, match="contiguous"):
        replace(route, segments=(route.segments[0], replace(route.segments[1], start_node="start")))
    with pytest.raises(ValueError, match="endpoint"):
        replace(catalog(), nodes=(RouteNode("goal", "goal"),))
    with pytest.raises(ValueError, match="destination"):
        replace(route, destination="elsewhere")
    with pytest.raises(ValueError, match="unsupported"):
        replace(route.segments[0], instruction_template="{landmark.__class__}")


def test_example_catalog_and_id_plan_match():
    from pathlib import Path
    from navila_orca.routeproof import load_route_plan
    examples = Path(__file__).resolve().parents[1] / "examples"
    plan = load_route_plan(examples / "liveguide_routes.json", require_instructions=False)
    data = load_route_catalog(examples / "liveguide_catalog.json")
    guide = ApprovedRouteSession(data, frozenset(r.route_id for r in plan.routes))
    assert data.metadata["data_status"] == "synthetic_not_surveyed"
    assert [guide.resolve(r.route_id).destination for r in plan.routes] == [plan.destination] * 2
    with pytest.raises(ValueError, match="instruction"):
        load_route_plan(examples / "liveguide_routes.json")


def test_file_adapter_requires_matching_event_and_times_out_stopped(tmp_path, monkeypatch):
    guide = ApprovedRouteSession(catalog(), frozenset({"a"}))
    guide.start_route("a", _state(0))
    request = guide.request_confirmation("entry", _state(0))
    path = tmp_path / "progress.json"
    source = FlagFileProgressSource(path)
    ticks = iter([0.0, 0.0, 0.0, 0.2])
    monkeypatch.setattr("navila_orca.live_guide.progress.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("navila_orca.live_guide.progress.time.sleep", lambda _: None)
    path.write_text(json.dumps({**asdict(request), "request_id": "stale", "event_id": "old", "source": "operator"}))
    polls = []
    with pytest.raises(TimeoutError):
        source.wait(request, timeout_s=0.1, on_poll=lambda: polls.append(True))
    assert polls == [True]
    assert json.loads(source.request_path.read_text()) == asdict(request)
    ticks = iter([0.0, 0.0, 0.0])
    path.write_text(json.dumps({**asdict(request), "event_id": "new", "source": "operator"}))
    event = source.wait(request, timeout_s=0.1, on_poll=lambda: None)
    assert event.request == request
    assert event.source == "operator"
    # A valid event arriving after a slow guard poll cannot bypass the timeout.
    ticks = iter([0.0, 0.0, 1.0])
    with pytest.raises(TimeoutError):
        source.wait(request, timeout_s=0.1, on_poll=lambda: None)
