import numpy as np
import pytest

from navila_orca.cli import (
    ScriptedVLMClient,
    _build_parser,
    _make_instruction_provider,
    _procedural_rgb,
    _resolve_instruction,
    _resolve_waypoint_instructions,
    main,
)
from navila_orca.contracts import RobotState


def test_procedural_renderer_handles_negative_world_coordinates():
    state = RobotState(
        step_id=3,
        sim_time_s=0.06,
        root_pos_world=np.array([-0.2, 0.0, 0.3]),
        root_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        body_ang_vel=np.zeros(3),
        base_rpy=np.array([0.0, 0.0, -0.2]),
        joint_pos=np.zeros(12),
        joint_vel=np.zeros(12),
        last_raw_action=np.zeros(12),
    )
    image = _procedural_rgb(state, np.zeros((1, 19)))
    assert image.shape == (512, 512, 3)
    assert image.dtype == np.uint8


def test_scripted_vlm_fails_safe_to_stop_after_sequence():
    client = ScriptedVLMClient(["move forward 25 cm"])
    assert client.infer([], "x") == "move forward 25 cm"
    assert client.infer([], "x") == "stop"


def test_scene_ready_is_fail_closed_before_backend_start(capsys):
    assert main(["run", "--scene-ready"]) == 1
    assert "flat physics" in capsys.readouterr().err


def test_orcalab_run_defaults_preserve_and_align_existing_scene():
    args = _build_parser().parse_args(["run", "--render-backend", "orcalab"])
    assert args.publish_scene is False
    assert args.no_publish is False
    assert args.robot_actor_name == "auto"
    assert args.anchor_existing_scene is True
    assert args.scene_profile == "orca-train"
    assert args.strict_scene_alignment is True
    assert args.manual_xml_override is True
    assert args.randomized_play is False
    assert args.warmup_steps == 100


def test_go2_warmup_steps_can_be_overridden():
    args = _build_parser().parse_args(["run", "--warmup-steps", "0"])
    assert args.warmup_steps == 0


def test_instruction_file_overrides_dataset_prompt(tmp_path):
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("  Walk to the cabinet and stop.\n", encoding="utf-8")
    args = _build_parser().parse_args(["run", "--instruction-file", str(prompt_path)])
    assert _resolve_instruction(args, "dataset prompt") == (
        "Walk to the cabinet and stop."
    )


def test_instruction_file_provider_reloads_edits(tmp_path):
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Walk to the cabinet.\n", encoding="utf-8")
    args = _build_parser().parse_args(["run", "--instruction-file", str(prompt_path)])
    provider = _make_instruction_provider(args)

    assert provider is not None
    assert provider() == "Walk to the cabinet."
    prompt_path.write_text("Turn right at the cabinet.\n", encoding="utf-8")
    assert provider() == "Turn right at the cabinet."


def test_waypoint_file_loads_one_stage_per_nonempty_line(tmp_path):
    prompt_path = tmp_path / "waypoints.txt"
    prompt_path.write_text(
        "# staged scene\nReach orange and stop.\n\nReach blue and stop.\n",
        encoding="utf-8",
    )
    args = _build_parser().parse_args(
        ["run", "--waypoint-instruction-file", str(prompt_path)]
    )
    stages = _resolve_waypoint_instructions(args)
    assert stages == ("Reach orange and stop.", "Reach blue and stop.")
    assert _resolve_instruction(
        args, "dataset prompt", waypoint_instructions=stages
    ) == (
        "Waypoint 1: Reach orange and stop. "
        "Waypoint 2: Reach blue and stop."
    )


def test_routeproof_routes_replace_the_default_instruction_file():
    args = _build_parser().parse_args(
        [
            "run",
            "--routeproof-routes",
            "routes.json",
            "--routeproof-blockage-flag",
            "/tmp/routeproof_blocked",
        ]
    )

    assert args.routeproof_routes == "routes.json"
    assert args.instruction_file is None
    assert args.routeproof_blockage_flag == "/tmp/routeproof_blocked"


@pytest.mark.parametrize("arguments,expected", [
    (["--liveguide-catalog", "catalog.json"], "requires --routeproof-routes"),
    (["--liveguide-progress-flag", "progress.json"], "requires --liveguide-catalog"),
    (["--liveguide-catalog", "catalog.json", "--liveguide-progress-flag", "progress.json",
      "--routeproof-routes", "routes.json", "--max-decisions", "0"], "finite positive"),
    (["--liveguide-catalog", "catalog.json", "--liveguide-progress-flag", "progress.json",
      "--routeproof-routes", "routes.json", "--publish-scene"], "existing scene"),
])
def test_guidance_configuration_fails_before_backend_creation(arguments, expected, tmp_path, capsys):
    assert main(["run", "--output", str(tmp_path), *arguments]) == 1
    assert expected in capsys.readouterr().err


def test_guided_cli_wires_catalog_and_preserves_single_runtime(tmp_path, monkeypatch):
    from pathlib import Path
    from navila_orca import cli
    from navila_orca.live_guide import ProgressConfirmation
    from test_routeproof_live_guide import MovingPhysics
    from test_runner import FakeRenderer

    physics = MovingPhysics()
    physics.start = lambda: None
    physics.alignment_report = {}
    renderer = FakeRenderer()
    created = []
    def make_renderer(*args, **kwargs):
        created.append(True)
        return renderer, None
    monkeypatch.setattr(cli, "MjlabGo2Backend", lambda **kwargs: physics)
    monkeypatch.setattr(cli, "_make_renderer", make_renderer)
    def confirmation(self, request, **kwargs):
        kwargs["on_poll"]()
        assert physics.command.vx == 0
        return ProgressConfirmation(request, request.request_id, "test operator")
    monkeypatch.setattr(cli.FlagFileProgressSource, "wait", confirmation)
    examples = Path(__file__).resolve().parents[1] / "examples"
    assert main(["run", "--output", str(tmp_path),
                 "--routeproof-routes", str(examples / "liveguide_routes.json"),
                 "--liveguide-catalog", str(examples / "liveguide_catalog.json"),
                 "--liveguide-progress-flag", str(tmp_path / "progress.json"),
                 "--scripted-action", "stop"]) == 0
    payload = __import__("json").loads((tmp_path / "measurements.json").read_text())
    assert payload["routeproof"]["verified_route_id"] == "main-corridor"
    assert len(payload["routeproof"]["attempts"][0]["segments"]) == 2
    assert created == [True]
    assert physics.resets == 1
