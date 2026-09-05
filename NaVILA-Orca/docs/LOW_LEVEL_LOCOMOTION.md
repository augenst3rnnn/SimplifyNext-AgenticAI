<p align="right"><sub><strong>English</strong> · <a href="LOW_LEVEL_LOCOMOTION_zh.md">中文</a></sub></p>

# Low-level locomotion: train anywhere, integrate deliberately

The packaged Go2 checkpoint is the runnable baseline and low-level execution is a competition metric. The model is intentionally a general flat-ground policy: it is not specialized for factory navigation, discrete NaVILA action chunks, or exact stopping near task objects. Participants can train a low-level policy in [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion) (the default reference), IsaacLab, or another simulator/training stack.

Orca_VLN uses MJLab only to run the supplied baseline and to expose a concrete alignment report for the Go2 model. It does not ask teams to retrain in MJLab.

## Environment boundary

Keep OrcaLocomotion or another training stack in a dedicated environment. The
Orca_VLN/OrcaLab runtime is pinned to the reviewed CUDA 12.8 PyTorch build; a
training repository's requirements may replace that build and make the runtime
depend on a newer host driver.

Move only the compatible actor `state_dict` or inference checkpoint across this
boundary. Do not copy the training environment. If a full training checkpoint
also contains optimizer or scheduler state, export its actor weights separately
before integration. Resume training and validate optimizer state in the original
training environment.

## Stable runtime contract

The VLN layer sends only a body-frame velocity target:

```text
VelocityCommand(vx, vy, wz, duration_s) → low-level policy → RobotState + qpos
```

Your policy can use any observation vector, reward, terrain representation, action parameterization, or network architecture. The integration must preserve the command semantics and produce synchronized robot state at a known `control_dt`.

## Two integration paths

### 1. Direct checkpoint replacement

`--checkpoint` directly loads a checkpoint only when it has the same runtime ABI as the supplied `Unitree-Go2-Flat` policy: the same runner format, actor architecture, observation ordering, action order, and Go2 joint convention. This is appropriate for a policy retrained from the compatible Go2 task.

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --checkpoint /absolute/path/to/compatible_go2_policy.pt
```

### 2. Adapter integration

For an IsaacLab, OrcaLocomotion, or other policy with a different checkpoint/runner format, do not force it through `--checkpoint`. Implement a small backend adapter that satisfies `VelocityPhysicsBackend`:

```text
reset(episode)                 -> RobotState
set_velocity_command(command)  -> store vx, vy, wz target
step()                         -> advance one control tick and return synchronized state
control_dt                     -> policy tick duration
qpos_batch                     -> current Go2 generalized position for OrcaLab rendering
```

This isolates all platform-specific loading, observation construction, action scaling, and physics stepping below the VLN boundary.

## Alignment checklist

Before connecting a custom policy to a live NaVILA run, verify:

1. **Robot asset:** Go2 link names, joint names, joint limits, and neutral pose match the rendered robot.
2. **Joint action ABI:** all 12 joints have the intended order and sign; use a low-amplitude per-joint sweep before policy rollout.
3. **Observation ABI:** proprioception, command scaling, history, terrain features, and normalization match the policy that was trained.
4. **Timing:** policy control period, action hold/decimation, and simulated duration agree with `VelocityCommand` chunks.
5. **State bridge:** root pose is `(x, y, z, w, x, y, z)` in local qpos and `(w, x, y, z)` at the public contract boundary; renderer updates remain synchronized.
6. **Motion checks:** stand, forward, turn, stop, and recovery pass before any VLM is connected.

The baseline runner writes an alignment report into `measurements.json`. Use it as a reference when comparing your robot XML, action order, qpos map, and control period.

## Suggested workflow

1. Reproduce the supplied factory baseline unchanged.
2. Create a separate environment and train and validate a policy in OrcaLocomotion, IsaacLab, or your selected platform.
3. Choose direct replacement only if the checkpoint ABI is compatible; otherwise build an adapter.
4. Run fixed velocity tests before the VLN loop.
5. Repeat the unchanged `VLN_Presentation` factory episode and compare drift, motion chunks, and final trace.

For this advanced track, submit training provenance, an alignment note, and a baseline-versus-custom run directory. This keeps low-level research comparable to high-level VLN changes.
