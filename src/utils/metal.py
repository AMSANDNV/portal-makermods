"""Two MakerMods Metal followers with three cameras, and two Metal leaders.

Each arm is one `MetalSDKInterface` (github.com/makermods-robotics/metal-python-ros)
on its own CAN link. It reads and takes seven values: joint1..joint6 in radians,
then the gripper stroke in mm (0 closed, 80 open). A leader floats in gravity
compensation; a follower tracks targets. See tutorial/02-robot-loop.md.

Nothing here disables the motors: a Metal arm has no brakes and drops.
"""
from __future__ import annotations

import logging
import math
import platform
import time
from pathlib import Path

from _common import env_camera_id, env_float, env_int, env_str

logger = logging.getLogger(__name__)

SIDES = ("left", "right")
GRIPPER_MAX_MM = 80.0
# MakerMods' soft limits for joint1..joint6, degrees.
LIMITS_DEG = ((-160, 160), (-180, 0), (0, 180), (-123, 81), (-85, 85), (-145, 145))
LIMITS_RAD = [(math.radians(lo), math.radians(hi)) for lo, hi in LIMITS_DEG]


def arm_fields(side: str) -> list[str]:
    """One arm's seven `portal.yaml` fields, in SDK order."""
    return [f"{side}_joint{i}.pos" for i in range(1, 7)] + [f"{side}_gripper.pos"]


def clamp_to_limits(action: dict[str, float]) -> dict[str, float]:
    """Every joint inside its soft limit, every gripper inside its stroke."""
    out = dict(action)
    for side in SIDES:
        *joints, gripper = arm_fields(side)
        for name, (lo, hi) in zip(joints + [gripper], LIMITS_RAD + [(0.0, GRIPPER_MAX_MM)]):
            if name in out:
                out[name] = min(max(out[name], lo), hi)
    return out


class Arm:
    """One Metal arm. `read()` returns None instead of raising, so a CAN hiccup never ends the loop."""

    def __init__(self, side: str, can_id: str, leader: bool) -> None:
        self.side, self.can_id, self.leader = side, can_id, leader
        self.errors = 0
        self._sdk = None

    def connect(self) -> None:
        try:
            from metal_sdk import ControlMode, MetalSDKInterface
        except ImportError as exc:
            raise RuntimeError("metal_sdk is not installed: see tutorial/08-running-the-rig.md") from exc
        urdf = Path(env_str("METAL_URDF", "~/metal-python-ros/metal_sdk/example/urdf/metal_with_gripper.urdf"))
        # arm_end_type 3: gripper and teaching pendant, as the vendor's launches use.
        sdk = MetalSDKInterface(can_id=self.can_id, urdf_path=str(urdf.expanduser()), arm_end_type=3, enable_arm=True)
        if not sdk.Init():
            raise RuntimeError(f"{self.side} arm: Metal SDK Init() failed on {self.can_id}; is the CAN link up?")
        sdk.SetArmControlMode(ControlMode.GRAVITY_COMPENSATION if self.leader else ControlMode.RT_JOINT_POSITION)
        self._sdk = sdk

    def read(self) -> list[float] | None:
        try:
            # GetJointErrorCode: 0 disabled, 1 enabled, 2 and up a fault.
            if faults := [code for code in self._sdk.GetJointErrorCode() if code >= 2]:
                raise RuntimeError(f"motor fault codes {faults}")
            values = [float(v) for v in self._sdk.GetJointPosition()]
            if len(values) != 7 or not all(map(math.isfinite, values)):
                raise RuntimeError(f"bad reading {values}")
            return values
        except Exception as exc:
            self.errors += 1
            if self.errors == 1 or self.errors % 30 == 0:  # first failure, then about once a second
                logger.warning("%s arm: read failed (%s); %d so far", self.side, exc, self.errors)
            return None

    def command(self, values: list[float]) -> None:
        self._sdk.SetFollowerArmJointPosition(values)


class Camera:
    """A USB camera that serves its last good frame through a hiccup."""

    def __init__(self, name: str, cam) -> None:
        self.name, self._cam, self._last = name, cam, None

    def read(self):
        try:
            self._last = self._cam.read_latest(max_age_ms=500)
        except Exception as exc:
            logger.warning("%s: %s; serving the last frame", self.name, exc)
        return self._last


