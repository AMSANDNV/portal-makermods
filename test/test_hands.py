"""Webcam hand teleop: gesture, hand pose, mapping, IK and the pilot; no camera, model, or robot.

    uv run python -m unittest test.test_hands

Hands are synthetic: a hand of MediaPipe's 21 landmarks, built in the camera frame and projected
through a pinhole, as the model would report it.
"""
import math
import unittest

import numpy as np

from hand_teleop import Pilot, Sighting, build_pilot
from utils.hands import (CAMERA_TO_BASE, CURLED_BELOW, INDEX_TIP, OPEN_ABOVE, THUMB_TIP, ArmFollower, ArmsIK, Clutch,
                         Hand, HandPose, Mapping, OneEuro, assign_sides, camera_to_base, curl, hand_pose, palm_position,
                         palm_rotation, pinch)
from utils.metal import GRIPPER_MAX_MM, SIDES, arm_fields

FOCAL, CENTER = 457.0, (320.0, 240.0)  # 640x480 at a 70 degree field of view


def hand_world(curled: bool = True, pinch_m: float = 0.08) -> np.ndarray:
    """A right hand, palm to the lens, fingers up (camera -y), about its centre (m)."""
    w = np.zeros((21, 3))
    w[0] = (0.0, 0.04, 0.0)  # wrist
    for base, x in ((5, -0.025), (9, 0.0), (13, 0.02), (17, 0.038)):  # index, middle, ring, pinky
        w[base] = (x, -0.05, 0.0)
        straight = base == 5 or not curled
        for k in (1, 2, 3):
            w[base + k] = (x, -0.05 - 0.03 * k, 0.0) if straight else (x, -0.05 + 0.012 * k, -0.025)
    for k in (1, 2, 3):  # thumb, toward the index tip
        w[k] = (-0.03 - 0.01 * k, 0.03 - 0.02 * k, 0.0)
    w[THUMB_TIP] = w[INDEX_TIP] + (-pinch_m, 0.0, 0.0)
    return w - w.mean(axis=0)


def project(world: np.ndarray, at: np.ndarray, rotation: np.ndarray = np.eye(3)) -> Hand:
    """The hand placed with its centre at `at` (camera frame) and seen through the pinhole."""
    points = world @ rotation.T + at
    image = points[:, :2] / points[:, 2:] * FOCAL + CENTER
    return Hand("right", image, world @ rotation.T)


def rot(axis: str, degrees: float) -> np.ndarray:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


class GestureTests(unittest.TestCase):
    def test_curl_tells_a_gripper_shape_from_an_open_hand(self):
        self.assertTrue((curl(hand_world(curled=True)) < CURLED_BELOW).all())
        self.assertTrue((curl(hand_world(curled=False)) > OPEN_ABOVE).all())

    def test_pinch_is_the_thumb_to_index_gap(self):
        self.assertAlmostEqual(pinch(hand_world(pinch_m=0.05)), 0.05)

    def test_clutch_closes_after_three_curled_frames_and_opens_after_two_open(self):
        clutch, curled, open_ = Clutch(), np.array([0.8, 0.7, 0.9]), np.array([0.8, 1.8, 0.9])
        self.assertEqual([clutch.update(curled) for _ in range(3)], [False, False, True])
        self.assertTrue(clutch.update(np.array([0.8, 1.1, 0.9])), "between the thresholds keeps it closed")
        self.assertEqual([clutch.update(open_) for _ in range(2)], [True, False])
        clutch.update(curled), clutch.update(curled)
        self.assertFalse(clutch.update(np.array([0.8, 1.05, 0.9])), "closing needs every finger in the palm")
        for _ in range(3):
            clutch.update(curled)
        self.assertFalse(clutch.update(None), "a lost hand opens it at once")

    def test_clutch_needs_the_fingers_all_the_way_into_the_palm(self):
        clutch = Clutch()
        hooked = np.array([1.2, 1.2, 1.25])  # bent, tips short of the palm
        self.assertFalse(any(clutch.update(hooked) for _ in range(10)), "a half curl never takes the arm")
        for _ in range(3):
            clutch.update(np.array([0.8, 0.7, 0.9]))
        self.assertEqual([clutch.update(hooked) for _ in range(2)], [True, False], "coming out of the palm lets go")

    def test_sides_follow_the_label_or_position(self):
        a = Hand("right", np.full((21, 2), 100.0), np.zeros((21, 3)))  # on the raw image's left
        b = Hand("left", np.full((21, 2), 500.0), np.zeros((21, 3)))
        self.assertEqual(assign_sides([b, a]), {"right": a, "left": b})
        self.assertEqual(assign_sides([a])["right"], a)
        self.assertEqual(assign_sides([a, b], swap=True)["left"].image[0, 0], 100.0)
        same = Hand("right", b.image, b.world)
        self.assertEqual(assign_sides([same, a]), {"right": a, "left": same},
                         "one label for two hands: the one on the raw image's left is the operator's right")


