"""Offline tests for the visual scene planner's reply parsing; no network or hardware."""
import unittest

import scene_planner


class ExtractTasksTests(unittest.TestCase):
    def test_plain_json(self):
        reasoning, tasks = scene_planner.extract_tasks(
            '{"reasoning": "tortilla and cheese on the table", '
            '"tasks": ["pick up the tortilla", "place the cheese on it", "fold it"]}'
        )
        self.assertEqual(reasoning, "tortilla and cheese on the table")
        self.assertEqual(tasks, ["pick up the tortilla", "place the cheese on it", "fold it"])

    def test_reasoning_block_and_prose_are_stripped(self):
        reply = (
            "<think>The user wants a quesadilla. I see a tortilla.</think>\n"
            'Here is the plan:\n{"reasoning": "one tortilla", "tasks": ["lay the tortilla flat", "add cheese"]}'
        )
        reasoning, tasks = scene_planner.extract_tasks(reply)
        self.assertEqual(reasoning, "one tortilla")
        self.assertEqual(tasks, ["lay the tortilla flat", "add cheese"])

    def test_tasks_are_capped_and_whitespace_normalized(self):
        many = [f"step number {i}" for i in range(20)]
        import json
        _, tasks = scene_planner.extract_tasks(json.dumps({"reasoning": "", "tasks": ["  do   the\n thing  ", *many]}))
        self.assertEqual(len(tasks), scene_planner.MAX_TASKS)
        self.assertEqual(tasks[0], "do the thing")

    def test_long_task_is_truncated(self):
        import json
        _, tasks = scene_planner.extract_tasks(json.dumps({"reasoning": "", "tasks": ["x" * 500]}))
        self.assertEqual(len(tasks[0]), scene_planner.MAX_TASK_CHARS)

    def test_unparseable_reply_raises(self):
        with self.assertRaises(SystemExit):
            scene_planner.extract_tasks("I cannot help with that.")

    def test_empty_task_list_raises(self):
        with self.assertRaises(SystemExit):
            scene_planner.extract_tasks('{"reasoning": "nothing", "tasks": []}')


if __name__ == "__main__":
    unittest.main()
