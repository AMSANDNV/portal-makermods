"""A pair of MakerMods Metal arms on the vendor's SDK, three USB cameras, and a pair of Metal leaders.

The SDK is `metal_sdk` from github.com/makermods-robotics/metal-python-ros (built
from source on the robot and operator hosts; see tutorial/07-running-the-rig.md).
Each arm is one `MetalSDKInterface` on its own CAN link. Its joints are
radians in the URDF's frame and its gripper is the stroke in mm, 0 closed to 80
open: `GetJointPosition()` returns those seven values and, in follower mode,
`SetFollowerArmJointPosition()` takes them. A leader in gravity compensation
reports the same seven, which is how the vendor's own leader/follower launch
wires one to the other. See tutorial/02-robot-loop.md.

The SDK is imported lazily, so hosts that never touch an arm (the recorder, the
policies, the tests) do not need it.
"""
from __future__ import annotations

import logging
import math
import platform
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from _common import env_camera_id, env_float, env_int, env_str

logger = logging.getLogger(__name__)

SIDES = ("left", "right")
JOINTS = tuple(f"joint{i}" for i in range(1, 7))
GRIPPER = "gripper"
GRIPPER_MAX_MM = 80.0
# Soft limits per joint, degrees: MakerMods' motor configuration (also Dream's
# `robots.metal` and the vendor's Star leader config).
JOINT_LIMITS_DEG = ((-160.0, 160.0), (-180.0, 0.0), (0.0, 180.0), (-123.0, 81.0), (-85.0, 85.0), (-145.0, 145.0))
JOINT_LIMITS_RAD = tuple((math.radians(lo), math.radians(hi)) for lo, hi in JOINT_LIMITS_DEG)

# GetJointErrorCode(): 0 disabled, 1 enabled, then faults.
_FAULTS = {2: "motor disconnected", 3: "over-voltage", 4: "under-voltage", 5: "over-current",
           6: "MOS over-temperature", 7: "rotor over-temperature", 8: "overload"}

_STALE_CEILING_MS = 60_000


def arm_fields(side: str) -> list[str]:
    """The seven `portal.yaml` fields of one arm, in SDK order: joint1..joint6, then the gripper."""
    return [f"{side}_{name}.pos" for name in (*JOINTS, GRIPPER)]


def clamp_to_limits(action: dict[str, float]) -> dict[str, float]:
    """Keep every joint inside its soft limit and the gripper inside its stroke."""
    clamped = dict(action)
    for side in SIDES:
        fields = arm_fields(side)
        for name, (lo, hi) in zip(fields, JOINT_LIMITS_RAD):
            if name in clamped:
                clamped[name] = min(max(clamped[name], lo), hi)
        if fields[-1] in clamped:
            clamped[fields[-1]] = min(max(clamped[fields[-1]], 0.0), GRIPPER_MAX_MM)
    return clamped


def _sdk():
    try:
        import metal_sdk
    except ImportError as exc:
        raise RuntimeError(
            "metal_sdk is not installed in this environment. Build it from "
            "github.com/makermods-robotics/metal-python-ros, then "
            "`uv pip install <repo>/metal_sdk/binding` "
            "(tutorial/07-running-the-rig.md)") from exc
    return metal_sdk


def _urdf_path() -> str:
    path = Path(env_str("METAL_URDF", "~/metal-python-ros/metal_sdk/example/urdf/metal_with_gripper.urdf"))
    path = path.expanduser()
    if not path.is_file():
        raise RuntimeError(f"METAL_URDF: {path} does not exist; point it at the SDK's metal_with_gripper.urdf")
    return str(path)


