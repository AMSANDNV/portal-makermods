"""Robot side: the two Metal followers and three cameras behind a Portal `Robot`.

Run with `uv run robot`, with the followers resting: that pose is where they park. Each tick
reads both arms and the cameras, publishes them on one timestamp so operators match them into
one observation, and applies the active operator's latest action.

`p` (here, in teleop, or in a policy) parks: the arms walk back to the starting pose and switch
off, while state and video keep flowing. `p` again powers them up. Every exit, ctrl-c included,
parks first, so the next start finds the arms off at rest. A park that cannot finish (an arm
unreadable or blocked) leaves them holding.

`uv run robot --check-arms` prints the followers' joints with the motors off: move an arm to see
which link it is on.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import time
from typing import Iterator

from utils.common import (CONFIG_PATH, PARK_RPC, ROBOT_IDENTITY, HardwareThread, TickBudget, env_int, load_env,
                          mint_token, pace, required_env)
from livekit.portal import Action, Robot, RobotConfig
from utils.hotkeys import Hotkeys
from utils.metal import build_followers, check_arms


def diagnostics(followers, budget: TickBudget) -> Iterator[str]:
    """The 5 s console lines: achieved rate, read errors, speed cap."""
    if line := budget.report():
        yield line
    for side, arm in followers.arms.items():
        if arm.errors:
            yield f"{side} arm: {arm.errors} read errors so far"
    if clamped := followers.limiter.clamped:
        followers.limiter.clamped = 0
        yield f"speed cap: {clamped} joint commands ramped in 5s"


async def main() -> None:
    load_env()
    url, room = required_env("LIVEKIT_URL"), required_env("LIVEKIT_ROOM")
    cfg = RobotConfig.from_yaml_file(CONFIG_PATH, room)
    fps = env_int("PORTAL_FPS", cfg.fps)
    tracks = list(cfg.video_tracks)

    followers = build_followers(tracks, fps)
    followers.connect()  # opens both arms or neither
    robot = Robot(cfg)
    hardware = HardwareThread("followers")
    hotkeys = Hotkeys(global_tap=False)  # this terminal only; works over SSH
    latest_action: dict[str, float] = {}
    toggle_by: str | None = None  # who asked to park or power up

    def on_action(action: Action) -> None:
        nonlocal latest_action
        latest_action = {k: float(v) for k, v in action.values.items()}

    def on_active_operator_changed(identity: str | None) -> None:
        nonlocal latest_action
        latest_action = {}  # never replay the previous operator's last command
        print(f"[robot] active operator now: {identity}")

    def on_operator_left(identity: str) -> None:
        # Portal keeps control pinned to a departed operator for its reconnect; an arm should not wait.
        nonlocal latest_action
        if identity == robot.active_operator():
            print(f"[robot] active operator '{identity}' left; releasing")
            latest_action = {}
            asyncio.create_task(robot.set_active_operator(None))

    def on_park(data) -> str:
        nonlocal toggle_by
        toggle_by = data.caller_identity
        return "powering the arms up" if followers.parked else "parking the arms"

    async def toggle_park(who: str) -> None:
        if followers.parked:
            print(f"[robot] {who}: powering the arms up")
            step, done = followers.unpark, "arms powered up, holding where they rest"
        else:
            print(f"[robot] {who}: releasing control and parking ...")
            await robot.set_active_operator(None)
            step, done = functools.partial(followers.park, fps), "parked: arms off, still streaming; p powers them up"
        try:
            await hardware.run(step)
            print(f"[robot] {done}")
        except Exception as exc:
            print(f"[robot] {exc}; press p to try again")

    robot.register_rpc_method(PARK_RPC, on_park)
    robot.on_action(on_action)
    robot.on_active_operator_changed(on_active_operator_changed)
    robot.on_operator_left(on_operator_left)

    budget = TickBudget(fps)
    try:
        print(f"[robot] connecting to {url} as '{ROBOT_IDENTITY}' in room '{room}' ...")
        await robot.connect(url, mint_token(ROBOT_IDENTITY, room))
        hotkeys.start()
        print(f"[robot] connected; cameras={tracks}; {fps} fps; p parks or powers up, ctrl-c parks and stops")
        async for tick in pace(fps):
            if "p" in hotkeys.drain():
                toggle_by = "this terminal"
            if toggle_by:
                who, toggle_by, latest_action = toggle_by, None, {}
                await toggle_park(who)

            # One stamp for state and frames, so they match into one observation.
            ts_us = robot.now_us()
            started = time.perf_counter()
            state, frames = await hardware.run(followers.get_observation)
            device_ms = (time.perf_counter() - started) * 1e3
            if state:  # empty while an arm is unreadable: never invent a state, keep the video
                robot.send_state(state, timestamp_us=ts_us)
            for name, frame in frames.items():
                robot.send_video_frame(name, frame, timestamp_us=ts_us)
            if latest_action:
                applied = time.perf_counter()
                await hardware.run(followers.send_action, latest_action)
                device_ms += (time.perf_counter() - applied) * 1e3
            budget.record(device_ms, (time.perf_counter() - started) * 1e3)

            if tick and tick % (fps * 5) == 0:
                for line in diagnostics(followers, budget):
                    print(f"[robot] {line}")
    except (KeyboardInterrupt, asyncio.CancelledError):  # ctrl-c arrives as CancelledError
        print("\n[robot] stopping ...")
    finally:
        hotkeys.stop()
        if not followers.parked:
            print("[robot] parking before exit ...")
            try:
                await hardware.run(followers.park, fps)
                print("[robot] parked: arms off")
            except BaseException as exc:  # a second ctrl-c too: hardware.close() still lets the park finish
                print(f"[robot] park did not finish ({exc!r}); the arms hold under power. Support them and "
                      "cycle their power before the next start")
        try:
            await robot.disconnect()
        finally:
            robot.close()
            hardware.close()
            followers.disconnect()


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-arms", action="store_true", help="print the followers' joints, motors off")
    if parser.parse_args().check_arms:
        load_env()
        check_arms()
    else:
        asyncio.run(main())


if __name__ == "__main__":
    run()
