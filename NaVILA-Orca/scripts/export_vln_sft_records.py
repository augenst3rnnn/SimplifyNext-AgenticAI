#!/usr/bin/env python3
"""Export one reviewable high-level VLN record from a saved baseline rollout.

The emitted JSONL is intentionally model-agnostic.  Review and relabel it
before adapting it to NaVILA's SFT/LoRA training format; it is not a claim that
raw baseline actions are ground truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a reviewable VLN SFT record from one Orca_VLN run."
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="directory containing measurements.json from run_orcalab_scene_locomotion.sh",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSONL file to append with one model-agnostic record",
    )
    parser.add_argument(
        "--label",
        help="human-reviewed action label; omit to mark the record unreviewed",
    )
    return parser


def _load_measurements(run_dir: Path) -> dict[str, Any]:
    path = run_dir.expanduser().resolve() / "measurements.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def export_record(
    run_dir: Path, *, label: str | None = None
) -> dict[str, Any]:
    """Create a portable high-level record from an Orca_VLN result directory."""

    payload = _load_measurements(run_dir)
    episode = payload.get("episode")
    runtime = payload.get("runtime")
    actions = payload.get("vlm_outputs")
    if not isinstance(episode, dict) or not isinstance(runtime, dict):
        raise ValueError("measurements.json is missing episode or runtime metadata")
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        raise ValueError("measurements.json is missing textual vlm_outputs")
    instruction = str(episode.get("instruction", "")).strip()
    if not instruction:
        raise ValueError("episode instruction is empty")

    frames_dir = Path(str(runtime.get("frames_directory", ""))).expanduser()
    frame_files = runtime.get("frame_files", [])
    if not isinstance(frame_files, list) or not all(
        isinstance(item, str) for item in frame_files
    ):
        raise ValueError("runtime frame_files must be a list of strings")
    image_paths = [str((frames_dir / name).resolve()) for name in frame_files]
    return {
        "record_version": 1,
        "source": "orca_vln_rollout",
        "review_status": "reviewed" if label else "unreviewed",
        "episode_id": str(episode.get("episode_id", "")),
        "scene_id": str(episode.get("scene_id", "")),
        "instruction": instruction,
        "image_paths": image_paths,
        "baseline_actions": actions,
        "target_action": label.strip() if label else None,
        "note": "Review image/action alignment before converting this record to a NaVILA SFT or LoRA dataset.",
    }


def main() -> int:
    args = _parser().parse_args()
    record = export_record(args.run_dir, label=args.label)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