class MetalArm:
    """One Metal arm: `follower` tracks seven-value targets, `leader` floats in gravity compensation.

    Nothing here ever disables the motors. A Metal arm has no brakes: disabled,
    it drops. Support or rest the arms before stopping a process.
    """

    def __init__(self, side: str, can_id: str, role: str, *, urdf: str, end_type: int) -> None:
        if role not in ("follower", "leader"):
            raise ValueError(f"role must be follower or leader, not {role!r}")
        self.side, self.can_id, self.role = side, can_id, role
        self._urdf, self._end_type = urdf, end_type
        self._iface: Any = None

    def connect(self) -> None:
        sdk = _sdk()
        iface = sdk.MetalSDKInterface(can_id=self.can_id, urdf_path=self._urdf,
                                      arm_end_type=self._end_type, enable_arm=True)
        if not iface.Init():
            raise RuntimeError(f"{self.side} {self.role}: Metal SDK Init() failed on {self.can_id}; "
                               "is the CAN link up (metal_ros2/can_scripts/start_canN.sh)?")
        mode = (sdk.ControlMode.RT_JOINT_POSITION if self.role == "follower"
                else sdk.ControlMode.GRAVITY_COMPENSATION)
        iface.SetArmControlMode(mode)
        self._iface = iface
        logger.info("%s %s connected on %s", self.side, self.role, self.can_id)

    def read(self) -> list[float]:
        """joint1..joint6 in radians, then the gripper stroke in mm. Raises on a fault."""
        if self._iface is None:
            raise RuntimeError(f"{self.side} {self.role} is not connected")
        if faults := self.faults():
            raise RuntimeError(f"{self.side} {self.role}: {', '.join(faults)}")
        values = [float(v) for v in self._iface.GetJointPosition()]
        if len(values) != len(JOINTS) + 1 or not all(math.isfinite(v) for v in values):
            raise RuntimeError(f"{self.side} {self.role}: GetJointPosition returned {values}; "
                               "is METAL_END_TYPE set for an arm with a gripper?")
        return values

    def command(self, values: Sequence[float]) -> None:
        if self.role != "follower":
            raise RuntimeError("only a follower takes commands")
        if self._iface is None:
            raise RuntimeError(f"{self.side} follower is not connected")
        self._iface.SetFollowerArmJointPosition([float(v) for v in values])

    def faults(self) -> list[str]:
        codes = self._iface.GetJointErrorCode() if self._iface is not None else []
        return [f"{name} {_FAULTS[code]}" for name, code in zip((*JOINTS, GRIPPER), codes) if code in _FAULTS]

    def reconnect(self) -> None:
        self._iface = None  # drop the old interface (and its CAN thread) before opening a new one
        self.connect()

    def disconnect(self) -> None:
        self._iface = None


class ResilientArm:
    """Keeps a CAN hiccup from ending the control loop.

    Failures are counted and reported, and the arm is reopened after
    `errors_before_reconnect` in a row. Reopening runs inline on the hardware
    thread, so it never overlaps a read or a command.
    """

    def __init__(self, arm: MetalArm, *, errors_before_reconnect: int, reconnect_interval_s: float) -> None:
        self.arm = arm
        self._errors_before_reconnect = errors_before_reconnect
        self._reconnect_interval_s = reconnect_interval_s
        self._consecutive = 0
        self._last_reconnect = 0.0
        self._last_report = 0.0
        self.errors = 0
        self.reconnects = 0
        self.on_recovered: Callable[[str], None] = lambda why="": None

    @property
    def degraded(self) -> bool:
        return self._consecutive > 0

    def read(self) -> list[float] | None:
        try:
            values = self.arm.read()
        except Exception as exc:
            self._failed("read", exc)
            return None
        self._consecutive = 0
        return values

    def command(self, values: Sequence[float]) -> bool:
        try:
            self.arm.command(values)
        except Exception as exc:
            self._failed("command", exc)
            return False
        self._consecutive = 0
        return True

    def _failed(self, what: str, exc: BaseException) -> None:
        self.errors += 1
        self._consecutive += 1
        now = time.monotonic()
        if self._consecutive == 1 or now - self._last_report >= 1.0:
            self._last_report = now
            logger.warning("%s %s: %s failed (%s) - %d consecutive",
                           self.arm.side, self.arm.role, what, exc, self._consecutive)
        if self._consecutive < self._errors_before_reconnect:
            return
        if now - self._last_reconnect < self._reconnect_interval_s:
            return
        self._last_reconnect = now
        logger.warning("%s %s: reopening after %d consecutive failures",
                       self.arm.side, self.arm.role, self._consecutive)
        try:
            self.arm.reconnect()
        except Exception as exc:
            logger.warning("%s %s: reopen failed (%s) - will retry in %.0fs",
                           self.arm.side, self.arm.role, exc, self._reconnect_interval_s)
            return
        self.reconnects += 1
        self._consecutive = 0
        self.on_recovered(f"{self.arm.side} arm reopened")


