"""The task monitor's clip, verdicts, and control; no robot, network, or model.

    uv run python -m unittest test.test_task_monitor
"""
import asyncio
import json
import unittest
from types import SimpleNamespace

import numpy as np

from task_monitor import CLIP_CAP, FRAME_WIDTH, MIN_FRAMES, Clip, Limits, Monitor, Run, pose
from utils.common import DREAM_STATUS_RPC, DREAM_TASKS_IDENTITY, MONITOR_REPORT_RPC, TELEOPERATOR_IDENTITY
from utils.metal import SIDES, arm_fields

BLUE, RED = "put the blue cube in the box", "put the red cube in the box"
LIMITS = Limits(success=0.85, hold_s=6.0, stuck_s=20.0, gain=0.05, still_s=10.0, every_s=1.0)
FIELDS = [name for side in SIDES for name in arm_fields(side)]


class FakeObserver:
    """The Portal calls the monitor makes: a control gate, the newest frame and state, and RPCs, in memory."""

    def __init__(self) -> None:
        self.active, self.task, self.moving = None, BLUE, True
        self.reports, self.gate = [], []
        self.voice_agent, self.dream = True, True
        self.ticks = 0
        self.frame = SimpleNamespace(data=bytes(64 * 48 * 3), width=64, height=48)

    def active_operator(self):
        return self.active

    async def set_active_operator(self, identity) -> None:
        self.gate.append(identity)
        self.active = identity

    def get_video_frame(self, camera):
        return self.frame

    def get_state(self):
        self.ticks += 1
        wave = 0.1 * np.sin(self.ticks) if self.moving else 0.0
        return SimpleNamespace(values={name: wave for name in FIELDS})

    async def perform_rpc(self, method, payload="", destination=None, response_timeout_ms=None):
        if method == DREAM_STATUS_RPC and self.dream:
            return json.dumps({"task": self.task, "driving": self.active == destination})
        if method == MONITOR_REPORT_RPC and self.voice_agent:
            self.reports.append(json.loads(payload))
            return "ok"
        raise RuntimeError(f"no one answers {method}")


class FakeScorer:
    """Scores from a script, then its last score forever; remembers what it was asked."""

    def __init__(self, scores) -> None:
        self.scores, self.asked = list(scores), []

    def score(self, frames, fps, task):
        self.asked.append((frames.shape, fps, task))
        return (self.scores.pop(0) if len(self.scores) > 1 else self.scores[0]), -1.0


class InlineWorker:
    async def run(self, fn, *args):
        return fn(*args)


class ClipTests(unittest.TestCase):
    def test_spans_the_whole_run_in_bounded_memory(self):
        clip = Clip()
        for tick in range(10 * 300):  # five minutes at 10 Hz
            t = tick / 10
            if clip.wants(t):
                clip.add(t, np.full((2, 2, 3), tick % 256, np.uint8))
        self.assertLess(len(clip), CLIP_CAP)
        self.assertEqual(clip.times[0], 0.0)
        self.assertGreater(clip.times[-1], 280.0)
        gaps = np.diff(clip.times)
        self.assertLess(gaps.max() - gaps.min(), 1e-6)  # evenly spaced

    def test_a_sample_runs_from_the_start_to_the_newest_frame(self):
        clip = Clip()
        for second in range(30):
            clip.add(float(second), np.full((2, 2, 3), second, np.uint8))
        frames, fps = clip.sample(16)
        self.assertEqual(frames.shape, (16, 2, 2, 3))
        self.assertEqual((frames[0, 0, 0, 0], frames[-1, 0, 0, 0]), (0, 29))
        self.assertAlmostEqual(fps, 15 / 29)
        few, _ = clip.sample(64)
        self.assertEqual(len(few), 30)


