"""The voice agent's RPCs to the task-switching Dream-1, over a local LiveKit server.

    uv run --group voice python -m test.test_voice_tasks      (with `livekit-server --dev`)

The voice agent's own connection (`PolicyLink`) calls `TaskControl`'s RPCs on a real Operator, with a fake
robot holding the control gate and a fake teleoperator in the room: the spoken flow "put the blue cube in the
box", "continue", "wait, the red cube instead", "continue", "teleop", "back to Dream-1", "stop", "continue",
"home the arms", "continue", "park the arms", without the speech, the LLM, or Dream-1 inference in between.
"""
import asyncio
import os

from livekit.agents import ToolError

from policies.dream_1_tasks.run import TaskControl
from test._harness import Checks, RobotProcess, fresh_room
from test.fake_peers import FakeOperator
from utils.common import (DREAM_PAUSE_RPC, DREAM_RESUME_RPC, DREAM_SET_TASK_RPC, DREAM_STATUS_RPC,
                          DREAM_TASKS_IDENTITY, DREAM_TO_TELEOP_RPC, TELEOPERATOR_IDENTITY)
from voice_agent import STOP_WORDS, PolicyLink, describe

BLUE, RED = "put the blue cube in the box", "put the red cube in the box"


def check_words(checks: Checks) -> None:
    for heard in ("wait", "Stop!", "wait stop, put the red cube instead", "hold on a second", "pause it"):
        checks.check(bool(STOP_WORDS.search(heard)), f"{heard!r} pauses at once")
    for heard in ("continue", "put the red cube in the box", "stopwatch", "Dream-1 paused", "go ahead"):
        checks.check(not STOP_WORDS.search(heard), f"{heard!r} does not")
    paused = describe({"task": RED, "presets": [BLUE, RED], "driving": False, "active_operator": None,
                       "teleoperator_connected": True})
    checks.check("paused" in paused and "2. put the red cube" in paused, "the LLM reads the task, state and presets",
                 paused)


async def main() -> int:
    checks = Checks("Voice tasks")
    check_words(checks)
    room = fresh_room("voice")
    os.environ["LIVEKIT_ROOM"] = room
    url = os.environ["LIVEKIT_URL"]
    policy = FakeOperator(room, url, identity=DREAM_TASKS_IDENTITY)
    human = FakeOperator(room, url, identity=TELEOPERATOR_IDENTITY)
    tasks = TaskControl(policy.portal, [BLUE, RED])
    tasks.register_rpcs()
    link = PolicyLink()

    async def call(method: str, **payload) -> dict:
        status = await link.call(method, **payload)
        await asyncio.sleep(0.2)  # the robot's control attribute reaches the other peers
        return status

    with RobotProcess(room, ticks=30 * 30):
        try:
            await link.connect()
            try:
                await link.call(DREAM_STATUS_RPC)
                checks.check(False, "a missing Dream-1 is reported")
            except ToolError as error:
                checks.check("dream-1-tasks" in str(error), "a missing Dream-1 is reported", str(error))

            await policy.connect()
            await asyncio.sleep(1)
            try:
                await link.call(DREAM_TO_TELEOP_RPC)
                checks.check(False, "a missing teleoperator is reported")
            except ToolError as error:
                checks.check("teleoperator" in str(error), "a missing teleoperator is reported", str(error))
            await human.connect()
            await asyncio.sleep(1)

            status = await call(DREAM_STATUS_RPC)
            checks.check(status["task"] == BLUE and not status["driving"] and status["teleoperator_connected"],
                         "starts on the first preset, not driving, teleop connected", str(status))

            status = await call(DREAM_RESUME_RPC)
            checks.check(status["driving"] and human.portal.active_operator() == DREAM_TASKS_IDENTITY,
                         "'continue' hands control to Dream-1", str(status))

            status = await call(DREAM_SET_TASK_RPC, task=RED)
            checks.check(status["task"] == RED and not status["driving"], "a new task pauses Dream-1", str(status))
            checks.check(human.portal.active_operator() is None, "the robot holds: no operator drives")
            checks.check(tasks.changed, "the next drive is a new episode")

            status = await call(DREAM_RESUME_RPC)
            checks.check(status["driving"] and status["task"] == RED, "'continue' resumes on the new task",
                         str(status))

            status = await call(DREAM_TO_TELEOP_RPC)
            checks.check(status["active_operator"] == TELEOPERATOR_IDENTITY and not status["driving"]
                         and human.portal.active_operator() == TELEOPERATOR_IDENTITY,
                         "'teleop' hands control to the leader arms", str(status))

            status = await call(DREAM_PAUSE_RPC)
            checks.check(human.portal.active_operator() == TELEOPERATOR_IDENTITY,
                         "'stop' leaves the teleoperator in control", str(status))

            status = await call(DREAM_RESUME_RPC)
            checks.check(status["driving"] and human.portal.active_operator() == DREAM_TASKS_IDENTITY,
                         "'back to Dream-1' takes control from the teleoperator", str(status))

            status = await call(DREAM_PAUSE_RPC)
            checks.check(not status["driving"] and human.portal.active_operator() is None, "'stop' pauses Dream-1",
                         str(status))

            try:
                await link.call(DREAM_SET_TASK_RPC, task=" ")
                checks.check(False, "an empty task is refused")
            except ToolError as error:
                checks.check(tasks.task == RED, "an empty task is refused and the task kept", str(error))

            await call(DREAM_RESUME_RPC)
            reply = await link.home()
            await asyncio.sleep(0.3)  # the robot releases control on its next tick
            status = await call(DREAM_STATUS_RPC)
            checks.check(reply == "returning the arms home" and not status["driving"]
                         and human.portal.active_operator() is None,
                         "'home the arms' sends them home, and Dream-1 no longer drives", f"{reply!r}, {status}")
            status = await call(DREAM_RESUME_RPC)
            checks.check(status["driving"], "'continue' after a home hands the arms back to Dream-1", str(status))

            reply = await link.park()
            await asyncio.sleep(0.3)  # the robot releases control on its next tick
            status = await call(DREAM_STATUS_RPC)
            checks.check(reply == "parking the arms" and not status["driving"]
                         and human.portal.active_operator() is None,
                         "'park the arms' parks them, and Dream-1 no longer drives", f"{reply!r}, {status}")
            reply = await link.park()
            checks.check(reply == "already parked", "'park' again never powers parked arms up", repr(reply))
            reply = await link.home()
            checks.check(reply.startswith("parked"), "'home' on parked arms never powers them up", repr(reply))
        finally:
            await link.close()
            await human.close()
            await policy.close()
    return checks.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
