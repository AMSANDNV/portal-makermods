"""Robot safety: what the arms do when things go wrong. No hardware, no server.

    uv run python -m test.test_resilience
"""
from __future__ import annotations

import math
import os
import sys
from types import SimpleNamespace

import numpy as np

from utils.metal import GRIPPER_MAX_MM, LIMITS_RAD, Arm, Followers, Leaders, StepLimiter, arm_fields, clamp_to_limits
from test._harness import Checks

HOME = [0.0, -1.2, 1.5, 0.0, 0.0, 0.0, 40.0]  # inside every soft limit; gripper 40 mm


class FakeArm:
    """Stands in for a `robots.followers.FollowerArm` at the calls `Arm` makes.

    `read()` raises like the driver does on a silent or faulted motor; `write()` records the mm target it was handed.
    """

    def __init__(self):
        self.raises, self.commands = False, []

    def read(self):
        if self.raises:
            raise OSError("CAN read failed")
        return SimpleNamespace(positions_rad=np.array(HOME[:6], dtype=np.float64), gripper=HOME[6] / GRIPPER_MAX_MM)

    def write(self, command):
        self.commands.append([*command.positions_rad, command.gripper * GRIPPER_MAX_MM])

    def close(self):
        pass


class TrackingArm(FakeArm):
    """Follows each command exactly, or not at all when `blocked`; records a switch-off."""

    def __init__(self, start, blocked=False):
        super().__init__()
        self.pose, self.blocked, self.off = list(start), blocked, False

    def read(self):
        if self.raises:
            raise OSError("CAN read failed")
        return SimpleNamespace(positions_rad=np.array(self.pose[:6]), gripper=self.pose[6] / GRIPPER_MAX_MM)

    def write(self, command):
        super().write(command)
        if not self.blocked:
            self.pose = self.commands[-1]

    def torque_off(self):
        self.off = True


def parking(blocked=False):
    arms = {}
    for side in ("left", "right"):
        arms[side] = Arm(side, "can0", leader=False)
        arms[side]._arm = TrackingArm([0.5, -1.0, 1.0, 0.2, 0.0, 0.0, 30.0], blocked and side == "left")
        arms[side].rest = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return Followers(arms, {}, StepLimiter(0.1))


class FakeCamera:
    def read(self):
        return "frame"


def arm(side):
    a = Arm(side, "can0", leader=False)
    a._arm = FakeArm()
    return a


def pair(step=0.1):
    return Followers({s: arm(s) for s in ("left", "right")}, {"overhead_camera": FakeCamera()}, StepLimiter(step))


def action(left, right):
    return dict(zip(arm_fields("left"), left)) | dict(zip(arm_fields("right"), right))


def main() -> int:
    c = Checks("robot safety")

    a = arm("left")
    a._arm.raises = True
    c.check(a.read() is None and a.errors == 1, "a failed read returns None instead of raising")
    c.check(a.read() is None and a.errors == 2, "every failed read counts; a motor fault never ends the loop")

    p = pair()
    state, frames = p.get_observation()
    c.check(len(state) == 14 and frames == {"overhead_camera": "frame"}, "both arms read: fourteen values and video")
    p.arms["right"]._arm.raises = True
    state, frames = p.get_observation()
    c.check(state == {} and frames, "one arm unreadable: no state at all, but video keeps flowing")

    p = pair()
    p.send_action(action(HOME, HOME))
    c.check(not p.arms["left"]._arm.commands, "no command before the first observation")

    p = pair(step=0.1)
    p.get_observation()
    far = [1.0] + HOME[1:6] + [0.0]  # joint1 a radian away, gripper closed
    p.send_action(action(far, HOME))
    sent = p.arms["left"]._arm.commands[-1]
    c.check(math.isclose(sent[0], 0.1), "a far target advances by max_step", str(sent[0]))
    c.check(sent[6] == 0.0, "the gripper is not ramped", str(sent[6]))
    p.send_action(action(far, HOME))
    c.check(math.isclose(p.arms["left"]._arm.commands[-1][0], 0.2), "the ramp continues from the last target")

    limiter = StepLimiter(0.3)
    limiter.present = {"left_joint1.pos": 0.0}
    for _ in range(10):
        out = limiter.step({"left_joint1.pos": 3.0})
    c.check(math.isclose(out["left_joint1.pos"], StepLimiter.MAX_LAG), "a stuck joint's target leads by MAX_LAG at most")
    limiter = StepLimiter(0.0)
    limiter.present = {"left_joint1.pos": 0.0}
    c.check(limiter.step({"left_joint1.pos": 2.0}) == {"left_joint1.pos": 2.0}, "max_step 0 disables the ramp")

    clamped = clamp_to_limits(action([9, 9, -9, 9, 9, 9, 500], [-9] * 6 + [-5]))
    joints_ok = all(lo <= clamped[f"{s}_joint{i + 1}.pos"] <= hi
                    for s in ("left", "right") for i, (lo, hi) in enumerate(LIMITS_RAD))
    c.check(joints_ok, "every joint lands inside its soft limit")
    c.check(clamped["left_gripper.pos"] == GRIPPER_MAX_MM and clamped["right_gripper.pos"] == 0.0,
            "grippers stay in 0-100 mm")

    leaders = Leaders({s: arm(s) for s in ("left", "right")})
    c.check(len(leaders.get_action()) == 14, "both leaders read: one fourteen-value action")
    leaders.arms["left"]._arm.raises = True
    c.check(leaders.get_action() == {}, "one leader unreadable: no action, never half of one")

    os.environ["METAL_PARK_DEG_S"] = "3000"  # fast, so the test takes a second
    p = parking()
    p.park(fps=100)
    poses = [a._arm.pose[:6] for a in p.arms.values()]
    c.check(all(max(map(abs, pose)) < 1e-6 for pose in poses), "park walks both arms to their rest pose")
    c.check(all(a._arm.off for a in p.arms.values()), "park switches the arms off once they are at rest")
    steps = [abs(b[1] - a[1]) for a, b in zip(p.arms["right"]._arm.commands, p.arms["right"]._arm.commands[1:])]
    c.check(max(steps) <= math.radians(30) + 1e-9, "park never jumps more than one step per tick", f"{max(steps):.3f}")

    p = parking(blocked=True)
    try:
        p.park(fps=100)
        raised = False
    except RuntimeError:
        raised = True
    c.check(raised and not any(a._arm.off for a in p.arms.values()),
            "a blocked arm stops the park: nothing is switched off")

    p = parking()
    p.arms["right"]._arm.raises = True
    try:
        p.park(fps=100)
        raised = False
    except RuntimeError:
        raised = True
    c.check(raised and not p.arms["left"]._arm.commands and not p.arms["left"]._arm.off,
            "an unreadable arm: no park, nothing moves, nothing switched off")
    return c.exit_code


if __name__ == "__main__":
    sys.exit(main())
