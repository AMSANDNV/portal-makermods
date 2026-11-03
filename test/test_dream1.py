"""Dream-1 observation and action mapping; no robot, network, or API key required.

    uv run --group dream python -m unittest test.test_dream1
"""
import base64
import importlib.util
import unittest

import cv2
import dream
import numpy as np
from livekit.portal import OperatorConfig

from _common import CONFIG_PATH, ROOT
from utils.metal import JOINT_LIMITS_RAD, arm_fields

_spec = importlib.util.spec_from_file_location("dream1_inference", ROOT / "src/policies/dream-1/inference.py")
dream1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dream1)


class Dream1Tests(unittest.TestCase):
    def test_portal_yaml_is_the_metal_pair(self):
        cfg = OperatorConfig.from_yaml_file(CONFIG_PATH, "test")
        fields = [name for side in ("left", "right") for name in arm_fields(side)]
        self.assertEqual(sorted(f.name for f in cfg.action_schema), sorted(fields))
        self.assertEqual(sorted(f.name for f in cfg.state_schema), sorted(fields))

    def test_embodiment_spans_are_contiguous(self):
        state = dream1.embodiment()["state"]
        self.assertEqual(state["right_arm"], [0, 6])
        self.assertEqual(state["left_eef_rot6"][1], 2 * (6 + 1 + 3 + 6))
        self.assertEqual(set(dream1.embodiment()["video"]), {"overhead", "wrist_l", "wrist_r"})

    def test_default_cameras_cover_every_role(self):
        cfg = OperatorConfig.from_yaml_file(CONFIG_PATH, "test")
        roles = dream1.camera_roles(dream1.DEFAULT_CAMERAS, cfg.video_tracks)
        self.assertEqual(roles, {"overhead": "overhead_camera", "wrist_l": "left_wrist_camera",
                                 "wrist_r": "right_wrist_camera"})
        for spec in ("top=overhead_camera", "wrist_r=missing", ""):
            with self.assertRaises(ValueError):
                dream1.camera_roles(spec, cfg.video_tracks)

    def test_frame_is_cropped_resized_and_bgr(self):
        rgb = np.zeros((480, 640, 3), np.uint8)
        rgb[:60] = 255  # outside the 16:9 crop
        rgb[60:420, :, 0] = 255  # red inside it
        jpeg = np.frombuffer(base64.b64decode(dream1.encode_frame(rgb)), np.uint8)
        bgr = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
        self.assertEqual(bgr.shape, (176, 320, 3))
        self.assertGreater(bgr[88, 160, 2], 200)  # red stays red
        self.assertLess(bgr[2, 160, 1], 50)  # the white band was cropped away

    def test_units_round_trip(self):
        left = [0.1, -1.2, 1.5, 0.2, -0.3, 0.4, 40.0]
        right = [-0.1, -0.8, 1.0, -0.2, 0.3, -0.4, 80.0]
        state = dict(zip(arm_fields("left"), left)) | dict(zip(arm_fields("right"), right))
        row = dream1.to_model(state)
        self.assertEqual(row.shape, (14,))
        self.assertAlmostEqual(row[6], 0.4)  # 40 mm of Dream's 100 mm grip scale
        self.assertAlmostEqual(row[13], 0.8)
        action = dream1.to_robot(row)
        for key, value in state.items():
            self.assertAlmostEqual(action[key], value, places=6)

    def test_to_robot_stays_inside_the_limits(self):
        action = dream1.to_robot(np.array([9.0] * 6 + [2.0] + [-9.0] * 6 + [-1.0]))
        for side in ("left", "right"):
            *joints, gripper = arm_fields(side)
            for name, (lo, hi) in zip(joints, JOINT_LIMITS_RAD):
                self.assertTrue(lo <= action[name] <= hi, name)
            self.assertTrue(0.0 <= action[gripper] <= 80.0)

    def test_step_toward_limits_each_entry(self):
        out = dream1.step_toward(np.zeros(3), np.array([1.0, -1.0, 1.0]), np.array([0.1, 0.1, 0.02]))
        np.testing.assert_allclose(out, [0.1, -0.1, 0.02])

    def test_chunk_rows_are_left_then_right(self):
        reply = {"q_l_chunk": np.zeros((48, 7)), "q_r_chunk": np.ones((48, 7))}
        rows = dream1.chunk_rows(reply)
        self.assertEqual(rows.shape, (48, 14))
        self.assertTrue((rows[:, :7] == 0).all() and (rows[:, 7:] == 1).all())
        for bad in (np.zeros((48, 6)), np.full((48, 7), np.nan), np.zeros((47, 7))):
            with self.assertRaises(dream.DreamError):
                dream1.chunk_rows({"q_l_chunk": np.zeros((48, 7)), "q_r_chunk": bad})


if __name__ == "__main__":
    unittest.main()
