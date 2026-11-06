"""Two Star Arm 102 leaders, moved by hand, read as targets for the Metal followers.

Each leader is seven FashionStar UART servos (ids 0-6: joint1..joint6, then the gripper), read with
their motors off through `motorbridge-smart-servo`. Readings come out in the followers' units: joints
in radians, the gripper in mm (0 closed). See tutorial/06-operator-loop.md.

The servo handling and the Metal mapping are adapted from MakerMods' lerobot fork
(`teleoperators/rebot_102_leader`, Apache-2.0), used by MakerMods Lab.
"""
from __future__ import annotations

import logging
import math
import time

from utils.common import env_float, required_env
from utils.metal import GRIPPER_MAX_MM, LIMITS_DEG, SIDES, read_pair

logger = logging.getLogger(__name__)

SERVO_IDS = list(range(7))  # joint1..joint6, gripper
BAUDRATE = 1_000_000
# Leader servo degrees times these are follower degrees: the sign flips a joint, the size rescales
# its travel (the leader's elbow turns ~270 deg for the follower's 180).
JOINT_SCALES = (-1.0, -1.0, -0.667, -1.0, 1.0, -1.0)
# The gripper servo's turn from closed to fully open. 41.2 is MakerMods' vertical grip for the Metal;
# the stock lever grip is about 60.7. Negative if the grip turns the other way.
GRIPPER_TRAVEL_DEG = 41.2


def unwrap(raw_deg: float, lo: float, hi: float) -> float:
    """A multi-turn servo angle moved by whole turns into the 360 deg window centred on [lo, hi]."""
    center = (lo + hi) / 2
    return raw_deg - 360.0 * round((raw_deg - center) / 360.0)


def to_follower(raw_deg: list[float], gripper_travel_deg: float) -> list[float]:
    """Seven leader servo angles as one follower arm's seven values, inside the follower's limits."""
    values = []
    for raw, scale, (lo, hi) in zip(raw_deg, JOINT_SCALES, LIMITS_DEG):
        deg = unwrap(raw, lo / scale, hi / scale) * scale
        values.append(math.radians(min(max(deg, lo), hi)))
    opened = unwrap(raw_deg[6], 0.0, gripper_travel_deg) / gripper_travel_deg
    values.append(min(max(opened, 0.0), 1.0) * GRIPPER_MAX_MM)
    return values


class StarLeader:
    """One leader. `read()` returns None on a hiccup instead of raising, and never a stale angle."""

    def __init__(self, side: str, port: str, gripper_travel_deg: float) -> None:
        self.side, self.port, self.gripper_travel_deg = side, port, gripper_travel_deg
        self.errors = 0
        self._bus = None  # motorbridge_smart_servo.FashionStarServo

    def _open_bus(self):
        try:
            from motorbridge_smart_servo import FashionStarServo
        except ImportError as exc:
            raise RuntimeError("motorbridge-smart-servo is not installed: run `uv sync`") from exc
        bus = FashionStarServo(self.port, baudrate=BAUDRATE)
        try:
            for servo in SERVO_IDS:
                if not bus.ping(servo):
                    raise RuntimeError(f"{self.side} leader: servo {servo} does not answer on {self.port}")
                bus.unlock(servo)  # motor off: the leader is moved by hand
                time.sleep(0.01)
        except Exception:
            bus.close()
            raise
        return bus

    def connect(self) -> None:
        self._bus = self._open_bus()
        for servo in SERVO_IDS:
            self._bus.reset_multi_turn(servo)
        if self.read() is None:
            raise RuntimeError(f"{self.side} leader: no pose after opening {self.port}")

    def zero(self) -> None:
        """Make the present pose every servo's zero. Hold the arm in the followers' zero pose, gripper closed."""
        bus = self._open_bus()
        try:
            for servo in SERVO_IDS:
                bus.set_origin_point(servo)
        finally:
            bus.close()

    def read(self) -> list[float] | None:
        try:
            monitors = self._bus.sync_monitor(SERVO_IDS)
            # The library holds a missed servo's last angle and marks it unreliable: refuse it.
            stale = [servo for servo in SERVO_IDS if monitors.get(servo) is None or not monitors[servo].reliable]
            if stale:
                raise RuntimeError(f"servos {stale} did not answer")
            values = to_follower([float(monitors[servo].angle_deg) for servo in SERVO_IDS], self.gripper_travel_deg)
            if not all(map(math.isfinite, values)):
                raise RuntimeError(f"bad reading {values}")
            return values
        except Exception as exc:
            self.errors += 1
            if self.errors == 1 or self.errors % 30 == 0:  # first failure, then about once a second
                logger.warning("%s leader: read failed (%s); %d so far", self.side, exc, self.errors)
            return None

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None


class Leaders:
    """Both leaders, read as one Portal action."""

    def __init__(self, arms: dict[str, StarLeader]) -> None:
        self.arms = arms

    def connect(self) -> None:
        try:
            for arm in self.arms.values():
                arm.connect()
        except Exception:
            self.disconnect()
            raise

    def get_action(self) -> dict[str, float]:
        return read_pair(self.arms)

    def disconnect(self) -> None:
        for arm in self.arms.values():
            arm.close()


def build_leaders() -> Leaders:
    """One leader per side, on the serial port `STAR_LEADER_<SIDE>_PORT` names; required, so arms never swap."""
    travel = env_float("STAR_GRIPPER_TRAVEL_DEG", GRIPPER_TRAVEL_DEG)
    return Leaders({side: StarLeader(side, required_env(f"STAR_LEADER_{side.upper()}_PORT"), travel)
                    for side in SIDES})


def check_leaders() -> None:
    """Print both leaders twice a second, in follower units, until ctrl-c: move an arm to see its port."""
    leaders = build_leaders()
    leaders.connect()
    try:
        while True:
            for side, arm in leaders.arms.items():
                if (values := arm.read()) is not None:
                    joints = " ".join(f"{math.degrees(v):+5.0f}" for v in values[:6])
                    print(f"{side:5s} on {arm.port}: joints {joints} deg, gripper {values[6]:3.0f} mm")
            print()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        leaders.disconnect()


def zero_leaders() -> None:
    """Set both leaders' zero pose, once per build: it is stored in the servos."""
    leaders = build_leaders()
    input("Hold both leaders in the followers' zero pose (every joint at 0), grippers closed, then press Enter: ")
    for arm in leaders.arms.values():
        arm.zero()
        print(f"{arm.side} leader on {arm.port}: zeroed")