class PoseTests(unittest.TestCase):
    def test_palm_position_finds_the_hand_and_how_far_it_moved(self):
        world = hand_world()
        palm = world[[0, 5, 9, 13, 17]].mean(axis=0)
        found = {}
        for at in ([0.0, 0.0, 0.5], [0.12, -0.05, 0.4], [-0.1, 0.08, 0.7]):
            hand = project(world, np.array(at))
            found[tuple(at)] = palm_position(hand.image, hand.world, FOCAL, CENTER)
            np.testing.assert_allclose(found[tuple(at)], palm + at, atol=0.02 * at[2])
        # What the arm follows is the motion: a few percent of it at most.
        a, b = np.array([0.0, 0.0, 0.5]), np.array([-0.1, 0.08, 0.7])
        moved = found[tuple(b)] - found[tuple(a)]
        self.assertLess(np.linalg.norm(moved - (b - a)), 0.03 * np.linalg.norm(b - a))

    def test_palm_rotation_turns_with_the_hand(self):
        world, turn = hand_world(), rot("y", 30) @ rot("x", -20)
        before, after = palm_rotation(world), palm_rotation(world @ turn.T)
        np.testing.assert_allclose(before.T @ before, np.eye(3), atol=1e-9)
        self.assertAlmostEqual(np.linalg.det(before), 1.0)
        np.testing.assert_allclose(after @ before.T, turn, atol=1e-9)

    def test_camera_motion_maps_to_the_operators_point_of_view(self):
        to_base = camera_to_base(0.0)
        np.testing.assert_allclose(to_base @ [0, 0, -1], [1, 0, 0], err_msg="toward the screen reaches forward")
        np.testing.assert_allclose(to_base @ [-1, 0, 0], [0, -1, 0],
                                   err_msg="the operator's right (the raw image's left) is the robot's right")
        np.testing.assert_allclose(to_base @ [0, -1, 0], [0, 0, 1], err_msg="up is up")
        self.assertAlmostEqual(np.linalg.det(CAMERA_TO_BASE), 1.0)
        np.testing.assert_allclose(camera_to_base(90.0) @ [0, 0, -1], [0, -1, 0], atol=1e-12,
                                   err_msg="an arm turned 90 degrees left sees forward as its right")

    def test_one_euro_quiets_jitter_and_follows_motion(self):
        f, rng = OneEuro(1.0, 4.0), np.random.default_rng(0)
        f.reset([0.5], 0.0)
        still = [f([0.5 + rng.normal(0, 0.003)], k / 30)[0] for k in range(1, 91)]
        self.assertLess(np.std(still[30:]), 0.0012, "3 mm of jitter on a still hand cut to about a third")
        moving = [f([0.5 + 0.3 * k / 30], 3 + k / 30)[0] for k in range(1, 31)]  # 30 cm/s for a second
        self.assertGreater(moving[-1], 0.5 + 0.3 - 0.02, "less than 2 cm behind a 30 cm/s hand")


def pose(at: float, position, rotation=np.eye(3), pinch_m=0.08) -> HandPose:
    return HandPose(at, np.asarray(position, dtype=np.float64), rotation, pinch_m, np.ones(3))


