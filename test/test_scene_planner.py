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
                             "present": ["tortilla", "cheese"], "missing": [],
                             "steps": [{"task": "lay the tortilla flat", "contact_xy": [250, 200]},
                                       {"task": "add the cheese", "contact_xy": [420, 160]}]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            reasoning, steps, present, missing = scene_planner.plan(np.zeros((360, 640, 3), np.uint8),
                                                                    "make a quesadilla")
        finally:
            scene_planner.astra = orig
        self.assertEqual(reasoning, "a tortilla and cheese")
        self.assertEqual(steps[0], {"task": "lay the tortilla flat", "contact_xy": [250, 200]})
        self.assertEqual(steps[1]["contact_xy"], [420, 160])
        self.assertEqual(present, ["tortilla", "cheese"])
        self.assertEqual(missing, [])

    def test_present_and_missing_ingredients(self):
        reply = self._reply({"reasoning": "only a tortilla", "present": ["tortilla"],
                             "missing": ["cheese", "griddle"],
                             "steps": [{"task": "fold the tortilla", "contact_xy": [1, 2]}]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            _, _, present, missing = scene_planner.plan(np.zeros((10, 10, 3), np.uint8), "make a quesadilla")
        finally:
            scene_planner.astra = orig
        self.assertEqual(present, ["tortilla"])
        self.assertEqual(missing, ["cheese", "griddle"])

    def test_missing_or_bad_contact_point_becomes_none(self):
        reply = self._reply({"reasoning": "", "steps": [{"task": "fold it"},
                                                        {"task": "cook it", "contact_xy": "nope"}]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            _, steps, _, _ = scene_planner.plan(np.zeros((10, 10, 3), np.uint8), "g")
        finally:
            scene_planner.astra = orig
        self.assertIsNone(steps[0]["contact_xy"])
        self.assertIsNone(steps[1]["contact_xy"])

    def test_steps_capped(self):
        reply = self._reply({"reasoning": "", "steps": [{"task": f"step {i}"} for i in range(20)]})
        orig = scene_planner.astra
        scene_planner.astra = lambda *a, **k: reply
        try:
            _, steps, _, _ = scene_planner.plan(np.zeros((10, 10, 3), np.uint8), "g")
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


class GroceriesTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._orig = scene_planner.GROCERIES_PATH
        self._tmp = Path(tempfile.mkdtemp()) / "groceries.json"
        scene_planner.GROCERIES_PATH = self._tmp

    def tearDown(self):
        scene_planner.GROCERIES_PATH = self._orig

    def test_missing_file_is_empty_list(self):
        self.assertEqual(scene_planner.load_groceries(), [])

    def test_add_dedupes_case_insensitively(self):
        self.assertEqual(scene_planner.add_groceries(["cheese", "Griddle"]), ["cheese", "Griddle"])
        self.assertEqual(scene_planner.add_groceries(["CHEESE", "salsa"]), ["cheese", "Griddle", "salsa"])
        self.assertEqual(scene_planner.load_groceries(), ["cheese", "Griddle", "salsa"])


class GateMissingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._orig = scene_planner.GROCERIES_PATH
        scene_planner.GROCERIES_PATH = Path(tempfile.mkdtemp()) / "groceries.json"

    def tearDown(self):
        scene_planner.GROCERIES_PATH = self._orig

    def test_auto_logs_to_groceries_and_proceeds(self):
        director = scene_planner.Director()
        self.assertTrue(director._gate_missing(["cheese"], auto=True))
        self.assertEqual(scene_planner.load_groceries(), ["cheese"])

    def test_proceed_choice_returns_true(self):
        import builtins
        director = scene_planner.Director()
        orig = builtins.input
        builtins.input = lambda *a, **k: "p"
        try:
            self.assertTrue(director._gate_missing(["cheese"], auto=False))
        finally:
            builtins.input = orig
        self.assertEqual(scene_planner.load_groceries(), [])  # 'p' does not add


class OverlayTests(unittest.TestCase):
    def test_overlay_marks_points_without_changing_shape(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        out = scene_planner.overlay_markers(frame, [[320, 180], None, [100, 100]])
        self.assertEqual(out.shape, frame.shape)
        self.assertGreater(int(out.sum()), 0)  # something was drawn


if __name__ == "__main__":
    unittest.main()
