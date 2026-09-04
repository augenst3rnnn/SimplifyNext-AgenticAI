<p align="right"><sub><strong>English</strong> · <a href="ARCHITECTURE_zh.md">中文</a></sub></p>

# Architecture: a stable boundary between VLN and locomotion

Orca_VLN is deliberately split into a high-level navigation layer and a low-level motion layer. Teams can improve either layer without having to retrain or rewrite the other.

```text
RGB history + instruction
          │
          ▼
  High-level VLN policy
          │ textual action
          ▼
  action parser / safety gate
          │ VelocityCommand(vx, vy, wz, duration_s)
          ▼
  low-level locomotion backend
          │ RobotState + qpos
          ▼
  OrcaLab renderer and persistent ego camera
          └─────────────────────────────── feedback RGB
```

## Deployment options

The deployment choice changes where the NaVILA server runs, but it does not
change the VLM or locomotion contracts described below:

| Deployment option | Process placement | Contract impact |
| --- | --- | --- |
| **Option A (default) — single-host deployment** | OrcaLab, the navigation process, and NaVILA run on one machine | The TCP client reaches NaVILA through the local loopback endpoint |
| **Option B — remote inference** | OrcaLab and navigation run on the client; NaVILA runs on a separate GPU server | The client still uses a loopback endpoint, forwarded securely to the inference server; the `VLMClient` contract is unchanged |
| **Option C — managed remote inference (AWS SSM)** | OrcaLab and navigation run on the participant's machine; NaVILA runs on an organizer-managed AWS instance | An AWS SSM port-forward exposes the managed service through the same local loopback endpoint; the `VLMClient` contract is unchanged |

Choose one option for a deployment. For Option B installation, service
startup, tunneling, and end-to-end validation, follow the
[remote inference guide](REMOTE_INFERENCE.md). For Option C temporary SSO
credentials, AWS SSM port-forwarding, health checks, and client startup,
follow the [managed access guide](ACCESS_GUIDE.md).

## The only control contract

The boundary is [`VelocityCommand`](../src/navila_orca/contracts.py). It contains body-frame `vx`, `vy`, `wz`, and an exact simulated duration. The default parser accepts one canonical high-level action at a time:

| Text action | Motion contract |
| --- | --- |
| `move forward 25/50/75 cm` | `vx=0.5 m/s`, duration `0.5/1.0/1.5 s` |
| `turn left/right 15/30/45 degrees` | fixed signed `wz`, duration `0.5/1.0/1.5 s` |
| `stop` | zero velocity, zero duration |

The parser rejects empty, ambiguous, or unsupported model output. That failure is intentional: a competition team should see an invalid high-level response rather than silently sending an unintended motion command.

## High-level VLN layer

The high-level layer implements `VLMClient`: it receives exactly eight RGB frames and the active instruction, then returns one textual action. The provided TCP client connects to a separately run NaVILA server. It has no dependency on the locomotion network, joint order, terrain representation, or OrcaLab actor names.

What a team can change here:

- Prompting, staged waypoint logic, mission state, and stop criteria.
- NaVILA SFT or LoRA adapters.
- Hazard-detection calls, image capture rules, and reporting logic.
- A different VLN server, provided it emits the canonical action vocabulary.

## Low-level locomotion layer

The low-level layer owns balance, contact, gait generation, and joint action. The default backend is the packaged Go2 flat-ground policy running at a 50 Hz control period. It receives `VelocityCommand`; it never receives language text, RGB images, or a NaVILA prompt.

What a team can change here:

- Train a policy in [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion), IsaacLab, or another platform.
- Export a checkpoint compatible with the baseline runner, or implement an adapter for a different policy ABI.
- Add terrain features or a different robot adapter while preserving velocity-command semantics.

The replacement must preserve the `VelocityPhysicsBackend` behavior: `reset`, `set_velocity_command`, `step`, `control_dt`, and synchronized state output. See [low-level locomotion](LOW_LEVEL_LOCOMOTION.md).

## Why this matters in the competition

The baseline can be reproduced without any model training. A team building a better inspection behavior should normally work above the boundary. A team researching gait, uneven terrain, or recovery can work below it. Both approaches keep the same scene, camera, episode, and evaluation artifacts, which makes demos easier to compare.
