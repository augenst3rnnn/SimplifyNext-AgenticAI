"""Outer RouteProof loop: select, navigate, observe, reroute, and escalate."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from PIL import Image

from ..contracts import EpisodeSpec, NavigationGuardDecision, RobotState
from ..runner import NavigationRunner, RunResult
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
    ) -> None:
        if not isinstance(detector, ObstructionDetector):
            raise TypeError("detector must provide a detect method")
        self.route_plan = route_plan
        self.detector = detector
        self.ticket_store = ticket_store
        self.require_metric_success = bool(require_metric_success)
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
        return self.current_route.instruction

    def inspect(
        self,
        images: Sequence[Image.Image],
        state: RobotState,
        instruction: str,
    ) -> NavigationGuardDecision:
        """Navigation-guard hook called whenever the runner captures a frame."""

        if self.current_route is None:
            raise RuntimeError("RouteProof cannot inspect before selecting a route")
        if self._last_inspected_step_id == state.step_id:
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
            if self._current_evidence_path is None:
                self._current_evidence_path = self.ticket_store.save_evidence(
                    route_id=self.current_route.route_id,
                    image=images[-1],
                    step_id=state.step_id,
                )
        return decision

    def verify(
        self,
        runner: NavigationRunner,
        base_episode: EpisodeSpec,
    ) -> RouteProofMissionResult:
        """Try approved routes in priority order until one verifies or all fail."""

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
                resume_from_state = None
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

        if last_run is None:  # RoutePlan validation should make this unreachable.
            raise RuntimeError("RouteProof route plan contained no routes")
        ticket = self._create_no_route_ticket(last_run)
        return RouteProofMissionResult(
            status=RouteProofStatus.NO_SAFE_ROUTE,
            destination=self.route_plan.destination,
            attempts=tuple(self._attempts),
            last_run=last_run,
            ticket=ticket,
        )

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
            f"ROUTEPROOF_ROUTE_SELECTED id={route.route_id!r} "
            f"instruction={route.instruction!r}",
            flush=True,
        )

    def _create_no_route_ticket(self, last_run: RunResult) -> TicketReceipt:
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
