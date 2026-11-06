"""Robot side: drives the pair of MakerMods Metal followers behind a Portal `Robot`.

Run with `uv run robot`. Each tick reads both arms and the three cameras,
publishes them on one shared timestamp so the operator's synchronizer can match
them into a single observation, and applies whatever action last cleared the
active-operator gate.

Ctrl-C stops and leaves the arms holding under power. `p` here, or in teleop or a policy,
parks instead: the arms walk back to the pose they were in when this started, switch off,
and the process exits, so the next start needs no power cycle.

`uv run robot --check-arms` opens the followers read-only and prints their joints: move one
to see which link it is on.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from typing import Iterator

from utils.common import (CONFIG_PATH, PARK_RPC, ROBOT_IDENTITY, HardwareThread, TickBudget, env_int, load_env,
                          mint_token, pace, required_env)
from livekit.portal import Action, Robot, RobotConfig
from utils.hotkeys import Hotkeys
from utils.metal import build_followers, check_arms


def diagnostics(followers, budget: TickBudget) -> Iterator[str]:
    """The 5s console lines: achieved rate, arm read errors, speed cap."""
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

    url = required_env("LIVEKIT_URL")
    room = required_env("LIVEKIT_ROOM")
    cfg = RobotConfig.from_yaml_file(CONFIG_PATH, room)
    fps = env_int("PORTAL_FPS", cfg.fps)
    tracks = list(cfg.video_tracks)

    followers = build_followers(tracks, fps)
    robot = Robot(cfg)
    hardware = HardwareThread("followers")

    latest_action: dict[str, float] = {}
    park_asked_by: str | None = None

    def on_action(action: Action) -> None:
        nonlocal latest_action
        latest_action = {k: float(v) for k, v in action.values.items()}

    def on_active_operator_changed(identity: str | None) -> None:
        nonlocal latest_action
        latest_action = {}  # never replay the previous operator's last command
        print(f"[robot] active operator now: {identity}")

    def on_operator_left(identity: str) -> None:
        # Portal keeps control pinned to a departed operator so it can resume
        # on reconnect. A physical arm should not wait on that, so release it.
        nonlocal latest_action
        if identity == robot.active_operator():
            print(f"[robot] active operator '{identity}' left; releasing")
            latest_action = {}
            asyncio.create_task(robot.set_active_operator(None))

    def on_park(data) -> str:
        nonlocal park_asked_by
        park_asked_by = data.caller_identity
        return "parking: the arms walk to rest, switch off, and the robot exits"

    robot.register_rpc_method(PARK_RPC, on_park)
    robot.on_action(on_action)
    robot.on_active_operator_changed(on_active_operator_changed)
    robot.on_operator_left(on_operator_left)

    followers.connect()
    print(f"[robot] connecting to {url} as '{ROBOT_IDENTITY}' in room '{room}' ...")
    await robot.connect(url, mint_token(ROBOT_IDENTITY, room))
    print(f"[robot] connected; cameras={list(tracks)}; streaming at {fps} fps; "
          "p to park and exit, ctrl-c to stop holding")

    budget = TickBudget(fps)
    hotkeys = Hotkeys(global_tap=False)  # this terminal only; works over SSH
    hotkeys.start()

    try:
        async for tick in pace(fps):
            if "p" in hotkeys.drain():
                park_asked_by = "this terminal"
            if park_asked_by:
                print(f"[robot] park asked by {park_asked_by}; releasing control and walking the arms to rest ...")
                park_asked_by, latest_action = None, {}
                await robot.set_active_operator(None)
                try:
                    await hardware.run(followers.park, fps)
                except Exception as exc:
                    print(f"[robot] park stopped: {exc}. The arms hold; press p to try again")
                    continue
                print("[robot] parked and switched off; exiting")
                break

            # One stamp for state and frames, so they match into one observation. Portal's
            # clock never goes backwards, which its lossy ordering guard relies on: a
            # receiver drops anything stamped behind the newest it has from us.
            ts_us = robot.now_us()
            started = time.perf_counter()
            state, frames = await hardware.run(followers.get_observation)
            device_ms = (time.perf_counter() - started) * 1e3

            # Empty state means an arm is unreadable. Keep publishing video so
            # the operator can see the arm stopped, but never invent a state.
            if state:
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
        print("[robot] disconnecting ...")
        try:
            await robot.disconnect()
        finally:
            robot.close()
            hardware.close()
            followers.disconnect()


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-arms", action="store_true", help="print the followers' joints read-only, motors off")
    if parser.parse_args().check_arms:
        load_env()
        check_arms()
    else:
        asyncio.run(main())


if __name__ == "__main__":
    run()
