"""Offline tests for the Astra director's reply parsing and overlay; no network or hardware."""
import json
import unittest

import numpy as np

import scene_planner


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        obj = scene_planner._extract_json('{"a": 1, "b": [2, 3]}')
        self.assertEqual(obj, {"a": 1, "b": [2, 3]})

    def test_think_block_and_prose_stripped(self):
        obj = scene_planner._extract_json('<think>planning...</think>\nHere:\n{"done": true, "reason": "ok"}')
        self.assertEqual(obj, {"done": True, "reason": "ok"})

    def test_unparseable_raises(self):
        with self.assertRaises(SystemExit):
            scene_planner._extract_json("I cannot help with that.")


class PlanParsingTests(unittest.TestCase):
    def _reply(self, obj):
        return json.dumps(obj)

    def test_steps_with_contact_points(self):
        reply = self._reply({"reasoning": "a tortilla and cheese",
                             "steps": [{"task": "lay the tortilla flat", "contact_xy": [250, 200]},
                                       {"task": "add the cheese", "contact_xy": [420, 160]}]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            reasoning, steps = scene_planner.plan(np.zeros((360, 640, 3), np.uint8), "make a quesadilla")
        finally:
            scene_planner.astra = orig
        self.assertEqual(reasoning, "a tortilla and cheese")
        self.assertEqual(steps[0], {"task": "lay the tortilla flat", "contact_xy": [250, 200]})
        self.assertEqual(steps[1]["contact_xy"], [420, 160])

    def test_missing_or_bad_contact_point_becomes_none(self):
        reply = self._reply({"reasoning": "", "steps": [{"task": "fold it"},
                                                        {"task": "cook it", "contact_xy": "nope"}]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            _, steps = scene_planner.plan(np.zeros((10, 10, 3), np.uint8), "g")
        finally:
            scene_planner.astra = orig
        self.assertIsNone(steps[0]["contact_xy"])
        self.assertIsNone(steps[1]["contact_xy"])

    def test_steps_capped(self):
        reply = self._reply({"reasoning": "", "steps": [{"task": f"step {i}"} for i in range(20)]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            _, steps = scene_planner.plan(np.zeros((10, 10, 3), np.uint8), "g")
        finally:
            scene_planner.astra = orig
        self.assertEqual(len(steps), scene_planner.MAX_TASKS)

    def test_empty_plan_raises(self):
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: '{"reasoning": "", "steps": []}'
        try:
            with self.assertRaises(SystemExit):
                scene_planner.plan(np.zeros((10, 10, 3), np.uint8), "g")
        finally:
            scene_planner.astra = orig


class VerifyTests(unittest.TestCase):
    def test_verify_parses_done(self):
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: '{"done": true, "reason": "tortilla is flat"}'
        try:
            done, reason = scene_planner.verify(np.zeros((10, 10, 3), np.uint8), "lay it flat", "g")
        finally:
            scene_planner.astra = orig
        self.assertTrue(done)
        self.assertEqual(reason, "tortilla is flat")


class OverlayTests(unittest.TestCase):
    def test_overlay_marks_points_without_changing_shape(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        out = scene_planner.overlay_markers(frame, [[320, 180], None, [100, 100]])
        self.assertEqual(out.shape, frame.shape)
        self.assertGreater(int(out.sum()), 0)  # something was drawn


if __name__ == "__main__":
    unittest.main()
