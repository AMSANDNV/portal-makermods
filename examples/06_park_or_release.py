#!/usr/bin/env python3
"""Safely walk the Maker Arm back to its folded rest pose (or a custom pose) and release torque,
or release torque immediately while you support the arm by hand.

Usage:
  # Option 1: Slowly walk joints back to the folded rest pose (near zero) and release torque:
  .venv/bin/python examples/06_park_or_release.py --backend slcan --port /dev/ttyACM0 --walk-to-folded

  # Option 2: Release torque right now (support the arm with your hand first so it doesn't drop!):
  .venv/bin/python examples/06_park_or_release.py --backend slcan --port /dev/ttyACM0 --release-now
"""

from __future__ import annotations

import math
import time

from maker_arm.cli.common import add_gravity_arg, apply_gravity_arg, arm_from_args, make_parser
from maker_arm.cli.safety import require_interactive_terminal

# Safe folded rest target inside Maker Arm v1 soft limits:
# J2 limits are [-175.3, -3.2] deg, J3 limits are [2.8, 236.1] deg, gripper [-96.0, -2.5] deg.
FOLDED_REST_DEG = [0.0, -5.0, 5.0, 0.0, 0.0, 0.0, -45.0]


def main() -> None:
    ap = make_parser(__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--walk-to-folded",
        action="store_true",
        help="smoothly walk the arm at 15 deg/s back to the folded rest pose, then release torque",
    )
    mode.add_argument(
        "--release-now",
        action="store_true",
        help="release motor torque immediately (hold the arm with your hand first!)",
    )
    ap.add_argument(
        "--speed-deg-s",
        type=float,
        default=15.0,
        help="speed when walking to the folded pose in deg/s (default: 15.0)",
    )
    add_gravity_arg(ap)
    args = ap.parse_args()
    require_interactive_terminal("park or release")

    arm = arm_from_args(args)
    if args.backend == "slcan":
        arm.config.control_rate_hz = 25.0
    arm.config.rest_speed = math.radians(max(5.0, min(30.0, args.speed_deg_s)))
    apply_gravity_arg(arm, args)

    arm.connect()
    try:
        pos_deg = [math.degrees(p) for p in arm.get_joint_positions()]
        print("Current joint angles (deg):")
        for j, deg in zip(arm.config.joints, pos_deg):
            print(f"  J{j.motor_id} ({j.label:<13}): {deg:+6.1f} deg")

        if args.release_now:
            ans = input(
                "\n⚠️ Hold/support the arm firmly with your hand (it has no brakes), "
                "then type RELEASE and press ENTER > "
            )
            if ans.strip().upper() != "RELEASE":
                print("Cancelled; torque state unchanged.")
                return
            arm.disable()
            print("Torque released on all 7 motors. You can now move the arm to rest by hand.")
            return

        target_rad = [math.radians(d) for d in FOLDED_REST_DEG]
        print(
            f"\nTarget folded rest pose (deg): "
            + ", ".join(f"J{i+1}={d:+.1f}" for i, d in enumerate(FOLDED_REST_DEG))
        )
        ans = input(
            "Ensure the workspace is clear, keep a hand near the power switch, "
            "and type HOME to walk to rest > "
        )
        if ans.strip().upper() != "HOME":
            print("Cancelled; nothing moved.")
            return

        arm.enable()
        time.sleep(0.2)
        print(f"Walking to folded rest pose at {args.speed_deg_s:.1f} deg/s...")
        ok = arm.return_to_rest(target_rad)
        if ok:
            print("Reached folded rest pose! Releasing torque...")
            arm.disable()
            print("Torque released safely.")
        else:
            print(
                "\nCould not verify arrival at the folded pose; the arm is holding its position."
            )
            ans2 = input("Support the arm by hand, then type RELEASE to switch torque off > ")
            if ans2.strip().upper() == "RELEASE":
                arm.disable()
                print("Torque released.")
    except KeyboardInterrupt:
        print("\nInterrupted! Holding current position.")
        if arm.state.name in ("ENABLED", "FAULT"):
            arm.hold_current_position()
            ans = input("Support the arm by hand, then type RELEASE to switch torque off > ")
            if ans.strip().upper() == "RELEASE":
                arm.disable()
                print("Torque released.")
    finally:
        arm.disconnect()


if __name__ == "__main__":
    main()
