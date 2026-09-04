"""Unitree G1 velocity environment configurations."""

from navila_orca.go2_task.assets.robots import (
  G1_ACTION_SCALE,
  get_g1_robot_cfg,
)
from mjlab.envs import ManagerBasedRlEnvCfg
from mjlab.envs import mdp as envs_mdp
from mjlab.envs.mdp import dr
from mjlab.envs.mdp.actions import JointPositionActionCfg
from mjlab.managers.event_manager import EventTermCfg
from mjlab.managers.reward_manager import RewardTermCfg
from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.sensor import ContactMatch, ContactSensorCfg, RayCastSensorCfg
import navila_orca.go2_task.tasks.velocity.mdp as velocity_mdp
from navila_orca.go2_task.tasks.velocity.mdp import UniformVelocityCommandCfg
from navila_orca.go2_task.tasks.velocity.velocity_env_cfg import make_velocity_env_cfg

G1_UPPER_BODY_NAMES = (
  "torso_link",
  "left_shoulder_pitch_link",
  "left_shoulder_roll_link",
  "left_shoulder_yaw_link",
  "left_elbow_link",
  "left_wrist_roll_link",
  "left_wrist_pitch_link",
  "left_wrist_yaw_link",
  "right_shoulder_pitch_link",
  "right_shoulder_roll_link",
  "right_shoulder_yaw_link",
  "right_elbow_link",
  "right_wrist_roll_link",
  "right_wrist_pitch_link",
  "right_wrist_yaw_link",
)

G1_ARM_JOINT_KEYWORDS = ("shoulder", "elbow", "wrist")
G1_ARM_JOINT_PATTERN = r".*(shoulder|elbow|wrist).*"


def _lock_arm_actions(joint_pos_action: JointPositionActionCfg) -> None:
  """Freeze arm targets without changing the policy action dimension."""
  assert isinstance(joint_pos_action.scale, dict)
  scale = dict(joint_pos_action.scale)
  for pattern in scale:
    if any(keyword in pattern for keyword in G1_ARM_JOINT_KEYWORDS):
      scale[pattern] = 0.0
  joint_pos_action.scale = scale


def _set_arms_out_pose(cfg: ManagerBasedRlEnvCfg) -> None:
  """Set a fixed arms-out default pose for play-mode visualization."""
  robot_cfg = cfg.scene.entities["robot"]
  joint_pos = dict(robot_cfg.init_state.joint_pos or {})
  joint_pos.update(
    {
      "left_shoulder_pitch_joint": 0.0,
      "right_shoulder_pitch_joint": 0.0,
      "left_shoulder_roll_joint": 1.0,
      "right_shoulder_roll_joint": -1.0,
      "left_shoulder_yaw_joint": 0.0,
      "right_shoulder_yaw_joint": 0.0,
      "left_elbow_joint": 0.2,
      "right_elbow_joint": 0.2,
      "left_wrist_roll_joint": 0.0,
      "right_wrist_roll_joint": 0.0,
      "left_wrist_pitch_joint": 0.0,
      "right_wrist_pitch_joint": 0.0,
      "left_wrist_yaw_joint": 0.0,
      "right_wrist_yaw_joint": 0.0,
    }
  )
  robot_cfg.init_state.joint_pos = joint_pos