class FollowerTests(unittest.TestCase):
    def follow(self, rotate="off", grip_mm=50.0, pinch_m=0.06):
        self.clock = 0.0
        follower = ArmFollower(Mapping(to_base=camera_to_base(), rotate=rotate))
        tcp = np.eye(4)
        tcp[:3, 3] = (0.3, 0.0, 0.3)
        follower.engage(pose(0.0, [0.0, 0.0, 0.5], pinch_m=pinch_m), tcp, grip_mm)
        return follower

    def run_to(self, follower, position, rotation=np.eye(3), pinch_m=0.06, seconds=2.0):
        for _ in range(int(30 * seconds)):
            self.clock += 1 / 30
            follower.track(pose(self.clock, position, rotation, pinch_m))
            goal = follower.goal(30)
        return goal

    def test_the_tool_moves_by_the_hands_motion(self):
        follower = self.follow()
        goal = self.run_to(follower, [-0.05, -0.02, 0.45])  # right, up, toward the screen
        np.testing.assert_allclose(goal[:3, 3], [0.35, -0.05, 0.32], atol=1e-3)
        np.testing.assert_allclose(goal[:3, :3], np.eye(3), atol=1e-9, err_msg="rotate off keeps the orientation")

    def test_the_target_moves_no_faster_than_the_limit(self):
        follower = self.follow()
        follower.track(pose(1 / 30, [0.0, 0.0, 0.2]))  # 30 cm at once
        step = np.linalg.norm(follower.goal(30)[:3, 3] - [0.3, 0.0, 0.3])
        self.assertLessEqual(step, 0.5 / 30 + 1e-9)

    def test_yaw_keeps_only_the_turn_about_vertical(self):
        follower = self.follow(rotate="yaw")
        # The hand turns about the camera's vertical (-y) by 30 degrees and tilts 20 about x.
        goal = self.run_to(follower, [0.0, 0.0, 0.5], rot("y", -30) @ rot("x", 20))
        self.assertAlmostEqual(goal[2, 2], 1.0, places=6, msg="no tilt")
        self.assertAlmostEqual(math.degrees(math.atan2(goal[1, 0], goal[0, 0])), 30.0, delta=6.0)

    def test_full_rotation_maps_the_turn_into_the_base(self):
        follower = self.follow(rotate="full")
        goal = self.run_to(follower, [0.0, 0.0, 0.5], rot("x", 25))
        expected = CAMERA_TO_BASE @ rot("x", 25) @ CAMERA_TO_BASE.T
        np.testing.assert_allclose(goal[:3, :3], expected, atol=1e-3)

    def test_the_anchor_slides_where_the_arm_cannot_follow(self):
        follower = self.follow()
        goal = self.run_to(follower, [0.0, 0.1, 0.5])  # 10 cm down, into a floor at z = 0.25
        floor = goal.copy()
        floor[2, 3] = 0.25
        follower.settle(floor)
        goal = self.run_to(follower, [0.0, 0.08, 0.5])  # 2 cm back up
        self.assertAlmostEqual(goal[2, 3], 0.27, places=3, msg="rises at once, not after 5 cm of dead zone")

    def test_the_gripper_waits_for_the_pinch_to_reach_it(self):
        follower = self.follow(grip_mm=0.0, pinch_m=0.09)  # holding something; the hand comes back open
        self.assertTrue(follower.grip_waiting)
        self.run_to(follower, [0.0, 0.0, 0.5], pinch_m=0.09, seconds=0.5)
        self.assertEqual(follower.grip_mm, 0.0, "an open hand does not drop it")
        self.run_to(follower, [0.0, 0.0, 0.5], pinch_m=0.02, seconds=0.5)
        self.run_to(follower, [0.0, 0.0, 0.5], pinch_m=0.09, seconds=1.0)
        self.assertGreater(follower.grip_mm, 0.9 * GRIPPER_MAX_MM, "once taken, the pinch drives it")


class IKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ik = ArmsIK(30, table_z_m=0.0, max_joint_rad_s=1.5)
        cls.ik.warmup()

    def test_each_side_moves_only_its_own_arm(self):
        q = {side: self.ik.rest.copy() for side in SIDES}
        goals = {side: self.ik.fk(q[side], side) for side in SIDES}
        goals["left"][2, 3] += 0.01
        for _ in range(15):
            q, _ = self.ik.step(goals, q)
        self.assertAlmostEqual(self.ik.fk(q["left"], "left")[2, 3], goals["left"][2, 3], delta=0.002)
        np.testing.assert_allclose(q["right"], self.ik.rest, atol=1e-3)

    def test_the_floor_holds_the_tool_above_the_table(self):
        q = {side: self.ik.rest.copy() for side in SIDES}
        goals = {side: self.ik.fk(q[side], side) for side in SIDES}
        goals["right"][2, 3] = -0.2
        for _ in range(90):
            q, reachable = self.ik.step(goals, q)
        self.assertGreater(self.ik.fk(q["right"], "right")[2, 3], 0.0)
        self.assertGreater(reachable["right"][2, 3], 0.0)


