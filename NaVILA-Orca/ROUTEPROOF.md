# RouteProof prototype

This patch was prepared against the Orca_VLN `dev` branch at commit
`f0d752dd621daa7764584acdce26d5affb03eb47`.

RouteProof is the outer decision-making loop around the existing NaVILA
`NavigationRunner`.

- `CLEAR`: let the normal NaVILA loop request and execute another action.
- `BLOCKED`: issue a zero-velocity safety stop, save the latest RGB frame, and
  retry from the base episode with the next pre-approved route instruction.
- `VERIFIED`: NaVILA returned `stop` for the selected route.
- `NO_SAFE_ROUTE`: every approved route was blocked or failed, so write a local
  facilities-ticket JSON file with evidence paths and the robot position.

This prototype intentionally resets to the episode start when it tries an
alternative. A later version can replace that reset with approved detour
instructions from the blockage location.

## Files

```text
src/navila_orca/routeproof/
├── agent.py       # outer route-selection and escalation loop
├── perception.py  # replaceable detector plus a deterministic demo trigger
├── routes.py      # route-plan JSON loader and validation
└── tickets.py     # evidence images and local JSON ticket receipt
```

Two existing components are extended:

- `contracts.py` defines the optional navigation-guard interface.
- `runner.py` calls the guard before VLM inference and after each recorded
  camera capture. Existing runs behave exactly as before when no guard is set.

`cli.py` connects the agent, and `run_orcalab_scene_locomotion.sh` recognises
`--routeproof-routes` as an instruction source.

## Test without OrcaLab

From `NaVILA-Orca`:

```bash
python -m pytest tests/test_routeproof.py tests/test_runner.py tests/test_cli.py
```

## Run the OrcaLab demonstration

First make sure the NaVILA port forward/server health check is successful and
the authored OrcaLab scene is already open. Then, in terminal 1:

```bash
rm -f /tmp/routeproof_blocked

./scripts/run_orcalab_scene_locomotion.sh \
  --routeproof-routes ./examples/routeproof_routes.json \
  --routeproof-blockage-flag /tmp/routeproof_blocked
```

When the robot approaches the obstacle, use terminal 2 to trigger the prototype
detector:

```bash
echo '{"obstacle":"delivery cart","confidence":1.0}' \
  > /tmp/routeproof_blocked
```

The runner stops, saves an evidence image, selects the next route, resets to the
episode start, and sends the next route's instruction to NaVILA. Leaving the
flag file unchanged will not block the alternative. To report a new blockage
on that route, modify the file again:

```bash
echo '{"obstacle":"locked door","confidence":1.0,"event":2}' \
  > /tmp/routeproof_blocked
```

After all routes are unavailable, inspect:

```text
outputs/scene_locomotion_smoke/routeproof/evidence/
outputs/scene_locomotion_smoke/routeproof/tickets/
```

## Use a real obstruction detector

`FlagFileObstructionDetector` proves the full stop/reroute/ticket wiring but is
not real perception. Replace it with a class that follows this interface:

```python
from navila_orca.contracts import NavigationGuardDecision


class CameraObstructionDetector:
    def detect(self, images, *, route_id):
        latest_rgb = images[-1]
        detection = your_model(latest_rgb)

        return NavigationGuardDecision(
            blocked=detection.blocks_accessible_corridor,
            obstacle_label=detection.label,
            confidence=detection.confidence,
            reason=detection.explanation,
            metadata={"route_id": route_id},
        )
```

Then construct `RouteProofAgent` with `CameraObstructionDetector()` instead of
`FlagFileObstructionDetector`.

For a real robot, this application-level guard must supplement rather than
replace the robot's independent collision avoidance and emergency stop.

## Verification policy

By default, a final NaVILA `stop` marks the route verified. This is useful while
the current OrcaLab bridge reports `scene_fidelity = False`. When goal
coordinates and collision geometry are reliable, add:

```bash
--routeproof-require-metric-success
```

That makes RouteProof require both a NaVILA stop and the existing navigation
success metric.
