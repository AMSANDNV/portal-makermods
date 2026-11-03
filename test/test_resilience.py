"""Robot safety: what the arms do when things go wrong. No hardware, no server.

    uv run python -m test.test_resilience
"""
from __future__ import annotations

import math
import sys

from utils.metal import LIMITS_RAD, Arm, Followers, Leaders, StepLimiter, arm_fields, clamp_to_limits
from test._harness import Checks

HOME = [0.0, -1.2, 1.5, 0.0, 0.0, 0.0, 40.0]  # inside every soft limit; gripper half open


class FakeSDK:
    """Stands in for `MetalSDKInterface` at the calls `Arm` makes."""

    def __init__(self):
        self.fault, self.raises, self.commands = False, False, []

    def GetJointErrorCode(self):
        return [1, 2 if self.fault else 1, 1, 1, 1, 1, 1]

    def GetJointPosition(self):
        if self.raises:
            raise OSError("CAN read failed")
        return list(HOME)

    def SetFollowerArmJointPosition(self, values):
        self.commands.append(list(values))


class FakeCamera:
    def read(self):
        return "frame"


def arm(side):
    a = Arm(side, "can0", leader=False)
    a._sdk = FakeSDK()
    return a


def pair(step=0.1):
    return Followers({s: arm(s) for s in ("left", "right")}, {"overhead_camera": FakeCamera()}, StepLimiter(step))


def action(left, right):
    return dict(zip(arm_fields("left"), left)) | dict(zip(arm_fields("right"), right))


def main() -> int:
    c = Checks("robot safety")

    a = arm("left")
    a._sdk.raises = True
    c.check(a.read() is None and a.errors == 1, "a failed read returns None instead of raising")
    a._sdk.raises, a._sdk.fault = False, True
    c.check(a.read() is None, "a motor fault code counts as a failed read")

    p = pair()
    state, frames = p.get_observation()
    c.check(len(state) == 14 and frames == {"overhead_camera": "frame"}, "both arms read: fourteen values and video")
    p.arms["right"]._sdk.fault = True
    state, frames = p.get_observation()
    c.check(state == {} and frames, "one arm unreadable: no state at all, but video keeps flowing")

    p = pair()
    p.send_action(action(HOME, HOME))
    c.check(not p.arms["left"]._sdk.commands, "no command before the first observation")

    p = pair(step=0.1)
    p.get_observation()
    far = [1.0] + HOME[1:6] + [0.0]  # joint1 a radian away, gripper closed
    p.send_action(action(far, HOME))
    sent = p.arms["left"]._sdk.commands[-1]
    c.check(math.isclose(sent[0], 0.1), "a far target advances by max_step", str(sent[0]))
    c.check(sent[6] == 0.0, "the gripper is not ramped", str(sent[6]))
    p.send_action(action(far, HOME))
    c.check(math.isclose(p.arms["left"]._sdk.commands[-1][0], 0.2), "the ramp continues from the last target")

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
    c.check(clamped["left_gripper.pos"] == 80.0 and clamped["right_gripper.pos"] == 0.0, "grippers stay in 0-80 mm")

    leaders = Leaders({s: arm(s) for s in ("left", "right")})
    c.check(len(leaders.get_action()) == 14, "both leaders read: one fourteen-value action")
    leaders.arms["left"]._sdk.raises = True
    c.check(leaders.get_action() == {}, "one leader unreadable: no action, never half of one")
    return c.exit_code


if __name__ == "__main__":
    sys.exit(main())
