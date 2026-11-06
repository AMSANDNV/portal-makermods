"""Two Metal followers with three cameras and two Metal leaders, over Dream's `robots` driver.

Each arm is one follower on its own CAN link: seven values, joint1..joint6 in radians then the
gripper (0 closed, GRIPPER_MAX_MM open). Followers are energized (position control with gravity
feed-forward); leaders open read-only, moved by hand. The arms are brakeless and drop when
unpowered: only `Followers.park()`, asked for explicitly, switches them off, and only after walking
them back to the pose each was opened in; `unpark()` powers them up again. See tutorial/05-robot-loop.md.
"""
from __future__ import annotations

import logging
import math
import platform
import time

import numpy as np

from utils.common import env_camera_id, env_float, env_int, env_str

logger = logging.getLogger(__name__)

SIDES = ("left", "right")
GRIPPER_MAX_MM = 100.0  # Dream's grip scale: the driver's gripper 0..1 maps 1.0 to this many mm
# Soft limits for joint1..joint6, degrees.
LIMITS_DEG = ((-160, 160), (-180, 0), (0, 180), (-123, 81), (-85, 85), (-145, 145))
LIMITS_RAD = [(math.radians(lo), math.radians(hi)) for lo, hi in LIMITS_DEG]
PARK_LAG_RAD = math.radians(10)  # a joint this far behind its park target is blocked: stop and hold
PARK_TOL_RAD = math.radians(3)  # within this of rest counts as parked


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


def _gripper_deg(side: str, which: str) -> float:
    """The gripper motor's angle at the jaws' OPEN or CLOSED end, degrees, measured per arm, from the env."""
    raw = env_str(f"METAL_{side.upper()}_GRIPPER_{which}_DEG", env_str(f"METAL_GRIPPER_{which}_DEG", ""))
    if not raw:
        raise RuntimeError(f"{side} arm: set METAL_GRIPPER_{which}_DEG (see tutorial/01-first-recording.md)")
    return float(raw)


def _arm_command(positions_rad: np.ndarray, gripper: float):
    from robots import types  # lazy: keep `import utils.metal` free of the hardware stack

    return types.ArmCommand(positions_rad=positions_rad, gripper=gripper)