class StepLimiter:
    """Moves each joint target at most `max_step` rad per command, so an arm never jumps.

    It ramps the TARGET and never clamps the tracking error: under impedance
    control the error is the torque, and capping it can leave a joint stuck.
    The ramp starts from the measured pose, and again after a pause in commands.
    Grippers are not ramped.
    """

    MAX_LAG = 0.5  # rad the target may lead a joint that cannot follow
    PAUSE_S = 0.5

    def __init__(self, max_step: float) -> None:
        self.max_step = max_step
        self.present: dict[str, float] = {}
        self._target: dict[str, float] = {}
        self._last = 0.0
        self.clamped = 0

    def step(self, action: dict[str, float]) -> dict[str, float]:
        now = time.monotonic()
        if now - self._last > self.PAUSE_S:
            self._target = {}  # restart the ramp from the measured pose
        self._last = now
        if self.max_step <= 0:
            return dict(action)
        out = {}
        for name, goal in action.items():
            present = self.present.get(name)
            if present is None or name.endswith("_gripper.pos"):
                out[name] = goal
                continue
            base = self._target.get(name, present)
            target = base + min(max(goal - base, -self.max_step), self.max_step)
            target = min(max(target, present - self.MAX_LAG), present + self.MAX_LAG)
            self.clamped += target != goal
            self._target[name] = out[name] = target
        return out


def _read_pair(arms: dict[str, Arm]) -> dict[str, float]:
    """Both arms as one dict, or {} if either is unreadable: never half a row."""
    values = {}
    for side, arm in arms.items():
        if (reading := arm.read()) is None:
            return {}
        values.update(zip(arm_fields(side), reading))
    return values


class Followers:
    """Both followers and the cameras, behind `get_observation()` and `send_action()`."""

    def __init__(self, arms: dict[str, Arm], cameras: dict[str, Camera], limiter: StepLimiter) -> None:
        self.arms, self.cameras, self.limiter = arms, cameras, limiter

    def connect(self) -> None:
        for camera in self.cameras.values():
            camera._cam.connect()
        for arm in self.arms.values():
            arm.connect()

    def get_observation(self) -> tuple[dict[str, float], dict]:
        """(state, frames). State is {} while an arm is unreadable; frames keep flowing."""
        state = _read_pair(self.arms)
        self.limiter.present.update(state)
        frames = {name: frame for name, cam in self.cameras.items() if (frame := cam.read()) is not None}
        return state, frames

    def send_action(self, action: dict[str, float]) -> None:
        target = clamp_to_limits(self.limiter.step(action))
        for side, arm in self.arms.items():
            fields = arm_fields(side)
            if all(name in target and name in self.limiter.present for name in fields):  # never before a read
                arm.command([target[name] for name in fields])

    def disconnect(self) -> None:
        for camera in self.cameras.values():
            camera._cam.disconnect()


class Leaders:
    """Both leaders in gravity compensation, read as one Portal action."""

    def __init__(self, arms: dict[str, Arm]) -> None:
        self.arms = arms

    def connect(self) -> None:
        for arm in self.arms.values():
            arm.connect()

    def get_action(self) -> dict[str, float]:
        return _read_pair(self.arms)

    def disconnect(self) -> None:
        pass  # leave the SDK to stop with the process; never disable the motors


def _arms(role: str, default_can: dict[str, str]) -> dict[str, Arm]:
    return {side: Arm(side, env_str(f"METAL_{role}_{side.upper()}_CAN", default_can[side]), leader=role == "LEADER")
            for side in SIDES}


def build_followers(tracks: list[str], fps: int) -> Followers:
    """Followers on can1 (right) and can3 (left), the vendor's layout; one camera per video track.

    Track `<name>_camera` reads its device from `METAL_CAM_<NAME>`.
    """
    from lerobot.cameras import Cv2Backends
    from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig

    cameras = {}
    for i, name in enumerate(tracks):
        config = OpenCVCameraConfig(
            index_or_path=env_camera_id(f"METAL_CAM_{name.removesuffix('_camera').upper()}", f"/dev/video{2 * i}"),
            fps=fps, width=env_int("METAL_CAM_WIDTH", 640), height=env_int("METAL_CAM_HEIGHT", 480), fourcc="MJPG",
            # Other OpenCV backends can ignore the requested format and size on Linux.
            backend=Cv2Backends.V4L2 if platform.system() == "Linux" else Cv2Backends.ANY,
        )
        cameras[name] = Camera(name, OpenCVCamera(config))
    limiter = StepLimiter(env_float("METAL_MAX_STEP_RAD", 0.087))
    return Followers(_arms("FOLLOWER", {"left": "can3", "right": "can1"}), cameras, limiter)


def build_leaders() -> Leaders:
    """Leaders on can0 (right) and can2 (left), the vendor's layout."""
    return Leaders(_arms("LEADER", {"left": "can2", "right": "can0"}))
