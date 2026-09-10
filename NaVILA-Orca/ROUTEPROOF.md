# RouteProof prototype

This patch was prepared against the Orca_VLN `dev` branch at commit
`f0d752dd621daa7764584acdce26d5affb03eb47`.

RouteProof is the outer decision-making loop around the existing NaVILA
`NavigationRunner`.

- `CLEAR`: let the normal NaVILA loop request and execute another action.
- `BLOCKED`: issue a zero-velocity safety stop, save the latest RGB frame, and
  continue from the stopped robot state with the next pre-approved route.
- `VERIFIED`: in legacy mode, NaVILA returned `stop` for the selected route;
  LiveGuide additionally requires explicit entry and all segment completions.
- `NO_SAFE_ROUTE`: every approved route was blocked or failed, so write a local
  facilities-ticket JSON file with evidence paths and the robot position.

The active implementation is `src/navila_orca/routeproof/`. The top-level
`routeproof/` directory contains an older, inactive copy. Route switches reuse
the same backend and renderer; they do not reset to the episode start. A
simulator termination/truncation ends the mission because MJLab may already
have auto-reset. Legacy instruction-only JSON remains supported. The structured
LiveGuide mode below additionally requires explicit route entry and completion.

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

The runner stops, saves an evidence image, selects the next route, preserves the
current physical state, and sends the next route's instruction to NaVILA. Leaving the
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

In legacy instruction-only mode, a final NaVILA `stop` marks the route verified.
LiveGuide additionally requires explicit operator confirmations. This is useful while
the current OrcaLab bridge reports `scene_fidelity = False`. When goal
coordinates and collision geometry are reliable, add:

```bash
--routeproof-require-metric-success
```

That makes RouteProof require both a NaVILA stop and the existing navigation
success metric.

## LiveGuide demo

Run from `NaVILA-Orca` in the existing Linux/WSL `orcalab` environment with the
project installed (or `PYTHONPATH=src`). OrcaLab must already have the scene and
one Go2 open, with RPC endpoints on ports 50051/50151. The NaVILA TCP server or
forward must be listening on 54321. This command uses the Python CLI directly
and avoids the Windows checkout's CRLF Bash-launcher issue.

The supplied routes preserve the VM-authored human-dummy layout, but remain
**synthetic demonstration data, not surveyed or calibrated**. The plan and both
catalog routes target `human-dummy`:

- `main-corridor`: `robot-start` → `red-bucket` → `human-dummy`.
- `side-corridor`: `red-bucket` → `silver-bucket` → `fire-extinguisher` →
  `blue-bin` → `human-dummy` (no robot-start prefix).

At primary entry, the robot faces the top of the reference image. At side entry,
it must already be stopped beside the red bucket, with the bucket on its left
and the same forward heading. Nodes denote operator-checked observation
positions beside objects, not occupied object coordinates. Confirm position,
heading and a safe observation position manually; no autonomous localization,
measured clearance or human accessibility is inferred. Never acknowledge an
entry that does not match the actual scene.

```bash
PYTHONPATH=src python -m navila_orca.cli run \
  --render-backend orcalab \
  --vlm-backend tcp \
  --no-publish --robot-actor-name auto --anchor-existing-scene \
  --camera-transport grpc-png --orcalab-camera-mode mujoco-png \
  --camera-actor-name mujococamera1080 \
  --camera-asset-path prefabs/mujococamera1080 \
  --routeproof-routes examples/liveguide_routes.json \
  --liveguide-catalog examples/liveguide_catalog.json \
  --routeproof-blockage-flag /tmp/routeproof-liveguide-blocked.json \
  --liveguide-progress-flag /tmp/routeproof-liveguide-progress.json \
  --liveguide-confirmation-timeout 120 \
  --max-decisions 80 --max-control-steps 4000 \
  --live-monitor \
  --output outputs/liveguide-demo
```

At each entry or completion boundary, the robot stays stopped. The CLI prints
`LIVEGUIDE_CONFIRMATION_REQUIRED` and writes the pending request to
`/tmp/routeproof-liveguide-progress.json.request.json`. In a second terminal,
inspect it and the actual robot location:

```bash
cat /tmp/routeproof-liveguide-progress.json.request.json
```

**Only after confirming that the robot is at the requested node**, run this
command once. Do not put it in a loop: it is the explicit operator confirmation.

```bash
python - <<'PY'
import json
from pathlib import Path
from uuid import uuid4
p = Path('/tmp/routeproof-liveguide-progress.json')
request = json.loads(p.with_name(p.name + '.request.json').read_text())
request.update(event_id=uuid4().hex, source='operator')
temporary = p.with_suffix('.tmp')
temporary.write_text(json.dumps(request))
temporary.replace(p)
PY
```

The event preserves all request fields (`request_id`, `activation_id`,
`route_id`, `segment_id`, `kind`, `node_id`, `step_id`) and adds a unique
`event_id` and `source`. Stale/mismatched events are ignored; no acknowledgment
within 120 seconds stops and escalates. Old progress files cannot acknowledge
a new mission because each request/activation has a fresh ID.

A clear primary route requests three confirmations: entry at `robot-start`,
completion at `red-bucket`, then completion at `human-dummy`. Illustrative log
excerpt (not live execution evidence):

