# JalanLens Live Campus Guide core

## Scope

`navila_orca.live_guide` is the product-facing domain/API for one person with a
disability who provides a current map location, destination, and hard mobility
constraints. It plans one feasible indoor route, emits ordered waypoint
instructions, and provides the safe command boundary needed for a simulated
Unitree Go2 guide.

The standalone graph-planning API intentionally does **not** provide accessibility
scores, authority views or recommendations, facilities tickets, field-survey
missions, Supabase storage, or a product-facing proof ledger. It remains
independent of RouteProof. The approved-route execution session below connects
RouteProof to NaVILA without invoking this API's shortest-path planner.

The packaged `example_campus_map.json` is synthetic fixture data, explicitly
marked `synthetic_not_surveyed`. Its distances, widths, and slopes are test
inputs, not measured accessibility claims. A deployment must replace it with
validated map data before guiding a person.

## Contracts and route selection

- `RouteMap` is a directed graph of typed `RouteNode` and `RouteEdge` values.
- `robot_passable` and `person_passable` are separate required JSON fields. A
  segment is eligible only when both are true.
- `MobilityConstraints` applies hard filtering for step-free travel, minimum
  known clear width, maximum known slope, and prohibited surfaces. Unknown
  width or slope fails the route when the corresponding constraint is set.
- `LiveGuide.start()` chooses the shortest feasible path by `distance_m`.
  Equal-distance paths are resolved deterministically by edge-ID sequence; no
  accessibility score or preference ranking is computed.
- A blocked edge is retained in `LiveGuideMission.blocked_edge_ids` and removed
  from subsequent planning. Rerouting starts at the person's supplied current
  map node, not at the original start.
- If no path remains, `AssistanceFallback` requires the robot to stay stopped
  and asks for nearby trained human assistance. It does not create a ticket or
  guess an unconfirmed route.

## Safety and integration seam

```text
current location + destination + constraints
                    |
                    v
          LiveGuide / RouteMap
                    |
          feasible waypoint instructions
                    |
                    v
        existing NaVILA/Orca navigation loop
                    |
             ProposedAction
                    v
 ActionValidator (emergency, communication, freshness,
      max steps, timeout, allowlist, motion bounds)
                    |
             ActionValidation
                    v
       to_velocity_command() -> VelocityCommand -> Go2
```

Every failed safety check produces a zero `stop` action. Emergency stop and
communication loss take precedence; stale/future observations, mission limits,
and malformed or out-of-bounds actions also fail closed. The
`to_velocity_command()` adapter is the only new seam to the existing runner
contract. Runtime/UI code is expected to construct `SafetySnapshot` from its
own monotonic clock and communication watchdog before executing each action.

## RouteProof approved-route execution

`ApprovedRouteSession` is the execution layer for a route that RouteProof has
already selected. It never calls `LiveGuide._plan()` or `block_and_reroute()`.
The existing planner and `ActionValidator` retain their standalone API and tests;
`ActionValidator` is **not** an installed runtime watchdog in this integration.
The runner's deterministic obstruction guard and strict action parser remain
authoritative over execution.

```text
RouteProof selects an allowlisted route_id
    -> ApprovedRouteSession resolves an authored segment sequence
    -> explicit operator confirmation of the route's starting node
    -> local instruction -> existing NavigationRunner -> NaVILA
    -> zero-velocity stop -> explicit operator completion confirmation
    -> RouteProof permits the next segment, approved alternate, or escalation
```

`RouteCatalog` contains named nodes and `GuidedRoute` definitions. Each route has
a destination node, ordered `RouteSegment` values and accessibility metadata.
Segments contain `segment_id`, `start_node`, `end_node`, `landmark`, `direction`
and an optional `instruction_template`. Templates allow only those four data
fields; no model generates paths or instructions. Catalog membership alone is
not approval: RouteProof's separate route plan supplies the ordered allowlist.
Unknown IDs, noncontiguous segments, destination mismatches and unknown nodes
are rejected before physics initialization.

The session exposes `start_route`, `switch_route`, `current_segment`,
`get_next_instruction`, `update_progress`, `request_confirmation`, `confirm`
and `is_complete`. `update_progress(RobotState)` records observations only.
`confirm(ProgressConfirmation, RobotState)` advances the cursor only for the
pending activation, route, segment, node, kind, step and unique request/event.
An old event cannot confirm a later segment or a new activation of the same route.

RouteProof resets physics once at mission initialization, then passes the latest
state to every `runner.run(..., resume_from_state=...)`. No backend, renderer,
camera or actor is recreated between segments/routes. The renderer's original
scene anchor and source-root reference remain intact. Zero velocity is latched
before evidence I/O, at every run exit and throughout confirmation waits.
The obstruction guard remains active during those waits even when no physics
tick has elapsed. The live monitor remains responsive through normal updates.

`--max-decisions` and `--max-control-steps` must both be positive in this mode.
They apply across the **entire mission**, including all segments and alternates.
Each approved route is attempted at most once. Every confirmation wait also has
a finite timeout. Exhaustion, missing/invalid confirmation, execution errors or
no remaining route stop execution and produce the existing local facilities
ticket. Simulator termination/truncation ends the mission because MJLab may
already have reset that world; the returned state is never used for a reroute.

For commands, manual event format and expected logs, see
[the RouteProof demo](../ROUTEPROOF.md#liveguide-demo).

## Prototype boundaries

- The example catalog is synthetic, not surveyed or calibrated to an OrcaLab
  scene. Its metadata is descriptive, not measured clearance or approval evidence.
- The operator must confirm that the robot is **already at** the first node of
  every selected route. There is no automatic node localization, mid-segment
  connector, backtracking path or teleport. If the next route cannot be entered
  from the actual stopped position, withhold confirmation and let it escalate.
- NaVILA `stop` is only a completion candidate. The operator must actually check
  arrival before confirming. No distance, elapsed time or motion count implies
  completion. A timeout does not grant approval.
- Obstruction events are manual flag-file events, or the existing always-clear
  detector when no flag is configured. RGB-only clearance perception is not added.
- Physics remains the local flat MJLab model; visual/collision/map alignment is
  unverified. Scene coordinates can differ from `RobotState`/ticket coordinates.
- The supported preservation boundary is the same live robot/render session.
  Process restart, simulator auto-reset, independent scene editing and arbitrary
  dynamic non-robot actor state are not restored by `RobotState`.
- Per-route attempts aggregate segment decision/control counts. Navigation
  metrics still describe the last executed segment's base episode; they are not
  route-quality or accessibility evidence. Confirmation provenance is logged.
- The command stop is a latched zero-velocity request, not measured mechanical
  braking. Physics does not advance during confirmation waits. Guard checks are
  at observations and wait polls; no independent real-robot emergency watchdog
  or obstacle perception is introduced.