class StepLimiter:
    """Moves each commanded joint target at most `max_step_rad` per command, so an arm never jumps.

    It ramps the TARGET and never clamps the tracking error: the motors run
    impedance control, where torque follows `kp * (target - present)`, so
    clamping the error would cap force and leave a gravity-loaded joint stuck.
    `max_lag_rad` only stops the target running away from an arm that cannot follow.

    The ramp restarts from the present pose at startup, after an arm is
    reopened, and after a pause in commands. `uncapped` fields (the grippers)
    skip it. See tutorial/02-robot-loop.md.
    """

    _IDLE_RESEED_S = 0.5

    def __init__(self, max_step_rad: float, max_lag_rad: float, uncapped: Sequence[str] = ()) -> None:
        self.max_step_rad = max_step_rad
        self.max_lag_rad = max_lag_rad
        self.uncapped = {name.strip() for name in uncapped if name.strip()}
        self._present: dict[str, float] = {}
        self._target: dict[str, float] = {}
        self._last_command = 0.0
        self.clamped = 0

    @property
    def enabled(self) -> bool:
        return self.max_step_rad > 0

    def reseed(self, why: str = "") -> None:
        """Start the next ramp from wherever the arms actually are."""
        if self._target:
            logger.info("speed cap: re-seeding the ramp from the present pose%s", f" ({why})" if why else "")
        self._target = {}

    def observe(self, state: dict[str, float]) -> None:
        self._present.update(state)

    def knows(self, name: str) -> bool:
        return name in self._present

    def step(self, action: dict[str, float]) -> dict[str, float]:
        now = time.monotonic()
        if self._last_command and now - self._last_command > self._IDLE_RESEED_S:
            self.reseed("commands resumed after a pause")
        self._last_command = now
        if not self.enabled:
            return dict(action)
        stepped = {}
        for name, goal in action.items():
            goal = float(goal)
            present = self._present.get(name)
            if present is None or name.removesuffix(".pos") in self.uncapped:
                stepped[name] = goal
                continue
            base = self._target.get(name, present)
            target = base + max(-self.max_step_rad, min(self.max_step_rad, goal - base))
            target = max(present - self.max_lag_rad, min(present + self.max_lag_rad, target))
            if target != goal:
                self.clamped += 1
            self._target[name] = target
            stepped[name] = target
        return stepped


