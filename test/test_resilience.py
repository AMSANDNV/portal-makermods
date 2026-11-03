"""Robot safety test: what the arms do when things go wrong.

    uv run python -m test.test_resilience      (no LiveKit server, no hardware)

Layers between the network and the motors, all in `src/utils/metal.py`:

`ResilientArm`: a CAN read or command that fails must not raise out of
`HardwareThread.run` and end the tick loop mid-motion; after a run of failures
the arm is reopened, with a backoff.

`StepLimiter`: the commanded target advances at most `max_step_rad` per
command. The case that matters is the discontinuity: after a fault an arm has
sagged and the leader has not moved, so an unramped command would send it
straight back at full speed. The cap ramps the TARGET; it must never clamp the
tracking error, because under impedance control that error is the torque.

`MetalFollowers`: a state row is published only when both arms read, an arm is
never commanded before it was read, and every command lands inside the soft limits.

The fake stands in for `MetalArm` at exactly the surface the wrappers touch.
"""
from __future__ import annotations

import math
import sys

from utils.metal import (
    JOINT_LIMITS_RAD, MetalFollowers, MetalLeaders, ResilientArm, StepLimiter, arm_fields, clamp_to_limits,
)
from test._harness import Checks

ERRORS_BEFORE_RECONNECT = 3
HOME = [0.0, -1.2, 1.5, 0.0, 0.0, 0.0, 40.0]  # inside every soft limit; gripper half open


class FakeArm:
    """Fails I/O while `failing` is set; `reconnect` counts and may fail."""

    def __init__(self, side: str, role: str = "follower", reconnect_fails: int = 0) -> None:
        self.side, self.role = side, role
        self.failing = False
        self.reconnect_fails = reconnect_fails
        self.reconnects = 0
        self.connected = False
        self.pose = list(HOME)
        self.commands: list[list[float]] = []

    def connect(self) -> None:
        self.connected = True

    def read(self) -> list[float]:
        if self.failing:
            raise RuntimeError("joint2 motor disconnected")
        return list(self.pose)

    def command(self, values) -> None:
        if self.failing:
            raise OSError("write failed: [Errno 105] No buffer space available")
        self.commands.append(list(values))

    def reconnect(self) -> None:
        self.reconnects += 1
        if self.reconnects <= self.reconnect_fails:
            raise RuntimeError("Init() failed")

    def disconnect(self) -> None:
        self.connected = False


class FakeCamera:
    def __init__(self, name):
        self.name = name

    def connect(self):
        pass

    def disconnect(self):
        pass

    def read_latest(self):
        return f"{self.name}-frame"


def resilient(arm, interval=0.0):
    return ResilientArm(arm, errors_before_reconnect=ERRORS_BEFORE_RECONNECT, reconnect_interval_s=interval)


def followers(step=0.1, lag=0.5):
    arms = {side: resilient(FakeArm(side)) for side in ("left", "right")}
    limiter = StepLimiter(step, lag, uncapped=("left_gripper", "right_gripper"))
    return MetalFollowers(arms, {"overhead_camera": FakeCamera("overhead_camera")}, limiter)


def action_for(pose_left, pose_right):
    return dict(zip(arm_fields("left"), pose_left)) | dict(zip(arm_fields("right"), pose_right))