class Arm:
    """One Metal arm over Dream's `robots` follower; `read()` returns None on a hiccup, never raising.

    A follower is energized; a leader opens read-only, moved by hand. The gripper crosses as 0..1, carried as mm.
    """

    def __init__(self, side: str, can_channel: str, leader: bool) -> None:
        self.side, self.can_channel, self.leader = side, can_channel, leader
        self.errors = 0
        self.rest: list[float] | None = None  # the joints as opened: where park() returns the arm
        self._arm = None  # robots.followers.FollowerArm

    def connect(self, energize: bool | None = None) -> None:
        """Open the arm: a follower energized, a leader read-only, unless `energize` says otherwise."""
        try:
            from robots import config, metal, motor_arm, robstride
        except ImportError as exc:
            raise RuntimeError("dreamlabs is not installed: run `uv sync` (see tutorial/01-first-recording.md)") from exc
        cfg = config.ArmConfig(
            can_channel=self.can_channel,
            home=(0.0,) * 6,  # the loop reads the real pose; never homes
            gripper_open_deg=_gripper_deg(self.side, "OPEN"),
            gripper_closed_deg=_gripper_deg(self.side, "CLOSED"),
            gripper_hold_torque_nm=env_float("METAL_GRIPPER_HOLD_NM", 0.5),
        )
        self._arm = motor_arm.open_arm(
            metal.ARM, cfg, self.side, opener=robstride.open_can_bus, energize=not self.leader if energize is None else energize
        )
        if (reading := self.read()) is None:
            raise RuntimeError(f"{self.side} arm: no pose after opening on {self.can_channel}")
        self.rest = reading[:6]  # start the robot with the arms resting: this is where they park

    def read(self) -> list[float] | None:
        try:
            state = self._arm.read()
            values = [float(x) for x in state.positions_rad] + [float(state.gripper) * GRIPPER_MAX_MM]
            if len(values) != 7 or not all(map(math.isfinite, values)):
                raise RuntimeError(f"bad reading {values}")
            return values
        except Exception as exc:
            self.errors += 1
            if self.errors == 1 or self.errors % 30 == 0:  # first failure, then about once a second
                logger.warning("%s arm: read failed (%s); %d so far", self.side, exc, self.errors)
            return None

    def command(self, values: list[float]) -> None:
        gripper = min(max(values[6] / GRIPPER_MAX_MM, 0.0), 1.0)
        self._arm.write(_arm_command(np.asarray(values[:6], dtype=np.float64), gripper))

    def switch_off(self) -> None:
        """The driver's torque off: parks where the arm was opened, then disables. Call only once it is there."""
        self._arm.torque_off()

    def close(self) -> None:
        """Release the driver without moving the arm (a follower keeps holding its last target)."""
        if self._arm is not None:
            self._arm.close()


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
        self.parked = False  # switched off at rest, open read-only: state flows, actions are ignored

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
        if self.parked:
            return
        target = clamp_to_limits(self.limiter.step(action))
        for side, arm in self.arms.items():
            fields = arm_fields(side)
            if all(name in target and name in self.limiter.present for name in fields):  # never before a read
                arm.command([target[name] for name in fields])

    def park(self, fps: int) -> None:
        """Walk both arms back to the pose they were opened in, slowly, switch them off, and reopen them read-only.

        Blocking; run it on the hardware thread with no actions applied. Raises with the arms holding
        where they are if an arm is unreadable, falls behind its target (blocked), or does not arrive.
        """
        step = math.radians(env_float("METAL_PARK_DEG_S", 10.0)) / fps
        present = {side: arm.read() for side, arm in self.arms.items()}
        if any(value is None for value in present.values()):
            raise RuntimeError("an arm is unreadable; not parking")
        target = {side: value[:6] for side, value in present.items()}
        grip = {side: value[6] for side, value in present.items()}
        settled = 0
        while settled < fps:  # one second at rest after the targets arrive
            started = time.monotonic()
            arrived = True
            for side, arm in self.arms.items():
                target[side] = [t + min(max(r - t, -step), step) for t, r in zip(target[side], arm.rest)]
                arrived &= all(abs(r - t) < 1e-9 for t, r in zip(target[side], arm.rest))
                arm.command(target[side] + [grip[side]])
                if (reading := arm.read()) is None:
                    continue
                lag = max(abs(t - q) for t, q in zip(target[side], reading[:6]))
                if lag > PARK_LAG_RAD:
                    arm.command(reading)
                    raise RuntimeError(f"{side} arm is {math.degrees(lag):.0f} deg behind its park target "
                                       "(blocked?); holding where it is")
            settled = settled + 1 if arrived else 0
            time.sleep(max(0.0, 1 / fps - (time.monotonic() - started)))
        for side, arm in self.arms.items():
            reading = arm.read()
            if reading is None or max(abs(r - q) for r, q in zip(arm.rest, reading[:6])) > PARK_TOL_RAD:
                raise RuntimeError(f"{side} arm did not reach its rest pose; holding where it is")
        for arm in self.arms.values():
            arm.switch_off()
        self.parked = True
        for arm in self.arms.values():
            arm.connect(energize=False)  # read-only, motors stay off: state keeps flowing

    def unpark(self) -> None:
        """Power the parked arms up again, holding where they rest (which becomes the new park pose)."""
        for arm in self.arms.values():
            arm.close()
        for arm in self.arms.values():
            arm.connect(energize=True)
        self.limiter.present.clear()  # never command before a fresh read
        self.parked = False

    def disconnect(self) -> None:
        for camera in self.cameras.values():
            camera._cam.disconnect()
        for arm in self.arms.values():
            arm.close()  # never disables the motors; park() is the one way to switch them off


class Leaders:
    """Both leaders read-only, moved by hand, read as one Portal action."""

    def __init__(self, arms: dict[str, Arm]) -> None:
        self.arms = arms

    def connect(self) -> None:
        for arm in self.arms.values():
            arm.connect()

    def get_action(self) -> dict[str, float]:
        return _read_pair(self.arms)

    def disconnect(self) -> None:
        for arm in self.arms.values():
            arm.close()


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


def check_arms(role: str = "FOLLOWER") -> None:
    """Open each configured arm read-only (motors off) and print its joints until ctrl-c: move one to see which."""
    arms = _arms(role, {"left": "can3", "right": "can1"} if role == "FOLLOWER" else {"left": "can2", "right": "can0"})
    for arm in arms.values():
        arm.leader = True  # read-only: never energized
        arm.connect()
    try:
        while True:
            for side, arm in arms.items():
                if (values := arm.read()) is not None:
                    joints = " ".join(f"{math.degrees(v):+6.0f}" for v in values[:6])
                    print(f"{side:5s} {arm.can_channel}: joints {joints} deg, gripper {values[6]:5.1f}")
            print()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for arm in arms.values():
            arm.close()


def build_leaders() -> Leaders:
    """Leaders on can0 (right) and can2 (left), the vendor's layout."""
    return Leaders(_arms("LEADER", {"left": "can2", "right": "can0"}))
