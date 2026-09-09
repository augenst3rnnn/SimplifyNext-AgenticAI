"""Outer RouteProof loop: select, navigate, observe, reroute, and escalate."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
from typing import Any, Mapping, Sequence

from PIL import Image

from ..contracts import EpisodeSpec, NavigationGuardDecision, RobotState
from ..runner import NavigationRunner, RunResult
from ..frames import sample_history
from ..live_guide import ApprovedRouteSession, ProgressSource
from .perception import ObstructionDetector
from .routes import ApprovedRoute, RoutePlan
from .tickets import LocalFacilitiesTicketStore, TicketReceipt


class RouteProofStatus(str, Enum):
    VERIFIED = "verified"
    NO_SAFE_ROUTE = "no_safe_route"


@dataclass(frozen=True, slots=True)
class RouteAttempt:
    route_id: str
    instruction: str
    outcome: str
    termination_reason: str
    control_steps: int
    decisions: int
    evidence_path: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    segments: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "instruction": self.instruction,
            "outcome": self.outcome,
            "termination_reason": self.termination_reason,
            "control_steps": self.control_steps,
            "decisions": self.decisions,
            "evidence_path": self.evidence_path,
            "metrics": dict(self.metrics),
            "segments": [dict(segment) for segment in self.segments],
        }


@dataclass(frozen=True, slots=True)
class RouteProofMissionResult:
    status: RouteProofStatus
    destination: str
    attempts: tuple[RouteAttempt, ...]
    last_run: RunResult = field(repr=False)
    verified_route_id: str | None = None
    ticket: TicketReceipt | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "destination": self.destination,
            "verified_route_id": self.verified_route_id,
            "attempts": [attempt.as_dict() for attempt in self.attempts],
            "ticket": None if self.ticket is None else self.ticket.as_dict(),
        }


class RouteProofAgent:
    """Supervise one normal NaVILA run per approved route."""

    def __init__(
        self,
        route_plan: RoutePlan,
        detector: ObstructionDetector,
        ticket_store: LocalFacilitiesTicketStore,
        *,
        require_metric_success: bool = False,
        guide: ApprovedRouteSession | None = None,
        progress_source: ProgressSource | None = None,
        confirmation_timeout_s: float = 120.0,
    ) -> None:
        if not isinstance(detector, ObstructionDetector):
            raise TypeError("detector must provide a detect method")
        self.route_plan = route_plan
        self.detector = detector
        self.ticket_store = ticket_store
        self.require_metric_success = bool(require_metric_success)
        self.guide = guide
        self.progress_source = progress_source
        self.confirmation_timeout_s = float(confirmation_timeout_s)
        if guide is not None:
            if progress_source is None:
                raise ValueError("guided execution requires an explicit progress source")
            if not math.isfinite(self.confirmation_timeout_s) or self.confirmation_timeout_s <= 0:
                raise ValueError("confirmation timeout must be positive and finite")
            if guide.approved_route_ids != frozenset(route.route_id for route in route_plan.routes):
                raise ValueError("LiveGuide approval set must match RouteProof's route plan")
            for route in route_plan.routes:
                if guide.resolve(route.route_id).destination != route_plan.destination:
                    raise ValueError("approved route and catalog destinations disagree")
        elif any(not route.instruction for route in route_plan.routes):
            raise ValueError("legacy routes require an instruction or a LiveGuide catalog")
        self.current_route: ApprovedRoute | None = None
        self._attempts: list[RouteAttempt] = []
        self._last_inspected_step_id: int | None = None
        self._current_evidence_path: str | None = None
        self._latest_blockage: NavigationGuardDecision | None = None
        self._latest_blockage_state: RobotState | None = None

    def current_instruction(self) -> str:
        """Instruction-provider hook called by NavigationRunner."""

        if self.current_route is None:
            raise RuntimeError("RouteProof has not selected a route")
        if self.guide is not None:
            return self.guide.get_next_instruction()
        return self.current_route.instruction

    def inspect(
        self,
        images: Sequence[Image.Image],
        state: RobotState,
        instruction: str,
        *,
        force: bool = False,
    ) -> NavigationGuardDecision:
        """Navigation-guard hook called whenever the runner captures a frame."""

        if self.current_route is None:
            raise RuntimeError("RouteProof cannot inspect before selecting a route")
        if not force and self._last_inspected_step_id == state.step_id:
            return NavigationGuardDecision(
                blocked=False,
                reason="Frame already inspected",
                metadata={"route_id": self.current_route.route_id},
            )
        self._last_inspected_step_id = state.step_id
        decision = self.detector.detect(
            images,
            route_id=self.current_route.route_id,
        )
        if not isinstance(decision, NavigationGuardDecision):
            raise TypeError("detector.detect must return NavigationGuardDecision")
        if decision.blocked:
            self._latest_blockage = decision
            self._latest_blockage_state = state
        return decision

    def on_blocked(self, images: Sequence[Image.Image], state: RobotState) -> None:
        """Called after the runner latches zero velocity, before any reroute."""
        if self._current_evidence_path is None:
            self._current_evidence_path = self.ticket_store.save_evidence(
                route_id=self.current_route.route_id, image=images[-1], step_id=state.step_id,
            )

    def verify(
        self,
        runner: NavigationRunner,
        base_episode: EpisodeSpec,
    ) -> RouteProofMissionResult:
        """Try approved routes in priority order until one verifies or all fail."""

        if self.guide is not None:
            try:
                return self._verify_guided(runner, base_episode)
            finally:
                runner.stop()
        self._reset_mission()
        last_run: RunResult | None = None
        resume_from_state: RobotState | None = None
        for route in self.route_plan.routes:
            self._begin_route(route)
            episode = replace(
                base_episode,
                episode_id=f"{base_episode.episode_id}:{route.route_id}",
                instruction=route.instruction,
            )
            result = runner.run(episode, resume_from_state=resume_from_state)
            last_run = result
            metric_success = bool(result.metrics.get("success", 0.0))
            arrived = result.termination_reason == "stop" and (
                metric_success or not self.require_metric_success
            )
            if result.termination_reason == "route_blocked":
                outcome = "blocked"
                # The guard has already latched a zero-velocity command. Keep
                # the current physical state so the alternate instruction
                # starts at the obstruction instead of teleporting to spawn.
                resume_from_state = result.final_state
            elif arrived:
                outcome = "verified"
                resume_from_state = None
            else:
                outcome = "navigation_failed"
                resume_from_state = result.final_state
            self._attempts.append(
                RouteAttempt(
                    route_id=route.route_id,
                    instruction=route.instruction,
                    outcome=outcome,
                    termination_reason=result.termination_reason,
                    control_steps=result.control_steps,
                    decisions=result.decisions,
                    evidence_path=self._current_evidence_path,
                    metrics=result.metrics,
                )
            )
            if arrived:
                return RouteProofMissionResult(
                    status=RouteProofStatus.VERIFIED,
                    destination=self.route_plan.destination,
                    attempts=tuple(self._attempts),
                    last_run=result,
                    verified_route_id=route.route_id,
                )
            if result.termination_reason in ("terminated", "truncated"):
                break  # MJLab may already have auto-reset; never resume that world.

        if last_run is None:  # RoutePlan validation should make this unreachable.
            raise RuntimeError("RouteProof route plan contained no routes")
        runner.stop()
        ticket = self._create_no_route_ticket(last_run)
        return RouteProofMissionResult(
            status=RouteProofStatus.NO_SAFE_ROUTE,
            destination=self.route_plan.destination,
            attempts=tuple(self._attempts),
            last_run=last_run,
            ticket=ticket,
        )

    def _confirm_progress(self, runner: NavigationRunner, kind: str, state: RobotState) -> None:
        runner.stop()
        request = self.guide.request_confirmation(kind, state)

        def inspect_while_stopped() -> None:
            # Force inspection: manual obstruction events may arrive without a
            # new physics tick. Rendering does not advance the simulation.
            frame = runner.capture_observation(state)
            if runner.monitor is not None:
                runner.monitor.update(
                    frame, instruction=f"Confirm {kind} at {request.node_id}",
                    vlm_output="Waiting for operator confirmation", command="zero velocity",
                    status="stopped; explicit confirmation required", decision=0,
                )
            images = sample_history([frame])
            decision = self.inspect(images, state, "Awaiting explicit confirmation", force=True)
            if decision.blocked:
                runner.stop()
                self.on_blocked(images, state)
                print(f"ROUTE_BLOCKED route_id={self.current_route.route_id} reason={decision.reason!r}", flush=True)
                raise _BlockedWhileWaiting()

        confirmation = self.progress_source.wait(
            request, timeout_s=self.confirmation_timeout_s, on_poll=inspect_while_stopped,
        )
        inspect_while_stopped()  # A blockage wins over a concurrently received confirmation.
        self.guide.confirm(confirmation, state)

    def _verify_guided(self, runner: NavigationRunner, base_episode: EpisodeSpec) -> RouteProofMissionResult:
        """One persistent physics/render session, bounded across all segments."""
        if not runner._velocity_facade:
            raise ValueError("guided execution requires a backend with explicit velocity stop")
        if runner.navigation_guard is not self or runner.waypoint_instructions:
            raise ValueError("guided execution requires the RouteProof guard and no waypoint mode")
        if runner.max_decisions is None or runner.max_control_steps is None:
            raise ValueError("guided missions require finite decision and control-step limits")
        self._reset_mission()
        state = runner.initialize_episode(base_episode)
        runner.stop()
        remaining_decisions = runner.max_decisions
        remaining_steps = runner.max_control_steps
        last_run = RunResult({}, "not_started", 0, 0, (), (), state)
        previous_route_id = None
        for route in self.route_plan.routes:
            if remaining_decisions <= 0 or remaining_steps <= 0:
                break
            self._begin_route(route)
            if previous_route_id is None:
                self.guide.start_route(route.route_id, state)
            else:
                print(f"ROUTEPROOF_REROUTE from={previous_route_id} to={route.route_id}", flush=True)
                self.guide.switch_route(route.route_id, state)
            previous_route_id = route.route_id
            runs: list[RunResult] = []
            segments: list[dict[str, Any]] = []
            result = RunResult({}, "not_started", 0, 0, (), (), state)
            executing_segment = False
            try:
                self._confirm_progress(runner, "entry", state)
                while not self.guide.is_complete():
                    if remaining_decisions <= 0 or remaining_steps <= 0:
                        result = replace(result, termination_reason="mission_budget_exhausted")
                        break
                    segment = self.guide.current_segment()
                    instruction = self.current_instruction()
                    print(f"LIVEGUIDE_SEGMENT route_id={route.route_id} index={self.guide.segment_index} "
                          f"segment_id={segment.segment_id} from={segment.start_node} to={segment.end_node}", flush=True)
                    print(f"LIVEGUIDE_INSTRUCTION instruction={instruction!r}", flush=True)
                    episode = replace(base_episode, episode_id=f"{base_episode.episode_id}:{route.route_id}:{segment.segment_id}",
                                      instruction=instruction)
                    executing_segment = True
                    result = runner.run(episode, resume_from_state=state,
                                        decision_budget=remaining_decisions, control_budget=remaining_steps)
                    executing_segment = False
                    runs.append(result)
                    state = result.final_state
                    self.guide.update_progress(state)
                    remaining_decisions -= result.decisions
                    remaining_steps -= result.control_steps
                    detail = {"segment_id": segment.segment_id, "instruction": instruction,
                              "termination_reason": result.termination_reason, "confirmed": False,
                              "step_id": state.step_id}
                    segments.append(detail)
                    if result.termination_reason != "stop":
                        break
                    self._confirm_progress(runner, "complete", state)
                    detail["confirmed"] = True
            except _BlockedWhileWaiting:
                result = replace(result, termination_reason="route_blocked", guard_decision=self._latest_blockage)
            except (OSError, ValueError, RuntimeError) as exc:
                # Bad/missing confirmation or malformed actions cannot cause
                # motion, a reset, or an invented alternate route.
                if executing_segment and runner.last_result is not None:
                    result = runner.last_result
                    runs.append(result)
                    segments.append({"segment_id": segment.segment_id, "instruction": instruction,
                                     "termination_reason": result.termination_reason, "confirmed": False,
                                     "step_id": result.final_state.step_id})
                result = replace(result, termination_reason="guidance_failed")
                state = runner.current_state or state
                self.guide.update_progress(state)
                print(f"ROUTEPROOF_GUIDANCE_FAILED reason={str(exc)!r}", flush=True)
            runner.stop()
            last_run = replace(result, final_state=state,
                               control_steps=sum(r.control_steps for r in runs),
                               decisions=sum(r.decisions for r in runs),
                               vlm_outputs=tuple(text for r in runs for text in r.vlm_outputs),
                               motion_chunks=tuple(chunk for r in runs for chunk in r.motion_chunks))
            arrived = (result.termination_reason == "stop" and self.guide.is_complete()
                       and (not self.require_metric_success or bool(result.metrics.get("success", 0.0))))
            outcome = "verified" if arrived else "blocked" if result.termination_reason == "route_blocked" else "navigation_failed"
            self._attempts.append(RouteAttempt(
                route.route_id, "", outcome, result.termination_reason,
                last_run.control_steps, last_run.decisions, self._current_evidence_path,
                result.metrics, tuple(segments),
            ))
            if arrived:
                print(f"ROUTEPROOF_ROUTE_VERIFIED route_id={route.route_id}", flush=True)
                return RouteProofMissionResult(RouteProofStatus.VERIFIED, self.route_plan.destination,
                                               tuple(self._attempts), last_run, verified_route_id=route.route_id)
            if result.termination_reason in ("terminated", "truncated", "guidance_failed", "mission_budget_exhausted",
                                              "max_decisions", "max_control_steps"):
                break
        runner.stop()
        ticket = self._create_no_route_ticket(last_run)
        return RouteProofMissionResult(RouteProofStatus.NO_SAFE_ROUTE, self.route_plan.destination,
                                       tuple(self._attempts), last_run, ticket=ticket)

    def _reset_mission(self) -> None:
        self.current_route = None
        self._attempts.clear()
        self._last_inspected_step_id = None
        self._current_evidence_path = None
        self._latest_blockage = None
        self._latest_blockage_state = None

    def _begin_route(self, route: ApprovedRoute) -> None:
        self.current_route = route
        self._last_inspected_step_id = None
        self._current_evidence_path = None
        print(
            f"ROUTEPROOF_ROUTE_SELECTED route_id={route.route_id}",
            flush=True,
        )

    def _create_no_route_ticket(self, last_run: RunResult) -> TicketReceipt:
        print(f"ROUTEPROOF_NO_SAFE_ROUTE destination={self.route_plan.destination!r}", flush=True)
        latest_decision = self._latest_blockage
        latest_state = self._latest_blockage_state or last_run.final_state
        evidence_paths = [
            attempt.evidence_path
            for attempt in self._attempts
            if attempt.evidence_path is not None
        ]
        reason = "; ".join(
            f"{attempt.route_id}: {attempt.outcome}"
            for attempt in self._attempts
        )
        return self.ticket_store.create_no_safe_route_ticket(
            destination=self.route_plan.destination,
            requester=self.route_plan.requester,
            attempts=[attempt.as_dict() for attempt in self._attempts],
            position_xyz=latest_state.root_pos_world,
            obstacle_label=(
                "unknown" if latest_decision is None else latest_decision.obstacle_label
            ),
            reason=reason or "No approved route remained",
            evidence_paths=evidence_paths,
        )


class _BlockedWhileWaiting(Exception):
    """Internal control flow: the guard preempts a progress confirmation."""