def main() -> int:
    c = Checks("robot safety")

    # -- a transient failure must not raise, and must not reconnect ----------
    fake = FakeArm("left")
    arm = resilient(fake)
    fake.failing = True
    c.check(arm.read() is None, "a failed read returns None instead of raising")
    c.check(arm.command(HOME) is False, "a failed command returns False instead of raising")
    c.check(arm.errors == 2 and fake.reconnects == 0, "below the threshold nothing is reopened",
            f"errors={arm.errors}")
    c.check(arm.degraded, "the fault is visible while it lasts")

    # -- a sustained failure reopens the arm, and recovery is reported ------
    recovered: list[str] = []
    arm.on_recovered = recovered.append
    arm.read()
    c.check(fake.reconnects == 1 and arm.reconnects == 1, "the threshold reopens the arm",
            f"reconnects={fake.reconnects}")
    c.check(recovered == ["left arm reopened"], "recovery is reported, so the ramp re-seeds", str(recovered))
    fake.failing = False
    c.check(arm.read() == HOME and not arm.degraded, "and it reads again afterwards")

    # -- backoff: a reopen is not attempted every tick -----------------------
    fake = FakeArm("right", reconnect_fails=99)
    arm = resilient(fake, interval=30.0)
    fake.failing = True
    for _ in range(ERRORS_BEFORE_RECONNECT * 10):
        arm.command(HOME)
    c.check(fake.reconnects == 1, "the backoff holds off a reopen storm", f"reconnects={fake.reconnects}")

    # -- the pair publishes both arms or nothing -----------------------------
    pair = followers()
    observation = pair.get_observation()
    c.check(len([k for k in observation if k.endswith(".pos")]) == 14, "both arms read: fourteen state values")
    c.check(observation.get("overhead_camera") == "overhead_camera-frame", "and the camera frame")
    pair.arms["right"].arm.failing = True
    observation = pair.get_observation()
    c.check(not any(k.endswith(".pos") for k in observation),
            "one arm unreadable: no state at all, never half a row", str(sorted(observation)))
    c.check("overhead_camera" in observation, "while video keeps flowing")

    # -- an arm is never commanded before it was read ------------------------
    pair = followers()
    pair.send_action(action_for(HOME, HOME))
    c.check(not pair.arms["left"].arm.commands and not pair.arms["right"].arm.commands,
            "no command before the first observation")

    # -- the ramp moves the target, a step at a time -------------------------
    pair = followers(step=0.1)
    pair.get_observation()
    far = list(HOME)
    far[0] = 1.0  # joint1 one radian away
    far[6] = 0.0  # close the gripper
    pair.send_action(action_for(far, HOME))
    sent = pair.arms["left"].arm.commands[-1]
    c.check(math.isclose(sent[0], 0.1), "a far target advances by max_step_rad", str(sent[0]))
    c.check(sent[6] == 0.0, "the gripper is uncapped and closes in one command", str(sent[6]))
    pair.send_action(action_for(far, HOME))
    sent = pair.arms["left"].arm.commands[-1]
    c.check(math.isclose(sent[0], 0.2), "the next command continues from the last target, not the present pose",
            str(sent[0]))

    # -- the lag bound stops the target running away -------------------------
    limiter = StepLimiter(0.3, 0.5)
    limiter.observe({"left_joint1.pos": 0.0})
    for _ in range(10):
        stepped = limiter.step({"left_joint1.pos": 3.0})
    c.check(math.isclose(stepped["left_joint1.pos"], 0.5),
            "an arm that cannot follow holds the target max_lag_rad ahead", str(stepped))

    # -- off means off -------------------------------------------------------
    limiter = StepLimiter(0.0, 0.5)
    limiter.observe({"left_joint1.pos": 0.0})
    c.check(limiter.step({"left_joint1.pos": 2.0}) == {"left_joint1.pos": 2.0},
            "max_step_rad=0 disables the cap entirely")

    # -- soft limits ---------------------------------------------------------
    wild = action_for([9.0, 9.0, -9.0, 9.0, 9.0, 9.0, 500.0], [-9.0] * 6 + [-5.0])
    clamped = clamp_to_limits(wild)
    left = [clamped[k] for k in arm_fields("left")]
    right = [clamped[k] for k in arm_fields("right")]
    c.check(all(lo <= v <= hi for v, (lo, hi) in zip(left, JOINT_LIMITS_RAD))
            and all(lo <= v <= hi for v, (lo, hi) in zip(right, JOINT_LIMITS_RAD)),
            "every joint lands inside its soft limit")
    c.check(left[6] == 80.0 and right[6] == 0.0, "grippers stay inside their 0-80 mm stroke",
            f"{left[6]}, {right[6]}")

    # -- leaders: both or nothing --------------------------------------------
    leaders = MetalLeaders({side: resilient(FakeArm(side, "leader")) for side in ("left", "right")})
    c.check(len(leaders.get_action()) == 14, "both leaders read: one fourteen-value action")
    leaders.arms["left"].arm.failing = True
    c.check(leaders.get_action() == {}, "one leader unreadable: no action, never half of one")
    return c.exit_code


if __name__ == "__main__":
    sys.exit(main())
