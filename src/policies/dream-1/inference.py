"""Run Dream Labs' Dream-1 zero-shot as a Portal operator; press c in teleop to hand it control.

Dream-1 runs on Dream Labs' servers. It reads three views (overhead, left
wrist, right wrist) and both arms, and answers 48 rows of hand poses per arm.
The `dream` client bridges to the Metal pair with Dream's own `metal` arm model
and tool center point (`"ik": {"arm": "metal"}`): it adds each hand's pose,
computed from our joints, to every request, and solves the model's hand poses
back to our joints. See tutorial/06-plugging-in-policies.md.

    uv run --group dream python src/policies/dream-1/inference.py --task "fold the towel"
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import time
from pathlib import Path

import cv2
import dream
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _common import CONFIG_PATH, HardwareThread, env_float, env_int, env_str, load_env, mint_token, pace, required_env
from livekit.portal import Operator, OperatorConfig, frame_bytes_to_numpy_rgb
from utils.metal import GRIPPER_MAX_MM, JOINTS, SIDES, arm_fields, clamp_to_limits

IDENTITY = "policy-dream-1"
ROLES = ("overhead", "wrist_l", "wrist_r")  # the three views Dream-1 reads
DEFAULT_CAMERAS = "overhead=overhead_camera,wrist_l=left_wrist_camera,wrist_r=right_wrist_camera"
CHUNK_KEYS = {"left": "q_l_chunk", "right": "q_r_chunk"}
FRAME_SIZE = (320, 176)  # width, height
ROW_HZ = 20  # chunk rows per second
STALE_S = 0.2  # hold when the newest observation is older than this
# Dream's Metal grip is the stroke in mm over this (`robots.metal.GRIP_SCALE_MM`).
GRIP_SCALE_MM = 100.0


def embodiment() -> dict:
    """Where each part sits in a state or action row. The client checks every request and reply against it."""
    spans, start = {}, 0
    for side in ("right", "left"):
        for part, width in (("arm", len(JOINTS)), ("grip", 1), ("eef_xyz", 3), ("eef_rot6", 6)):
            spans[f"{side}_{part}"] = [start, start + width]
            start += width
    return {"state": spans, "action": spans, "video": {role: f"observation.images.{role}" for role in ROLES}}


def camera_roles(spec: str, tracks) -> dict[str, str]:
    """Parse DREAM_CAMERAS, such as `overhead=overhead_camera,wrist_l=left_wrist_camera`."""
    roles = {}
    for item in filter(None, spec.replace(" ", "").split(",")):
        role, _, track = item.partition("=")
        if role not in ROLES or track not in tracks:
            raise ValueError(f"DREAM_CAMERAS {item!r}: role must be one of {ROLES}, track one of {list(tracks)}")
        roles[role] = track
    if not roles:
        raise ValueError("DREAM_CAMERAS maps no camera")
    return roles


def encode_frame(rgb: np.ndarray) -> str:
    """Center-crop to 16:9, resize to 320x176, and encode as base64 JPEG."""
    h, w = rgb.shape[:2]
    if h * 16 > w * 9:
        top = (h - w * 9 // 16) // 2
        rgb = rgb[top:top + w * 9 // 16]
    small = cv2.resize(rgb, FRAME_SIZE, interpolation=cv2.INTER_AREA)
    _, jpeg = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(jpeg.tobytes()).decode("ascii")


BLACK = encode_frame(np.zeros((FRAME_SIZE[1], FRAME_SIZE[0], 3), np.uint8))


def to_model(state: dict) -> np.ndarray:
    """Portal state -> one model row per arm, left then right: six joints in radians, then grip 0..1."""
    row = []
    for side in SIDES:
        *joints, gripper = arm_fields(side)
        row += [state[name] for name in joints] + [np.clip(state[gripper] / GRIP_SCALE_MM, 0.0, 1.0)]
    return np.asarray(row, dtype=np.float64)


def to_robot(row: np.ndarray) -> dict[str, float]:
    """One model row (left then right) -> a Portal action: radians and gripper mm, inside the soft limits."""
    action = {}
    for i, side in enumerate(SIDES):
        part = row[i * (len(JOINTS) + 1):(i + 1) * (len(JOINTS) + 1)]
        fields = arm_fields(side)
        action.update({name: float(value) for name, value in zip(fields[:-1], part[:-1])})
        action[fields[-1]] = float(np.clip(part[-1] * GRIP_SCALE_MM, 0.0, GRIPPER_MAX_MM))
    return clamp_to_limits(action)


def step_toward(last: np.ndarray, goal: np.ndarray, max_step: np.ndarray) -> np.ndarray:
    """Move the commanded target at most `max_step` per tick, so neither arm jumps."""
    return last + np.clip(goal - last, -max_step, max_step)


def chunk_rows(reply: dict) -> np.ndarray:
    """Both arms' chunks side by side, left then right: one row per step, joints in radians then grip."""
    parts = []
    for side in SIDES:
        rows = np.asarray(reply[CHUNK_KEYS[side]], dtype=np.float64)
        if rows.ndim != 2 or rows.shape[1] != len(JOINTS) + 1 or not np.isfinite(rows).all():
            raise dream.DreamActionError(
                f"{CHUNK_KEYS[side]} has shape {rows.shape}; expected [rows, {len(JOINTS) + 1}]")
        parts.append(rows)
    if parts[0].shape[0] != parts[1].shape[0]:
        raise dream.DreamActionError(f"chunks differ in length: {parts[0].shape[0]} and {parts[1].shape[0]}")
    return np.concatenate(parts, axis=1)