def _configure_upper_body_disturbance_walking(
  cfg: ManagerBasedRlEnvCfg,
  site_names: tuple[str, ...],
) -> None:
  """Configure G1 flat walking for upper-body disturbance robustness."""
  cfg.rewards["pose"].params["std_standing"] = {
    # Keep the support structure tight while allowing the upper body to move.
    r".*hip_pitch.*": 0.08,
    r".*hip_roll.*": 0.06,
    r".*hip_yaw.*": 0.06,
    r".*knee.*": 0.08,
    r".*ankle_pitch.*": 0.06,
    r".*ankle_roll.*": 0.05,
    r".*waist_yaw.*": 0.2,
    r".*waist_roll.*": 0.15,
    r".*waist_pitch.*": 0.15,
    r".*shoulder_pitch.*": 0.8,
    r".*shoulder_roll.*": 0.8,
    r".*shoulder_yaw.*": 0.8,
    r".*elbow.*": 0.8,
    r".*wrist.*": 1.0,
  }
  cfg.rewards["pose"].params["std_walking"] = {
    # Lower body.
    r".*hip_pitch.*": 0.5,
    r".*hip_roll.*": 0.15,
    r".*hip_yaw.*": 0.15,
    r".*knee.*": 0.5,
    r".*ankle_pitch.*": 0.15,
    r".*ankle_roll.*": 0.1,
    # Waist yields more than the legs, acting like an impedance buffer.
    r".*waist_yaw.*": 0.35,
    r".*waist_roll.*": 0.25,
    r".*waist_pitch.*": 0.25,
    # Upper body is loose because no manipulation dataset is available.
    r".*shoulder_pitch.*": 0.75,
    r".*shoulder_roll.*": 0.75,
    r".*shoulder_yaw.*": 0.75,
    r".*elbow.*": 0.75,
    r".*wrist.*": 1.0,
  }
  cfg.rewards["pose"].params["std_running"] = {
    # Lower body.
    r".*hip_pitch.*": 0.5,
    r".*hip_roll.*": 0.25,
    r".*hip_yaw.*": 0.25,
    r".*knee.*": 0.5,
    r".*ankle_pitch.*": 0.25,
    r".*ankle_roll.*": 0.1,
    # Waist.
    r".*waist_yaw.*": 0.45,
    r".*waist_roll.*": 0.35,
    r".*waist_pitch.*": 0.35,
    # Upper body.
    r".*shoulder_pitch.*": 0.9,
    r".*shoulder_roll.*": 0.9,
    r".*shoulder_yaw.*": 0.9,
    r".*elbow.*": 0.9,
    r".*wrist.*": 1.2,
  }

  cfg.rewards["body_orientation_l2"].params["asset_cfg"].body_names = ("pelvis",)
  cfg.rewards["body_ang_vel"].weight = -0.1
  cfg.rewards["angular_momentum"].weight = -0.05
  cfg.rewards["foot_gait"].weight = 0.8
  cfg.rewards["foot_slip"].weight = -0.5
  cfg.rewards["track_linear_velocity"].params["std"] = 0.4
  cfg.rewards["track_angular_velocity"].params["std"] = 0.6
  cfg.rewards["stand_still"].params["asset_cfg"] = SceneEntityCfg(
    "robot", joint_names=r".*(hip|knee|ankle|waist).*"
  )
  cfg.rewards["foot_slip"].params["command_threshold"] = -1.0
  cfg.rewards["foot_slip"].params["asset_cfg"].site_names = site_names
  cfg.rewards["standing_root_velocity_l2"] = RewardTermCfg(
    func=velocity_mdp.standing_root_velocity_l2,
    weight=-2.0,
    params={
      "command_name": "twist",
      "command_threshold": 0.1,
      "lin_weight": 1.0,
      "ang_weight": 0.25,
    },
  )
  cfg.rewards["standing_pelvis_height_l2"] = RewardTermCfg(
    func=velocity_mdp.standing_body_height_l2,
    weight=-8.0,
    params={
      "target_height": 0.8,
      "command_name": "twist",
      "command_threshold": 0.1,
      "asset_cfg": SceneEntityCfg("robot", body_names=("pelvis",)),
    },
  )
  cfg.rewards["standing_feet_contact"] = RewardTermCfg(
    func=velocity_mdp.standing_feet_contact,
    weight=1.0,
    params={
      "sensor_name": "feet_ground_contact",
      "command_name": "twist",
      "command_threshold": 0.1,
    },
  )
  cfg.rewards["pelvis_orientation_l2"] = RewardTermCfg(
    func=velocity_mdp.body_orientation_l2,
    weight=-1.5,
    params={"asset_cfg": SceneEntityCfg("robot", body_names=("pelvis",))},
  )