class ResilientCamera:
    """Serves the last good frame through a camera hiccup and reconnects in the background.

    A raising camera would otherwise throw away the motor state read that tick.
    """

    def __init__(self, name: str, cam, max_age_ms: float, reconnect_interval_s: float = 2.0,
                 stale_before_reconnect: int = 30) -> None:
        self._name = name
        self._cam = cam
        self._max_age_ms = max_age_ms
        self._reconnect_interval_s = reconnect_interval_s
        # A hung camera only times out; reconnect it after ~1s of that.
        self._stale_before_reconnect = stale_before_reconnect
        self._consecutive_stale = 0
        self.last_frame: np.ndarray | None = None
        self._last_reconnect = 0.0
        self._reconnecting = False
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._cam.connect()

    def disconnect(self) -> None:
        try:
            self._cam.disconnect()
        except Exception:
            pass  # already down

    def read_latest(self) -> np.ndarray | None:
        try:
            self.last_frame = self._cam.read_latest(max_age_ms=self._max_age_ms)
        except TimeoutError as exc:
            # Older than our bar, but a stale frame beats a hole in the row.
            logger.warning("%s: %s - serving stale frame", self._name, exc)
            try:
                self.last_frame = self._cam.read_latest(max_age_ms=_STALE_CEILING_MS)
            except Exception:
                pass  # thread died between the two calls
            self._consecutive_stale += 1
            if self._consecutive_stale >= self._stale_before_reconnect:
                logger.warning("%s: %d consecutive stale reads - camera appears hung, reconnecting",
                               self._name, self._consecutive_stale)
                self._consecutive_stale = 0
                self._maybe_reconnect()
        except Exception as exc:
            logger.warning("%s: read failed (%s) - reconnecting, reusing last frame", self._name, exc)
            self._consecutive_stale = 0
            self._maybe_reconnect()
        else:
            self._consecutive_stale = 0
        return self.last_frame

    def _maybe_reconnect(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._reconnecting or now - self._last_reconnect < self._reconnect_interval_s:
                return
            self._reconnecting, self._last_reconnect = True, now
        threading.Thread(target=self._reconnect, name=f"{self._name}-reconnect", daemon=True).start()

    def _reconnect(self) -> None:
        try:
            self.disconnect()
            self._cam.connect(warmup=False)
            logger.info("%s: reconnected", self._name)
        except Exception as exc:
            logger.warning("%s: reconnect failed (%s) - will retry", self._name, exc)
        finally:
            self._reconnecting = False


class MetalFollowers:
    """Both follower arms and the three cameras, behind `get_observation` / `send_action`.

    A state is published only when both arms read: a half-filled row would be
    invented data. Commands go through the step limiter, then the soft limits.
    """

    def __init__(self, arms: dict[str, ResilientArm], cameras: dict[str, ResilientCamera],
                 limiter: StepLimiter) -> None:
        self.arms = arms
        self.cameras = cameras
        self.step_limiter = limiter
        for arm in arms.values():
            arm.on_recovered = limiter.reseed  # an arm reopens wherever gravity left it

    def connect(self) -> None:
        for camera in self.cameras.values():
            camera.connect()
        for arm in self.arms.values():
            arm.arm.connect()

    def get_observation(self) -> dict[str, Any]:
        observation: dict[str, Any] = {}
        state: dict[str, float] = {}
        for side, arm in self.arms.items():
            values = arm.read()
            if values is None:
                state = {}
                break
            state.update(zip(arm_fields(side), values))
        self.step_limiter.observe(state)
        observation.update(state)
        for name, camera in self.cameras.items():
            if (frame := camera.read_latest()) is not None:
                observation[name] = frame
        return observation

    def send_action(self, action: dict[str, float]) -> None:
        stepped = clamp_to_limits(self.step_limiter.step(action))
        for side, arm in self.arms.items():
            fields = arm_fields(side)
            # Never command an arm we have not read: the ramp needs its present pose.
            if not all(name in stepped and self.step_limiter.knows(name) for name in fields):
                continue
            arm.command([stepped[name] for name in fields])

    def disconnect(self) -> None:
        for arm in self.arms.values():
            arm.arm.disconnect()
        for camera in self.cameras.values():
            camera.disconnect()


class MetalLeaders:
    """Both leader arms in gravity compensation; `get_action` reads them as one Portal action."""

    def __init__(self, arms: dict[str, ResilientArm]) -> None:
        self.arms = arms

    def connect(self) -> None:
        for arm in self.arms.values():
            arm.arm.connect()

    def get_action(self) -> dict[str, float]:
        """All fourteen fields, or {} while either leader is unreadable."""
        action: dict[str, float] = {}
        for side, arm in self.arms.items():
            values = arm.read()
            if values is None:
                return {}
            action.update(zip(arm_fields(side), values))
        return action

    def disconnect(self) -> None:
        for arm in self.arms.values():
            arm.arm.disconnect()


def _arms(role: str, defaults: dict[str, str]) -> dict[str, ResilientArm]:
    urdf, end_type = _urdf_path(), env_int("METAL_END_TYPE", 3)
    return {
        side: ResilientArm(
            MetalArm(side, env_str(f"METAL_{role.upper()}_{side.upper()}_CAN", defaults[side]), role,
                     urdf=urdf, end_type=end_type),
            errors_before_reconnect=env_int("METAL_BUS_ERRORS_BEFORE_RECONNECT", 5),
            reconnect_interval_s=env_float("METAL_BUS_RECONNECT_INTERVAL_S", 2.0),
        )
        for side in SIDES
    }


def build_followers(tracks: Sequence[str], fps: int) -> MetalFollowers:
    """Both followers and one camera per `portal.yaml` video track.

    CAN links default to the vendor's two-pair layout (right follower can1,
    left follower can3). A track `<name>_camera` reads its device from
    `METAL_CAM_<NAME>` (overhead_camera -> METAL_CAM_OVERHEAD). Linux forces
    V4L2, because other OpenCV backends can ignore the requested format and size.
    """
    from lerobot.cameras import Cv2Backends, Cv2Rotation
    from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig

    width, height = env_int("METAL_CAM_WIDTH", 640), env_int("METAL_CAM_HEIGHT", 480)
    max_age_ms = env_int("METAL_CAM_MAX_FRAME_AGE_MS", 500)
    cameras = {}
    for i, name in enumerate(tracks):
        config = OpenCVCameraConfig(
            index_or_path=env_camera_id(f"METAL_CAM_{name.removesuffix('_camera').upper()}", f"/dev/video{2 * i}"),
            fps=fps, width=width, height=height, rotation=Cv2Rotation.NO_ROTATION, fourcc="MJPG",
            backend=Cv2Backends.V4L2 if platform.system() == "Linux" else Cv2Backends.ANY,
        )
        cameras[name] = ResilientCamera(name, OpenCVCamera(config), max_age_ms)

    limiter = StepLimiter(
        max_step_rad=env_float("METAL_MAX_STEP_RAD", 0.087),
        max_lag_rad=env_float("METAL_MAX_LAG_RAD", 0.5),
        uncapped=env_str("METAL_UNCAPPED_JOINTS", "left_gripper,right_gripper").split(","),
    )
    logger.info("speed cap: %.3f rad per command (%.1f rad/s at %d fps); uncapped: %s",
                limiter.max_step_rad, limiter.max_step_rad * fps, fps, ", ".join(sorted(limiter.uncapped)) or "none")
    return MetalFollowers(_arms("follower", {"left": "can3", "right": "can1"}), cameras, limiter)


def build_leaders() -> MetalLeaders:
    """Both leaders; CAN links default to the vendor's layout (right leader can0, left leader can2)."""
    return MetalLeaders(_arms("leader", {"left": "can2", "right": "can0"}))


def split_state_frames(
    observation: dict, tracks: Sequence[str]
) -> tuple[dict[str, float], dict[str, np.ndarray]]:
    """Split a follower observation into (state, frames). Only `.pos` values are published."""
    state = {k: float(v) for k, v in observation.items() if k.endswith(".pos")}
    frames = {t: observation[t] for t in tracks if observation.get(t) is not None}
    return state, frames