```text
ROUTEPROOF_ROUTE_SELECTED route_id=main-corridor
LIVEGUIDE_ROUTE_STARTED route_id=main-corridor activation_id=...
LIVEGUIDE_CONFIRMATION_REQUIRED {... "kind": "entry", ...}
LIVEGUIDE_PROGRESS route_id=main-corridor ... kind=entry ... source='operator'
LIVEGUIDE_SEGMENT route_id=main-corridor index=0 segment_id=main-start-to-red-bucket from=robot-start to=red-bucket
LIVEGUIDE_INSTRUCTION instruction='Continue forward until you see the red bucket ahead and slightly to your left. Keep one robot-width to its right and continue until the bucket is beside your left side. Stop there before proceeding to the next segment.'
LIVEGUIDE_CONFIRMATION_REQUIRED {... "kind": "complete", ...}
LIVEGUIDE_PROGRESS route_id=main-corridor ... kind=complete ...
LIVEGUIDE_SEGMENT route_id=main-corridor index=1 segment_id=main-red-bucket-to-human-dummy from=red-bucket to=human-dummy
LIVEGUIDE_INSTRUCTION instruction='Turn slightly right and continue diagonally through the open aisle. Keep the silver bucket far to your left and pass to the left of the single cardboard box. Approach the human dummy beside the left end of the white partition and stop one robot-length before its head.'
LIVEGUIDE_CONFIRMATION_REQUIRED {... "kind": "complete", ...}
LIVEGUIDE_PROGRESS route_id=main-corridor ... kind=complete ...
ROUTEPROOF_ROUTE_VERIFIED route_id=main-corridor
```

`SCENE_REUSE_OK` and normal `MOTION_CHUNK` diagnostics also appear. For the reroute
demo, acknowledge primary entry at `robot-start` and traverse the first segment
with no blockage flag. Wait for NaVILA to **stop beside `red-bucket`** and for
`kind=complete`, `segment_id=main-start-to-red-bucket`, `node_id=red-bucket` in
the pending request. Check the actual position/heading. **Do not acknowledge this
completion yet**: that would immediately release the next main segment. While
stopped at this boundary, report a blockage of the onward path:

```bash
printf '%s\n' '{"route_id":"main-corridor","obstacle":"delivery cart","event":1}' \
  > /tmp/routeproof-liveguide-blocked.json
```

The wait poll detects the blockage without a physics tick, keeps zero velocity,
saves evidence and selects `side-corridor`. Blockage preempts the pending main
completion (its confirmation flag remains false). LiveGuide requires a **new
explicit entry confirmation at `red-bucket`** from the same stopped state; it
does not move the robot there, reset pose or replay `robot-start`. Read the new
request before using the confirmation command. The first alternate instruction
is `side-red-bucket-to-silver-bucket`; each of its four segment completions also
requires explicit confirmation. Never infer arrival from a stop or step count.

```text
ROUTE_BLOCKED route_id=main-corridor ...
ROUTEPROOF_ROUTE_SELECTED route_id=side-corridor
ROUTEPROOF_REROUTE from=main-corridor to=side-corridor
LIVEGUIDE_ROUTE_STARTED route_id=side-corridor activation_id=...
LIVEGUIDE_ROUTE_SWITCHED route_id=side-corridor step_id=...
LIVEGUIDE_CONFIRMATION_REQUIRED {... "route_id": "side-corridor", "segment_id": "side-red-bucket-to-silver-bucket", "kind": "entry", "node_id": "red-bucket", ...}
```

To demonstrate exhaustion, report a new blockage while awaiting that entry:

```bash
printf '%s\n' '{"route_id":"side-corridor","obstacle":"locked door","event":2}' \
  > /tmp/routeproof-liveguide-blocked.json
```

Expect `ROUTE_BLOCKED`, then `ROUTEPROOF_NO_SAFE_ROUTE`. Evidence and the local
ticket appear under `outputs/liveguide-demo/routeproof/`; attempts and segment
confirmation flags are also in `outputs/liveguide-demo/measurements.json`.
To start a later clear run, explicitly clear the demo obstruction:

```bash
printf '%s\n' '{"blocked":false}' > /tmp/routeproof-liveguide-blocked.json
```

If blocked before reaching `red-bucket`, or after leaving that junction, stay
stopped and **withhold side entry**. A stale `robot-start` event cannot confirm
`red-bucket`; the wait times out and escalates. This catalog has no connector or
backtracking route from an arbitrary corridor position. Manual confirmation is
an operator assertion, not independent localization; a fabricated matching event
would not establish actual arrival. No pose-based completion, autonomous obstacle
perception or arbitrary-position safe reroute is claimed. See
[LiveGuide limitations](docs/LIVE_GUIDE.md#prototype-boundaries).

The packaged-data regression suite is explicitly **unit-test evidence**: real
`RouteProofAgent`, `NavigationRunner`, session and flag-file adapters, but fake
physics/camera, scripted VLM/operator and a fake clock for timeout. It covers the
clear primary, stopped-junction reroute and pre-junction refusal/timeout. It is
not evidence of OrcaLab movement, live NaVILA inference or physical accessibility:

```bash
PYTHONPATH=src python -m pytest tests/test_liveguide_scene_defaults.py -v
```
