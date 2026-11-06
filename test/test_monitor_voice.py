"""The task monitor pausing Dream-1 and reporting to the voice agent, over a local LiveKit server.

    uv run --group voice python -m test.test_monitor_voice      (with `livekit-server --dev`)

A fake robot holds the control gate, a real Operator answers TaskControl's RPCs as dream-1-tasks, the voice agent's
own connection (`PolicyLink`) gives the spoken commands and takes the reports, and the real monitor watches as a
Portal observer. Only the vision model is a stub, whose scores make one run finish, one stall, and one get handed
to the teleoperator before a verdict.
"""
import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest import mock

from livekit.agents import Agent, ModelSettings, llm

from policies.dream_1_tasks.run import TaskControl
from task_monitor import FRAME_WIDTH, Limits, Monitor, TICK_HZ
from test._harness import Checks, RobotProcess, config_yaml, fresh_room
from test.fake_peers import FakeOperator
from livekit.portal import Observer, ObserverConfig
from utils.common import (DREAM_RESUME_RPC, DREAM_SET_TASK_RPC, DREAM_TASKS_IDENTITY, DREAM_TO_TELEOP_RPC,
                          MONITOR_IDENTITY, TELEOPERATOR_IDENTITY, HardwareThread, mint_token, pace)
from voice_agent import PolicyLink, VoiceControl, describe_report

BLUE, RED = "put the blue cube in the box", "put the red cube in the box"
LIMITS = Limits(success=0.85, hold_s=2.0, stuck_s=4.0, gain=0.05, still_s=60.0, every_s=0.5)


class StubScorer:
    """Scores by task: the blue cube goes well, the red one stalls."""

    def __init__(self) -> None:
        self.asked: list[tuple] = []
        self.blue = iter([0.2, 0.4, 0.6, 0.9, 0.95])

    def score(self, frames, fps, task):
        self.asked.append((frames.shape, round(fps, 2), task))
        time.sleep(0.1)  # a model takes a while
        return (next(self.blue, 0.95) if task == BLUE else 0.3), -1.0


