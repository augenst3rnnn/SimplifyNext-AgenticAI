# JalanLens Live Campus Guide core

## Scope

`navila_orca.live_guide` is the product-facing domain/API for one person with a
disability who provides a current map location, destination, and hard mobility
constraints. It plans one feasible indoor route, emits ordered waypoint
instructions, and provides the safe command boundary needed for a simulated
Unitree Go2 guide.

This core intentionally does **not** provide accessibility scores, authority
views or recommendations, facilities tickets, field-survey missions, Supabase
storage, or a product-facing proof ledger. The existing `routeproof` package is
a backward-compatible legacy surface and is not used by this API.

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