def retry_delay(error: dream.DreamError) -> float:
    """Seconds to wait before asking again. Raises the error when asking again cannot help."""
    if isinstance(error, dream.DreamSessionExpired):
        return 0.0  # the client already moved to a new session
    if isinstance(error, (dream.DreamTimeout, dream.DreamProtocolError)) or (
            isinstance(error, dream.DreamServerError) and error.retryable):
        wait = getattr(error, "retry_after_s", None) or 1.0
        print(f"[policy-dream-1] {getattr(error, 'reason', None) or error}; asking again in {wait:.1f}s")
        return wait
    raise error


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=env_str("PORTAL_DATA_TASK", "MakerMods Metal bimanual manipulation"))
    parser.add_argument("--dry-run", action="store_true", help="print the targets instead of sending them")
    args = parser.parse_args()

    url, room = required_env("LIVEKIT_URL"), required_env("LIVEKIT_ROOM")
    cfg = OperatorConfig.from_yaml_file(CONFIG_PATH, room)
    fps = env_int("PORTAL_FPS", cfg.fps)
    expected = [name for side in SIDES for name in arm_fields(side)]
    if sorted(field.name for field in cfg.action_schema) != sorted(expected):
        raise ValueError(f"portal.yaml actions must be the Metal pair's fields: {expected}")
    roles = camera_roles(env_str("DREAM_CAMERAS", DEFAULT_CAMERAS), cfg.video_tracks)
    per_arm = len(JOINTS) + 1
    max_step = np.full(2 * per_arm, env_float("DREAM_MAX_JOINT_RAD_S", 0.5) / fps)
    max_step[per_arm - 1::per_arm] = 0.05 * ROW_HZ / fps  # each grip moves at most 5% of its range per row

    client = dream.DreamClient()
    worker = HardwareThread("dream-1")  # one request at a time, off the event loop
    print("[policy-dream-1] compiling IK ...")
    await worker.run(client.policy, {
        "checkpoint": env_str("DREAM_CHECKPOINT", "dreamlabs/dream-1-beta"),
        "embodiment": embodiment(),
        "ik": {"arm": "metal", "head": "eef"},  # Dream's Metal model and tool center point
        "action_hz": ROW_HZ,
    })

    def ask() -> tuple[float, np.ndarray]:
        asked_at = time.monotonic()
        return asked_at, chunk_rows(client.get_action())

    def send_observation(obs) -> None:
        frames = {}
        for role in ROLES:
            if role not in roles:
                frames[f"video.{role}"] = BLACK  # a camera the rig lacks: black, never another view
                continue
            frame = obs.frames[roles[role]]
            frames[f"video.{role}"] = encode_frame(frame_bytes_to_numpy_rgb(frame.data, frame.width, frame.height))
        row = to_model(obs.state)
        left, right = row[:per_arm], row[per_arm:]
        client.set_observation(frames, {
            "prompt": args.task,
            "state.right_arm": right[:-1].tolist(),
            "state.right_grip": right[-1:].tolist(),
            "state.left_arm": left[:-1].tolist(),
            "state.left_grip": left[-1:].tolist(),
        })

    op = Operator(cfg)
    latest_obs, received_at = None, 0.0
    control_changed = True

    def on_observation(obs) -> None:
        nonlocal latest_obs, received_at
        latest_obs, received_at = obs, time.monotonic()

    def on_active_operator_changed(identity) -> None:
        nonlocal control_changed
        control_changed = True
        print(f"[policy-dream-1] active operator: {identity}")

    op.on_observation(on_observation)
    op.on_active_operator_changed(on_active_operator_changed)

    request = None  # the get_action in flight
    chunk = None  # (asked_at, rows, first row played); row k is due k / ROW_HZ s after asked_at
    target = None  # the last commanded row, in model units
    sent_ts = None  # the observation the client has
    retry_at = 0.0
    try:
        await op.connect(url, mint_token(IDENTITY, room))
        me = op.local_identity()
        print(f"[policy-dream-1] ready{' (dry run)' if args.dry_run else ''}; press c in teleop to select {me}")
        async for tick in pace(fps):
            obs = latest_obs
            if op.active_operator() != me:
                continue
            if (obs is None or time.monotonic() - received_at > STALE_S
                    or any(track not in obs.frames for track in roles.values())):
                continue  # no fresh view of the arms: hold the last target

            if control_changed:
                # Every handoff to Dream-1 starts a new episode: a new session, an
                # empty frame window, and a ramp from where the arms are now.
                control_changed = False
                if request is not None:
                    await asyncio.gather(request, return_exceptions=True)
                    request = None
                await worker.run(client.reset_episode)
                chunk, sent_ts, retry_at = None, None, 0.0
                target = to_model(obs.state)

            if obs.timestamp_us != sent_ts:
                send_observation(obs)
                sent_ts = obs.timestamp_us

            now = time.monotonic()
            if request is not None and request.done():
                try:
                    asked_at, rows = request.result()
                    chunk = (asked_at, rows, int((now - asked_at) * ROW_HZ))  # skip rows the round trip used up
                except dream.DreamError as error:
                    retry_at = now + retry_delay(error)
                request = None

            row, played, remaining = None, 0, 0
            if chunk is not None:
                asked_at, rows, first = chunk
                due = int((now - asked_at) * ROW_HZ)
                if due < len(rows):
                    row, played, remaining = rows[due], due - first, len(rows) - due
            if request is None and now >= retry_at and client.refill_due(played, remaining):
                request = asyncio.ensure_future(worker.run(ask))
            if row is None:
                continue  # the chunk ran out: hold until the next one lands

            target = step_toward(target, row, max_step)
            action = to_robot(target)
            if not args.dry_run:
                op.send_action(action, in_reply_to_ts_us=obs.timestamp_us)
            elif tick % fps == 0:
                print("[policy-dream-1] target " + ", ".join(f"{k}={v:.2f}" for k, v in action.items()))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[policy-dream-1] stopping")
    finally:
        worker.close()  # waits for a request in flight
        client.close()
        try:
            await op.disconnect()
        finally:
            op.close()


if __name__ == "__main__":
    asyncio.run(main())
