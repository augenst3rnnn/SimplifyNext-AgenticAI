"""The Go2 asset used by the bundled locomotion task.

Other Unitree robots are intentionally not imported: this developer kit exposes
only the robot that is part of the OrcaLab navigation example.
"""

from .unitree_go2.go2_constants import get_go2_robot_cfg

__all__ = ["get_go2_robot_cfg"]