class PilotTests(unittest.TestCase):
    """The whole chain but MediaPipe: synthetic sightings in, Portal actions out."""

    @classmethod
    def setUpClass(cls):
        cls.ik = ArmsIK(30, table_z_m=0.0, max_joint_rad_s=1.5)
        cls.ik.warmup()

    def setUp(self):
        self.pilot = build_pilot(30, self.ik, "off", 640)
        self.state = {}
        for side in SIDES:
            self.state.update(zip(arm_fields(side), [*self.ik.rest, 40.0]))
        self.seq, self.now = 0, 100.0

    def sight(self, *hands: Hand) -> None:
        self.seq += 1
        self.now += 1 / 30
        self.pilot.see(Sighting(self.seq, self.now, np.zeros((480, 640, 3), np.uint8), list(hands)), self.now)

    def tcp(self, action, side):
        return self.ik.fk([action[name] for name in arm_fields(side)[:6]], side)[:3, 3]

    def test_a_gripping_hand_drives_its_arm_and_an_open_one_lets_go(self):
        open_hand = project(hand_world(curled=False), np.array([0.0, 0.0, 0.5]))
        self.sight(open_hand)
        self.assertIsNone(self.pilot.tick(True, self.state), "an open hand sends nothing")
        for _ in range(3):
            self.sight(project(hand_world(), np.array([0.0, 0.0, 0.5])))
        first = self.pilot.tick(True, self.state)
        self.assertIsNotNone(first)
        start = self.tcp(first, "right")
        for _ in range(45):  # 5 cm to the operator's right: the raw image's left, camera -x
            self.sight(project(hand_world(), np.array([-0.05, 0.0, 0.5])))
            action = self.pilot.tick(True, self.state)
        moved = self.tcp(action, "right") - start
        self.assertLess(moved[1], -0.035, f"the right arm moved to the robot's right: {moved}")
        self.assertLess(abs(moved[0]) + abs(moved[2]), 0.015, f"and only that way: {moved}")
        for name in arm_fields("left"):
            self.assertAlmostEqual(action[name], self.state[name], places=5, msg="the left arm holds")
        for _ in range(2):
            self.sight(project(hand_world(curled=False), np.array([-0.05, 0.0, 0.5])))
        self.assertIsNone(self.pilot.tick(True, self.state), "opening the hand stops sending")

    def test_nothing_is_sent_without_control_and_control_restarts_from_the_state(self):
        for _ in range(3):
            self.sight(project(hand_world(), np.array([0.0, 0.0, 0.5])))
        self.assertIsNone(self.pilot.tick(False, self.state))
        self.assertIsNone(self.pilot.tick(True, None), "no state yet: nothing to start from")
        moved = dict(self.state, **{"right_joint1.pos": 0.3})
        self.pilot.drop()
        action = self.pilot.tick(True, moved)
        self.assertAlmostEqual(action["right_joint1.pos"], 0.3, places=3, msg="starts where the arm is now")

    def test_a_jump_lets_go(self):
        for _ in range(3):
            self.sight(project(hand_world(), np.array([0.0, 0.0, 0.5])))
        self.assertIsNotNone(self.pilot.tick(True, self.state))
        self.sight(project(hand_world(), np.array([0.3, 0.0, 0.5])))  # 30 cm in one frame: another hand
        self.assertIsNone(self.pilot.tick(True, self.state))
        self.assertIn("jumped", " ".join(self.pilot.drain_events()))

    def test_a_stale_webcam_lets_go(self):
        for _ in range(3):
            self.sight(project(hand_world(), np.array([0.0, 0.0, 0.5])))
        self.assertIsNotNone(self.pilot.tick(True, self.state))
        self.pilot.see(Sighting(self.seq, self.now, np.zeros((480, 640, 3), np.uint8), []), self.now + 1.0)
        self.assertIsNone(self.pilot.tick(True, self.state))


if __name__ == "__main__":
    unittest.main()