def unitree_g1_rough_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg:
  """Create Unitree G1 rough terrain velocity configuration."""
  cfg = make_velocity_env_cfg()

  cfg.sim.mujoco.ccd_iterations = 500
  cfg.sim.contact_sensor_maxmatch = 500
  cfg.sim.nconmax = 48

  cfg.scene.entities = {"robot": get_g1_robot_cfg()}

  # Set raycast sensor frame to G1 pelvis.
  for sensor in cfg.scene.sensors or ():
    if sensor.name == "terrain_scan":
      assert isinstance(sensor, RayCastSensorCfg)
      sensor.frame.name = "pelvis"

  site_names = ("left_foot", "right_foot")
  geom_names = tuple(
    f"{side}_foot{i}_collision" for side in ("left", "right") for i in range(1, 8)
  )

  feet_ground_cfg = ContactSensorCfg(
    name="feet_ground_contact",
    primary=ContactMatch(
      mode="subtree",
      pattern=r"^(left_ankle_roll_link|right_ankle_roll_link)$",
      entity="robot",
    ),
    secondary=ContactMatch(mode="body", pattern="terrain"),
    fields=("found", "force"),
    reduce="netforce",
    num_slots=1,
    track_air_time=True,
  )
  self_collision_cfg = ContactSensorCfg(
    name="self_collision",
    primary=ContactMatch(mode="subtree", pattern="pelvis", entity="robot"),
    secondary=ContactMatch(mode="subtree", pattern="pelvis", entity="robot"),
    fields=("found", "force"),
    reduce="none",
    num_slots=1,
    history_length=4,
  )
  cfg.scene.sensors = (cfg.scene.sensors or ()) + (
    feet_ground_cfg,
    self_collision_cfg,
  )

  if cfg.scene.terrain is not None and cfg.scene.terrain.terrain_generator is not None:
    cfg.scene.terrain.terrain_generator.curriculum = True

  joint_pos_action = cfg.actions["joint_pos"]
  assert isinstance(joint_pos_action, JointPositionActionCfg)
  joint_pos_action.scale = G1_ACTION_SCALE

  cfg.viewer.body_name = "torso_link"

  twist_cmd = cfg.commands["twist"]
  assert isinstance(twist_cmd, UniformVelocityCommandCfg)
  twist_cmd.viz.z_offset = 1.15

  cfg.observations["critic"].terms["foot_height"].params[
    "asset_cfg"
  ].site_names = site_names

  cfg.events["foot_friction"].params["asset_cfg"].geom_names = geom_names
  cfg.events["base_com"].params["asset_cfg"].body_names = ("torso_link",)

  # Rationale for std values:
  # - Knees/hip_pitch get the loosest std to allow natural leg bending during stride.
  # - Hip roll/yaw stay tighter to prevent excessive lateral sway and keep gait stable.
  # - Ankle roll is very tight for balance; ankle pitch looser for foot clearance.
  # - Waist roll/pitch stay tight to keep the torso upright and stable.
  # - Shoulders/elbows get moderate freedom for natural arm swing during walking.
  # - Wrists are loose (0.3) since they don't affect balance much.
  # Running values are ~1.5-2x walking values to accommodate larger motion range.
  cfg.rewards["pose"].params["std_standing"] = {".*": 0.05}
  cfg.rewards["pose"].params["std_walking"] = {
    # Lower body.
    r".*hip_pitch.*": 0.5,
    r".*hip_roll.*": 0.15,
    r".*hip_yaw.*": 0.15,
    r".*knee.*": 0.5,
    r".*ankle_pitch.*": 0.15,
    r".*ankle_roll.*": 0.1,
    # Waist.
    r".*waist_yaw.*": 0.15,
    r".*waist_roll.*": 0.1,
    r".*waist_pitch.*": 0.1,
    # Arms.
    r".*shoulder_pitch.*": 0.15,
    r".*shoulder_roll.*": 0.1,
    r".*shoulder_yaw.*": 0.1,
    r".*elbow.*": 0.1,
    r".*wrist.*": 0.1,
  }
  cfg.rewards["pose"].params["std_running"] = {
    # Lower body.
    r".*hip_pitch.*": 0.5,
    r".*hip_roll.*": 0.25,
    r".*hip_yaw.*": 0.25,
    r".*knee.*": 0.5,
    r".*ankle_pitch.*": 0.25,
    r".*ankle_roll.*": 0.1,
    # Waist.
    r".*waist_yaw.*": 0.25,
    r".*waist_roll.*": 0.1,
    r".*waist_pitch.*": 0.1,
    # Arms.
    r".*shoulder_pitch.*": 0.25,
    r".*shoulder_roll.*": 0.1,
    r".*shoulder_yaw.*": 0.1,
    r".*elbow.*": 0.1,
    r".*wrist.*": 0.1,
  }

  cfg.rewards["body_orientation_l2"].params["asset_cfg"].body_names = ("torso_link",)
  cfg.rewards["body_ang_vel"].params["asset_cfg"].body_names = ("torso_link",)
  cfg.rewards["foot_clearance"].params["asset_cfg"].site_names = site_names
  cfg.rewards["foot_slip"].params["asset_cfg"].site_names = site_names
  cfg.rewards["self_collisions"] = RewardTermCfg(
    func=velocity_mdp.self_collision_cost,
    weight=-1.0,
    params={"sensor_name": self_collision_cfg.name, "force_threshold": 10.0},
  )

  # Apply play mode overrides.
  if play:
    # Effectively infinite episode length.
    cfg.episode_length_s = int(1e9)

    cfg.observations["actor"].enable_corruption = False
    cfg.events.pop("push_robot", None)
    cfg.curriculum = {}
    cfg.events["randomize_terrain"] = EventTermCfg(
      func=envs_mdp.randomize_terrain,
      mode="reset",
      params={},
    )

    if cfg.scene.terrain is not None:
      if cfg.scene.terrain.terrain_generator is not None:
        cfg.scene.terrain.terrain_generator.curriculum = False
        cfg.scene.terrain.terrain_generator.num_cols = 5
        cfg.scene.terrain.terrain_generator.num_rows = 5
        cfg.scene.terrain.terrain_generator.border_width = 10.0

  return cfg


