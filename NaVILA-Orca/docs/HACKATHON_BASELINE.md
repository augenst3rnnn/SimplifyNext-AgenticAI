<p align="right"><sub><strong>English</strong> · <a href="HACKATHON_BASELINE_zh.md">中文</a></sub></p>

# Orca_VLN hackathon baseline

Orca_VLN is the organizer-provided simulation baseline. Teams begin with a working visual-language navigation loop in OrcaLab, then improve the mission behavior without rebuilding the robot stack.

## Baseline package

The organizer provides:

- the OrcaLab `VLN_Presentation` scene, obtained through asset subscription,
  and the packaged [`factory.json`](../factory.json) layout;
- a Go2, persistent ego RGB camera, and live navigation monitor;
- a NaVILA server integration point and default navigation episode;
- a packaged Go2 locomotion checkpoint and run scripts;
- result artifacts: RGB frames, action trace, trajectory state, and measurements;
- this guide plus the high-level and low-level extension tracks.

The baseline is simulation-first. A real EDU Go2 can be used for selected demonstrations, but hardware access is not required to reproduce or submit the core workflow.

The reviewed runtime target is OrcaLab 26.7.1. The kit includes `factory.json`
but not the subscribed OrcaLab assets; wait for both `VLN_Presentation` and
`unitree_robots` subscriptions to finish before loading the layout.

The supplied Go2 checkpoint is deliberately a general flat-ground policy. It is not tuned to the factory layout, the discrete NaVILA command vocabulary, or exact task stopping behavior. Teams should treat its tracking error and recovery behavior as visible baseline characteristics—not as a target to hide.

## Four checkpoints

| Checkpoint | Team outcome | Evidence |
| --- | --- | --- |
| 1. Environment | Subscribe to `VLN_Presentation` and `unitree_robots`, open the scene and layout, verify Go2 camera | screenshot of the factory map and robot view |
| 2. Autonomous loop | Run instruction → NaVILA → action → motion | `measurements.json` and terminal action trace |
| 3. Inspection logic | Add patrol, hazard, or image-capture behavior | saved images plus structured inspection record |
| 4. Integrated demo | Package one repeatable scenario | short video, source, setup notes, run directory |

## Competition tracks

### Baseline reproduction

Required for every team. Subscribe to `VLN_Presentation`, load the supplied
`factory.json` layout, and run the default red bin → right turn → blue barrel →
white robotic arm route with the Go2 checkpoint and NaVILA server. This verifies
that the simulator, camera, networking, and action interface are correct.
Choose either [Option A: single-host deployment](GETTING_STARTED.md#option-a-single-host)
or [Option B: remote inference](REMOTE_INFERENCE.md). Option B must pass the
documented end-to-end SSH tunnel and NaVILA protocol check first.

### Mission intelligence

The primary innovation track. Improve route logic, prompt design, staged waypoints, hazard detection, camera capture, inspection reports, or task-specific stop conditions. No model training is required.

### High-level VLN adaptation

Optional. Collect reviewed rollouts and apply SFT or LoRA to NaVILA without changing locomotion. See [VLN fine-tuning](VLN_FINE_TUNING.md).

### Low-level locomotion

Low-level execution is a scored dimension alongside high-level VLN. Improve command tracking, turning, stopping, stability, recovery, or terrain response while preserving the velocity-command interface. Use [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion) as the default low-level training reference, or use IsaacLab/another platform and follow the model-alignment path in [Low-level locomotion](LOW_LEVEL_LOCOMOTION.md).

## Evaluation focus

| Area | Evidence to review |
| --- | --- |
| VLN behavior | instruction adherence, visual grounding, valid actions, mission outcome |
| Low-level model | commanded versus measured motion, turn/stop precision, stability, recovery |
| End-to-end system | ego images, action trace, trajectory, measurements, reproducible run path |

## Submission checklist

1. Repository or archive with a one-command reproduction path.
2. Scene and prompt/configuration changes clearly documented.
3. A recorded run or screenshots from the robot camera.
4. `measurements.json` and relevant logs from a successful run.
5. A brief statement of which track was changed and what remained baseline.

## Support boundary

Organizers support scene setup, the baseline run path, camera visibility, and the high/low-level interface. Teams own their custom prompts, task logic, data curation, fine-tuning, and custom locomotion policies.
