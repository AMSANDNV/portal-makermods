"""The task-switching Dream-1's task and control rules; no robot, network, or API key.

    uv run python -m unittest test.test_dream1_tasks
"""
import os
import unittest
from unittest import mock

from policies.dream_1_tasks.run import MAX_TASK_CHARS, TaskControl, session_tasks
from utils.common import DREAM_TASKS_IDENTITY, HAND_TELEOP_IDENTITY, TELEOPERATOR_IDENTITY

ME = DREAM_TASKS_IDENTITY
BLUE, RED = "put the blue cube in the box", "put the red cube in the box"


class FakeOperator:
    """The Portal calls `TaskControl` makes, against a control gate held in memory."""

    def __init__(self, active=None, others=(TELEOPERATOR_IDENTITY,)) -> None:
        self.active, self.others, self.refuse = active, list(others), False

    def local_identity(self):
        return ME

    def operators(self):
        return self.others

    def active_operator(self):
        return self.active

    async def set_active_operator(self, identity) -> None:
        if self.refuse:
            raise RuntimeError("the robot did not answer")
        self.active = identity


class TaskControlTests(unittest.IsolatedAsyncioTestCase):
    def control(self, active=None, **op) -> TaskControl:
        self.op = FakeOperator(active, **op)
        return TaskControl(self.op, [BLUE, RED])

    async def test_a_new_task_while_driving_pauses_first(self):
        tasks = self.control(active=ME)
        status = await tasks.set_task(RED, "voice-agent")
        self.assertIsNone(self.op.active)  # released: the arms hold
        self.assertEqual((status["task"], status["driving"]), (RED, False))
        self.assertTrue(tasks.changed)  # the next drive starts a new episode

    async def test_a_new_task_never_starts_motion(self):
        tasks = self.control(active=None)
        await tasks.set_task(RED, "voice-agent")
        self.assertIsNone(self.op.active)

    async def test_a_new_task_leaves_the_human_in_control(self):
        tasks = self.control(active="teleoperator")
        status = await tasks.set_task(RED, "voice-agent")
        self.assertEqual((self.op.active, status["active_operator"]), ("teleoperator", "teleoperator"))

    async def test_resume_takes_control_and_pause_gives_it_up(self):
        tasks = self.control(active="teleoperator")
        self.assertTrue((await tasks.resume("voice-agent"))["driving"])
        self.assertEqual(self.op.active, ME)
        self.assertFalse((await tasks.pause("voice-agent"))["driving"])
        self.assertIsNone(self.op.active)

    async def test_pause_leaves_another_operator_alone(self):
        tasks = self.control(active="teleoperator")
        await tasks.pause("voice-agent")
        self.assertEqual(self.op.active, "teleoperator")

    async def test_teleop_takes_over_from_dream1_and_gives_back(self):
        tasks = self.control(active=ME)
        status = await tasks.to_teleop("voice-agent")
        self.assertEqual((self.op.active, status["driving"]), (TELEOPERATOR_IDENTITY, False))
        self.assertTrue((await tasks.resume("voice-agent"))["driving"])
        self.assertEqual(self.op.active, ME)

    async def test_teleop_must_be_connected(self):
        tasks = self.control(active=ME, others=())
        with self.assertRaises(ValueError):
            await tasks.to_teleop("voice-agent")
        self.assertEqual(self.op.active, ME)  # Dream-1 keeps driving rather than leave nobody

    async def test_hand_teleop_takes_over_from_dream1_and_gives_back(self):
        tasks = self.control(active=ME, others=(TELEOPERATOR_IDENTITY, HAND_TELEOP_IDENTITY))
        status = await tasks.to_hand_teleop("voice-agent")
        self.assertEqual((self.op.active, status["active_operator"], status["driving"]),
                         (HAND_TELEOP_IDENTITY, HAND_TELEOP_IDENTITY, False))
        self.assertTrue((await tasks.resume("voice-agent"))["driving"])
        self.assertEqual(self.op.active, ME)

    async def test_hand_teleop_takes_over_from_the_leader_arms(self):
        tasks = self.control(active=TELEOPERATOR_IDENTITY, others=(TELEOPERATOR_IDENTITY, HAND_TELEOP_IDENTITY))
        await tasks.to_hand_teleop("voice-agent")
        self.assertEqual(self.op.active, HAND_TELEOP_IDENTITY)

    async def test_hand_teleop_must_be_connected(self):
        tasks = self.control(active=ME)  # only the leader-arm teleoperator is connected
        with self.assertRaisesRegex(ValueError, "hand-teleop"):
            await tasks.to_hand_teleop("voice-agent")
        self.assertEqual(self.op.active, ME)  # Dream-1 keeps driving rather than leave nobody

    async def test_hand_teleop_identity_follows_its_setting(self):
        with mock.patch.dict(os.environ, {"HAND_TELEOP_IDENTITY": "hands-2"}):
            tasks = self.control(active=ME, others=("hands-2",))
        status = await tasks.to_hand_teleop("voice-agent")
        self.assertEqual((self.op.active, status["hand_teleop"], status["hand_teleop_connected"]),
                         ("hands-2", "hands-2", True))

    async def test_stopping_the_hand_teleop_leaves_nobody_driving(self):
        tasks = self.control(active=HAND_TELEOP_IDENTITY, others=(TELEOPERATOR_IDENTITY, HAND_TELEOP_IDENTITY))
        status = await tasks.stop_hand_teleop("voice-agent")
        self.assertEqual((self.op.active, status["active_operator"], status["driving"]), (None, None, False))

    async def test_stopping_the_hand_teleop_leaves_everyone_else_alone(self):
        for active in (ME, TELEOPERATOR_IDENTITY, None):
            tasks = self.control(active=active, others=(TELEOPERATOR_IDENTITY, HAND_TELEOP_IDENTITY))
            status = await tasks.stop_hand_teleop("voice-agent")
            self.assertEqual((self.op.active, status["active_operator"]), (active, active))

    async def test_task_text_is_tidied_and_bounded(self):
        tasks = self.control()
        self.assertEqual((await tasks.set_task("  put the red\n cube  in the box ", "t"))["task"], RED)
        for bad in ("", "   ", "x" * (MAX_TASK_CHARS + 1)):
            with self.assertRaises(ValueError):
                await tasks.set_task(bad, "t")
        self.assertEqual(tasks.task, RED)

    async def test_a_failed_pause_keeps_the_task(self):
        tasks = self.control(active=ME)
        self.op.refuse = True
        with self.assertRaises(RuntimeError):
            await tasks.set_task(RED, "voice-agent")
        self.assertEqual((tasks.task, tasks.changed, self.op.active), (BLUE, False, ME))

    async def test_the_same_task_is_not_a_new_episode(self):
        tasks = self.control()
        await tasks.set_task(BLUE, "voice-agent")
        self.assertFalse(tasks.changed)

    async def test_no_task_never_takes_control(self):
        self.op = FakeOperator(active=None)
        tasks = TaskControl(self.op, [])
        self.assertEqual((tasks.status()["task"], tasks.status()["presets"]), (None, []))
        with self.assertRaises(ValueError):
            await tasks.resume("voice-agent")
        self.assertIsNone(self.op.active)
        status = await tasks.set_task(RED, "voice-agent")  # a task sets it, and still does not start motion
        self.assertEqual((status["task"], self.op.active), (RED, None))
        await tasks.resume("voice-agent")
        self.assertEqual(self.op.active, ME)

    def test_status_lists_the_presets(self):
        status = self.control().status()
        self.assertEqual(status, {"task": BLUE, "presets": [BLUE, RED], "driving": False, "active_operator": None,
                                  "teleoperator_connected": True, "hand_teleop": HAND_TELEOP_IDENTITY,
                                  "hand_teleop_connected": False})


class SessionTaskTests(unittest.TestCase):
    def test_flags_then_env_then_none(self):
        with mock.patch.dict(os.environ, {"PORTAL_DATA_TASKS": f"{BLUE}; {RED}", "PORTAL_DATA_TASK": "other"}):
            self.assertEqual(session_tasks(["fold the towel"]), ["fold the towel"])
            self.assertEqual(session_tasks(None), [BLUE, RED])
        with mock.patch.dict(os.environ, {"PORTAL_DATA_TASKS": "", "PORTAL_DATA_TASK": "other"}):
            self.assertEqual(session_tasks(None), ["other"])
        with mock.patch.dict(os.environ, {"PORTAL_DATA_TASKS": "", "PORTAL_DATA_TASK": ""}):
            self.assertEqual(session_tasks(None), [])

    def test_at_most_nine(self):
        self.assertEqual(len(session_tasks([str(n) for n in range(12)])), 9)


if __name__ == "__main__":
    unittest.main()