def unitree_g1_flat_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg:
  """Create Unitree G1 flat terrain velocity configuration."""
  cfg = unitree_g1_rough_env_cfg(play=play)

  cfg.sim.njmax = 300
  cfg.sim.mujoco.ccd_iterations = 50
  cfg.sim.contact_sensor_maxmatch = 64
  cfg.sim.nconmax = None

  # Switch to flat terrain.
  assert cfg.scene.terrain is not None
  cfg.scene.terrain.terrain_type = "plane"
  cfg.scene.terrain.terrain_generator = None

  # Remove raycast sensor and height scan (no terrain to scan).
  cfg.scene.sensors = tuple(
    s for s in (cfg.scene.sensors or ()) if s.name != "terrain_scan"
  )
  del cfg.observations["actor"].terms["height_scan"]
  del cfg.observations["critic"].terms["height_scan"]

  # Disable terrain curriculum (not present in play mode since rough clears all).
  cfg.curriculum.pop("terrain_levels", None)

  _configure_upper_body_disturbance_walking(cfg, ("left_foot", "right_foot"))

  twist_cmd = cfg.commands["twist"]
  assert isinstance(twist_cmd, UniformVelocityCommandCfg)
  twist_cmd.resampling_time_range = (4.0, 7.0)
  twist_cmd.rel_standing_envs = 0.5
  twist_cmd.rel_heading_envs = 0.6
  twist_cmd.ranges.lin_vel_x = (0.15, 1.0)
  twist_cmd.ranges.lin_vel_y = (-0.25, 0.25)
  twist_cmd.ranges.ang_vel_z = (-0.5, 0.5)

  if not play:
    # Use root velocity kicks as an equivalent upper-body interaction signal. In
    # MuJoCo this is less exact than a hand/torso wrench, but it trains the same
    # recovery reflexes: lateral capture steps, pelvis stabilization, and yaw/roll
    # damping while velocity commands remain active.
    cfg.events["push_robot"].interval_range_s = (0.7, 1.4)
    cfg.events["push_robot"].params["velocity_range"] = {
      "x": (-0.35, 0.35),
      "y": (-0.8, 0.8),
      "z": (-0.2, 0.2),
      "roll": (-0.9, 0.9),
      "pitch": (-0.75, 0.75),
      "yaw": (-0.9, 0.9),
    }
    cfg.events["upper_body_com"] = EventTermCfg(
      mode="startup",
      func=dr.body_com_offset,
      params={
        "asset_cfg": SceneEntityCfg("robot", body_names=G1_UPPER_BODY_NAMES),
        "operation": "add",
        "ranges": {
          0: (-0.08, 0.08),
          1: (-0.08, 0.08),
          2: (-0.03, 0.06),
        },
      },
    )
    cfg.events["encoder_bias"].params["bias_range"] = (-0.02, 0.02)
    cfg.events["foot_friction"].params["ranges"] = (0.45, 1.4)
    cfg.events["reset_arm_joints"] = EventTermCfg(
      func=velocity_mdp.reset_joints_by_offset,
      mode="reset",
      params={
        "position_range": (-1.2, 0.8),
        "velocity_range": (-0.2, 0.2),
        "asset_cfg": SceneEntityCfg("robot", joint_names=G1_ARM_JOINT_PATTERN),
      },
    )

    cfg.curriculum["command_vel"].params["velocity_stages"] = [
      {
        "step": 0,
        "lin_vel_x": (0.15, 0.5),
        "lin_vel_y": (-0.15, 0.15),
        "ang_vel_z": (-0.25, 0.25),
      },
      {
        "step": 4000 * 24,
        "lin_vel_x": (0.15, 0.8),
        "lin_vel_y": (-0.2, 0.2),
        "ang_vel_z": (-0.4, 0.4),
      },
      {
        "step": 9000 * 24,
        "lin_vel_x": (0.15, 1.0),
        "lin_vel_y": (-0.25, 0.25),
        "ang_vel_z": (-0.5, 0.5),
      },
    ]

  if play:
    joint_pos_action = cfg.actions["joint_pos"]
    assert isinstance(joint_pos_action, JointPositionActionCfg)
    twist_cmd.debug_vis = True
    twist_cmd.heading_command = False
    twist_cmd.rel_heading_envs = 0.0
    twist_cmd.rel_standing_envs = 1.0
    twist_cmd.ranges.lin_vel_x = (0.0, 0.0)
    twist_cmd.ranges.lin_vel_y = (0.0, 0.0)
    twist_cmd.ranges.ang_vel_z = (0.0, 0.0)
    twist_cmd.ranges.heading = None

  return cfg