class VerdictTests(unittest.TestCase):
    def run_with(self, scores, every=2.0) -> tuple[Run, list]:
        run = Run(1, DREAM_TASKS_IDENTITY, BLUE, 0.0, LIMITS)
        verdicts = [run.add_score(i * every, p) for i, p in enumerate(scores, start=1)]
        return run, verdicts

    def test_done_waits_until_the_estimate_has_stayed_high(self):
        run, verdicts = self.run_with([0.2, 0.9, 0.95, 0.3, 0.9, 0.9, 0.95, 0.9])  # a score every 2 s
        # The dip at 8 s starts the wait again: high from 10 s, done at 16 s, LIMITS.hold_s later.
        self.assertEqual(verdicts, [None] * 7 + ["success"])
        self.assertIn("done", run.reason)
        self.assertIsNone(run.report(16.0)["done_for_s"])  # decided: no longer waiting

    def test_the_wait_shows_in_the_report(self):
        run, _ = self.run_with([0.5, 0.9, 0.92])
        report = run.report(6.0)
        self.assertEqual((report["state"], report["progress"], report["done_for_s"], report["hold_s"]),
                         ("watching", 0.92, 2.0, LIMITS.hold_s))

    def test_progress_is_the_newest_score(self):
        run, _ = self.run_with([0.4, 0.55, 0.69, 0.86])
        self.assertEqual(run.report(8.0)["progress"], 0.86)  # what the log prints, not the median

    def test_an_estimate_that_stops_rising_is_stuck(self):
        run, verdicts = self.run_with([0.2, 0.3] + [0.32] * 20)
        first = verdicts.index("stuck")
        self.assertEqual((first + 1) * 2.0 - run.rose_at, LIMITS.stuck_s)  # stuck_s after it last rose
        self.assertLessEqual(run.rose_at, 3 * 2.0)
        self.assertIn("no progress", run.reason)
        self.assertEqual([v for v in verdicts if v], ["stuck"])  # decided once

    def test_a_slowly_rising_estimate_is_not_stuck(self):
        _, verdicts = self.run_with([0.1 + 0.01 * i for i in range(60)])
        self.assertEqual(set(verdicts), {None})

    def test_still_arms_are_stuck_and_moving_ones_are_not(self):
        still = Run(1, DREAM_TASKS_IDENTITY, BLUE, 0.0, LIMITS)
        moving = Run(2, DREAM_TASKS_IDENTITY, BLUE, 0.0, LIMITS)
        rest = np.zeros(14)
        verdicts = [still.add_pose(t / 4, rest + 0.001 * (t % 2)) for t in range(80)]
        self.assertEqual(verdicts.index("stuck") / 4, LIMITS.still_s)
        self.assertEqual({moving.add_pose(t / 4, rest + 0.05 * (t % 2)) for t in range(80)}, {None})

    def test_still_arms_that_look_done_are_finishing(self):
        run = Run(1, DREAM_TASKS_IDENTITY, BLUE, 0.0, LIMITS)
        run.add_score(1.0, 0.9)  # done, waiting out the hold
        self.assertEqual({run.add_pose(t / 4, np.zeros(14)) for t in range(80)}, {None})

    def test_trend(self):
        run, _ = self.run_with([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], every=2.0)
        self.assertEqual(run.trend(), "rising")
        run, _ = self.run_with([0.5] * 8)
        self.assertEqual(run.trend(), "flat")

    def test_pose_reads_both_arms(self):
        state = {name: float(i) for i, name in enumerate(FIELDS)}
        self.assertEqual(pose(state).shape, (14,))


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    def monitor(self, scores, dry_run=False) -> Monitor:
        self.obs, self.scorer = FakeObserver(), FakeScorer(scores)
        self.now = 0.0
        return Monitor(self.obs, self.scorer, InlineWorker(), LIMITS, "overhead_camera", dry_run)

    async def advance(self, monitor: Monitor, seconds: float) -> None:
        for _ in range(int(seconds * 4)):
            self.now += 0.25
            await monitor.tick(self.now)
            for _ in range(3):  # let the score and the report run
                await asyncio.sleep(0)

    async def test_watches_only_dream1_with_its_own_task(self):
        monitor = self.monitor([0.3])
        self.obs.active = TELEOPERATOR_IDENTITY
        await self.advance(monitor, 5)
        self.assertIsNone(monitor.run)
        self.assertEqual(self.obs.reports[-1]["state"], "idle")
        self.obs.active, self.obs.task = DREAM_TASKS_IDENTITY, RED
        await self.advance(monitor, 5)
        self.assertEqual((monitor.run.task, self.obs.reports[-1]["state"]), (RED, "watching"))
        shape, _, task = self.scorer.asked[-1]
        self.assertEqual((shape[0] >= MIN_FRAMES, shape[2], shape[3], task), (True, FRAME_WIDTH, 3, RED))

    async def test_done_takes_control_and_tells_the_voice_agent(self):
        monitor = self.monitor([0.2, 0.5, 0.9, 0.95])
        self.obs.active = DREAM_TASKS_IDENTITY
        await self.advance(monitor, 5)
        self.assertEqual((self.obs.gate, self.obs.reports[-1]["state"]), ([], "watching"))  # looks done: waits
        self.assertIsNotNone(self.obs.reports[-1]["done_for_s"])
        await self.advance(monitor, LIMITS.hold_s + 2)
        self.assertEqual(self.obs.gate, [None])  # released once: the arms hold
        verdict = next(r for r in self.obs.reports if r["state"] == "success")
        self.assertEqual((verdict["task"], verdict["released"], verdict["run"]), (BLUE, True, 1))
        self.assertGreaterEqual(verdict["progress"], LIMITS.success)
        self.assertTrue(all(r["released"] for r in self.obs.reports if r["state"] == "success"))  # news once done
        await self.advance(monitor, 10)
        self.assertEqual(self.obs.reports[-1]["state"], "success")  # still the newest news while nobody drives

        self.obs.active = DREAM_TASKS_IDENTITY  # "continue": a new run
        await self.advance(monitor, 2)
        self.assertEqual((monitor.run.number, self.obs.reports[-1]["state"]), (2, "watching"))

    async def test_stuck_takes_control(self):
        monitor = self.monitor([0.3])
        self.obs.active = DREAM_TASKS_IDENTITY
        await self.advance(monitor, LIMITS.stuck_s + 5)
        self.assertEqual((self.obs.gate, monitor.run.verdict), ([None], "stuck"))
        self.assertTrue(self.obs.reports[-1]["released"])

    async def test_still_arms_take_control(self):
        monitor = self.monitor([0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
        self.obs.active, self.obs.moving = DREAM_TASKS_IDENTITY, False
        await self.advance(monitor, LIMITS.still_s + 2)
        self.assertEqual((self.obs.gate, monitor.run.verdict), ([None], "stuck"))
        self.assertIn("not moved", monitor.run.reason)

    async def test_a_dry_run_reports_but_never_takes_control(self):
        monitor = self.monitor([0.9], dry_run=True)
        self.obs.active = DREAM_TASKS_IDENTITY
        await self.advance(monitor, 10)
        self.assertEqual((self.obs.gate, self.obs.active), ([], DREAM_TASKS_IDENTITY))
        report = self.obs.reports[-1]
        self.assertEqual((report["state"], report["released"], report["dry_run"]), ("success", False, True))
        asked = len(self.scorer.asked)
        await self.advance(monitor, 5)
        self.assertGreater(len(self.scorer.asked), asked)  # keeps scoring, for calibration
        self.assertEqual(monitor.runs, 1)

    async def test_a_handover_ends_the_run_without_a_verdict(self):
        monitor = self.monitor([0.3, 0.4])
        self.obs.active = DREAM_TASKS_IDENTITY
        await self.advance(monitor, 5)
        self.obs.active = TELEOPERATOR_IDENTITY  # "teleop"
        await self.advance(monitor, 30)
        self.assertEqual((self.obs.gate, self.obs.active), ([], TELEOPERATOR_IDENTITY))
        self.assertEqual((self.obs.reports[-1]["state"], monitor.run.verdict), ("stopped", None))

    async def test_no_voice_agent_and_no_task_yet(self):
        monitor = self.monitor([0.9])
        self.obs.voice_agent, self.obs.dream = False, False
        self.obs.active = DREAM_TASKS_IDENTITY
        await self.advance(monitor, 5)
        self.assertIsNone(monitor.run)  # cannot read the task: waits rather than guess
        self.obs.dream = True
        await self.advance(monitor, 10)
        self.assertEqual((self.obs.gate, monitor.run.verdict, monitor.heard), ([None], "success", False))


if __name__ == "__main__":
    unittest.main()