async def until(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


def check_reports(checks: Checks) -> None:
    """The voice agent's side alone: what it says, and which verdicts it announces."""
    link, heard = PolicyLink(), []
    link.on_verdict = heard.append

    def take(**report) -> None:
        link._take_report(SimpleNamespace(payload=json.dumps(report)))

    checks.check("No task monitor" in link.progress(), "no report: no estimate", link.progress())
    take(state="success", run=1, task=BLUE, elapsed_s=40, progress=0.9, released=True, reason="done")
    take(state="watching", run=2, task=RED, elapsed_s=12.3, progress=0.42, trend="rising")
    checks.check("42 percent" in link.progress() and "rising" in link.progress(), "the LLM reads the estimate",
                 link.progress())
    take(state="stuck", run=2, task=RED, elapsed_s=60, progress=0.4, released=True,
         reason="no progress in 45 seconds")
    take(state="stuck", run=2, task=RED, elapsed_s=60, progress=0.4, released=True,
         reason="no progress in 45 seconds")  # the next report repeats it
    checks.check([report["run"] for report in heard] == [2],
                 "a verdict is announced once, and only on a run this agent watched", str(heard))
    checks.check("stuck" in link.progress() and "paused Dream-1" in link.progress(), "the LLM reads the verdict",
                 link.progress())
    take(state="watching", run=3, task=BLUE, elapsed_s=20, progress=0.9, trend="rising", done_for_s=4.2, hold_s=10)
    checks.check("looked done for 4 seconds" in link.progress() and "reaches 10 seconds" in link.progress(),
                 "the LLM reads the wait before the monitor pauses Dream-1", link.progress())


async def check_every_reply_sees_the_newest_report(checks: Checks) -> None:
    """Each LLM call gets the newest report with the instructions, not a number from earlier in the conversation."""
    link = PolicyLink()
    agent = VoiceControl(link, [BLUE, RED])
    seen: list[llm.ChatContext] = []

    async def fake_llm_node(_agent, chat_ctx, tools, model_settings):
        seen.append(chat_ctx)
        yield "ok"

    history = llm.ChatContext()
    history.add_message(role="system", content="instructions", id="lk.agent_task.instructions")
    history.add_message(role="user", content="how is it going?")
    history.add_message(role="assistant", content="About sixty-nine percent done.")
    history.add_message(role="user", content="and now?")
    with mock.patch.object(Agent.default, "llm_node", fake_llm_node):
        for progress in (0.69, 0.91):
            link._take_report(SimpleNamespace(payload=json.dumps(
                {"state": "watching", "run": 4, "task": BLUE, "elapsed_s": 30, "progress": progress})))
            async for _ in agent.llm_node(history, [], ModelSettings()):
                pass
    messages, _ = seen[-1].to_provider_format("openai")
    system = [m for m in messages if m["role"] == "system"]
    checks.check(len(system) == 1 and "Task monitor, now:" in str(system[0]["content"])
                 and "91 percent" in str(system[0]["content"]) and "69 percent" not in str(system[0]["content"]),
                 "every reply gets the newest report, folded into the one system prompt", str(system)[-160:])
    checks.check(len(history.items) == 4 and "69 percent" in str(seen[0].items[1].content),
                 "the stored history keeps no report; the earlier reply had the report of its time")


async def main() -> int:
    checks = Checks("Task monitor and voice agent")
    check_reports(checks)
    await check_every_reply_sees_the_newest_report(checks)
    room = fresh_room("monitor")
    os.environ["LIVEKIT_ROOM"] = room
    url = os.environ["LIVEKIT_URL"]
    policy = FakeOperator(room, url, identity=DREAM_TASKS_IDENTITY)
    human = FakeOperator(room, url, identity=TELEOPERATOR_IDENTITY)
    tasks = TaskControl(policy.portal, [BLUE, RED])
    tasks.register_rpcs()
    link = PolicyLink()
    verdicts: list[dict] = []
    link.on_verdict = verdicts.append
    obs = Observer(ObserverConfig.from_yaml_str(config_yaml(), room))
    scorer, worker = StubScorer(), HardwareThread("monitor-test")
    monitor = Monitor(obs, scorer, worker, LIMITS, "overhead_camera", dry_run=False)

    async def loop() -> None:
        async for _ in pace(TICK_HZ):
            await monitor.tick(time.monotonic())

    def state() -> str | None:
        return (link.report or {}).get("state")

    with RobotProcess(room, ticks=30 * 90):
        ticking = None
        try:
            await link.connect()
            await policy.connect()
            await human.connect()
            await obs.connect(url, mint_token(MONITOR_IDENTITY, room))
            ticking = asyncio.ensure_future(loop())
            checks.check(await until(lambda: state() == "idle", 8), "the voice agent hears an idle monitor",
                         link.progress())

            await link.call(DREAM_RESUME_RPC)  # "continue", on the blue cube
            checks.check(await until(lambda: state() == "watching", 5), "Dream-1 drives: the monitor watches",
                         link.progress())
            checks.check(await until(lambda: (link.report or {}).get("progress") is not None, 8),
                         "the voice agent can say how it is going", link.progress())
            checks.check(await until(lambda: state() == "success", 15), "the blue cube is judged done",
                         link.progress())
            await asyncio.sleep(0.3)  # the robot's control attribute reaches the other peers
            checks.check(human.portal.active_operator() is None and not tasks.driving(),
                         "the monitor took control: the arms hold", str(tasks.status()))
            checks.check(len(verdicts) == 1 and verdicts[0]["task"] == BLUE and verdicts[0]["released"],
                         "the voice agent is told once", str(verdicts))
            shape, fps, task = scorer.asked[-1]
            checks.check(shape[2:] == (FRAME_WIDTH, 3) and task == BLUE,
                         "the model sees the overhead camera with Dream-1's task", f"{shape} at {fps} fps")

            await link.call(DREAM_SET_TASK_RPC, task=RED)
            await link.call(DREAM_RESUME_RPC)  # "the red cube instead", "continue"
            checks.check(await until(lambda: state() == "watching" and link.report["task"] == RED, 5),
                         "a new run on the new task")
            checks.check(await until(lambda: state() == "stuck", LIMITS.stuck_s + 10), "the red cube stalls: stuck",
                         link.progress())
            await asyncio.sleep(0.3)
            checks.check(human.portal.active_operator() is None and len(verdicts) == 2
                         and verdicts[1]["state"] == "stuck", "stuck takes control and tells the voice agent",
                         str(verdicts[-1]))

            await link.call(DREAM_RESUME_RPC)
            checks.check(await until(lambda: state() == "watching" and link.report["run"] == 3, 5), "a third run")
            await link.call(DREAM_TO_TELEOP_RPC)  # "teleop", before any verdict
            checks.check(await until(lambda: state() == "stopped", 5), "a handover ends the run without a verdict",
                         link.progress())
            await asyncio.sleep(LIMITS.stuck_s + 2)
            checks.check(human.portal.active_operator() == TELEOPERATOR_IDENTITY and len(verdicts) == 2,
                         "the monitor leaves the teleoperator in control")
            checks.check("stopped" in link.progress() and RED in link.progress(), "and the voice agent can say so",
                         link.progress())
            checks.check("No task monitor" in describe_report(link.report, age_s=60),
                         "an old report reads as no monitor")
        finally:
            if ticking is not None:
                ticking.cancel()
                await asyncio.gather(ticking, return_exceptions=True)
            await monitor.close()
            worker.close()
            await obs.disconnect()
            obs.close()
            await link.close()
            await human.close()
            await policy.close()
    return checks.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
