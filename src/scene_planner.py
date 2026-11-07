"""Visual strategic planner: the first frame of the scene -> an ordered Dream-1 task list.

Sends the overhead camera's first frame to an OpenAI-compatible vision model (the NVIDIA NIM
inference hub) and asks for a short, ordered list of imperative tasks that accomplish a goal
(default: make a quesadilla). `uv run dream-1-tasks` then executes them one at a time.

    uv run scene-planner --from-room                      # grab the frame from the running robot's overhead camera
    uv run scene-planner --image scene.jpg                # or plan from a saved image
    uv run scene-planner --camera /dev/v4l/by-path/...    # or grab straight from a camera (robot NOT running)
    uv run scene-planner --from-room --drive              # also walk Dream-1 through the plan, one Enter per step

The plan is printed, written to plan.json, and shown as a ready-to-run `dream-1-tasks` command
(it takes up to nine --task presets). Config comes from the environment / .env.local:

    NIM_BASE_URL       OpenAI-compatible base URL, e.g. https://integrate.api.nvidia.com/v1
    NIM_API_KEY        the gateway API key
    NIM_PLANNER_MODEL  the vision model id, e.g. nvidia/nemotron-3.5-super-vl-120b-a12b

--drive sends each task to dream-1-tasks (dream.set_task) then resumes it (dream.resume), waiting
for you to press Enter before each step; it never drives the arms itself. Start the robot and
`uv run dream-1-tasks` first, with a hand on the park key.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import (CONFIG_PATH, DREAM_RESUME_RPC, DREAM_SET_TASK_RPC, DREAM_STATUS_RPC,
                          DREAM_TASKS_IDENTITY, env_str, load_env, mint_token, required_env)

# Where the overhead view sits in portal.yaml; the model learns the scene from it.
OVERHEAD_TRACK = "overhead_camera"
MAX_TASKS = 9           # dream-1-tasks takes 1..9 presets
MAX_TASK_CHARS = 200    # one instruction; longer is likely a model mistake
PLAN_PATH = Path(__file__).resolve().parents[1] / "plan.json"

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
DEFAULT_GOAL = "make a cheese quesadilla on the griddle"

SYSTEM_PROMPT = """\
You are the visual strategic planner for a bimanual MakerMods robot driven by the Dream-1 policy.
You see one photo of the workspace (an overhead camera) and a goal. Produce the ordered list of
short manipulation steps the robot must perform to reach the goal, grounded ONLY in what the photo
shows (ingredients, tools, their positions).

Rules for each step:
- One atomic manipulation per step, phrased as a short imperative instruction Dream-1 can follow,
  e.g. "pick up the tortilla and lay it flat on the griddle".
- Name the specific object you see ("the yellow cheese", "the red bowl"), never vague references.
- Order them so the task actually succeeds (e.g. tortilla before cheese before folding before plating).
- At most nine steps. Keep each under 200 characters.
- Do not include setup, safety, commentary, or numbering inside the strings.

Respond with ONLY a JSON object of this exact shape and nothing else:
{"reasoning": "<one short sentence on what you see and the strategy>", "tasks": ["step 1", "step 2", ...]}"""


# ---------------------------------------------------------------------------- frame sources


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"could not read image {path!r}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def grab_camera(device: str) -> np.ndarray:
    """Open a camera device directly and grab one frame (fails if the robot already owns it)."""
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    try:
        for _ in range(10):  # let auto-exposure settle
            ok, frame = cap.read()
        if not ok or frame is None:
            raise SystemExit(f"could not grab a frame from {device!r} (is the robot using it? use --from-room)")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


async def grab_from_room(timeout_s: float = 10.0) -> np.ndarray:
    """Grab the overhead camera's first frame from the running robot, via a Portal operator that never drives."""
    from livekit.portal import Operator, OperatorConfig, frame_bytes_to_numpy_rgb

    url, room = required_env("LIVEKIT_URL"), required_env("LIVEKIT_ROOM")
    op = Operator(OperatorConfig.from_yaml_file(CONFIG_PATH, room))
    got: asyncio.Future[np.ndarray] = asyncio.get_running_loop().create_future()

    def on_frame(name: str, frame) -> None:
        if not got.done():
            got.set_result(frame_bytes_to_numpy_rgb(frame.data, frame.width, frame.height))

    op.on_video_frame(OVERHEAD_TRACK, on_frame)
    try:
        await op.connect(url, mint_token("scene-planner", room))
        return await asyncio.wait_for(got, timeout_s)
    except asyncio.TimeoutError:
        raise SystemExit(f"no {OVERHEAD_TRACK} frame within {timeout_s:g}s; is `uv run robot` running?")
    finally:
        try:
            await op.disconnect()
        finally:
            op.close()


# ---------------------------------------------------------------------------- the VLM call


def encode_frame(rgb: np.ndarray) -> str:
    ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise SystemExit("could not JPEG-encode the frame")
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode("ascii")


def extract_tasks(content: str) -> tuple[str, list[str]]:
    """Pull the reasoning and task list out of the model's reply, tolerating <think> blocks and prose."""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)  # first JSON object anywhere in the reply
        if match:
            try:
                obj = json.loads(match.group(0))
            except json.JSONDecodeError:
                obj = None
    if not isinstance(obj, dict) or not isinstance(obj.get("tasks"), list):
        raise SystemExit(f"could not parse a task list from the model reply:\n{content[:800]}")
    tasks = []
    for item in obj["tasks"]:
        task = " ".join(str(item).split())
        if task:
            tasks.append(task[:MAX_TASK_CHARS])
    if not tasks:
        raise SystemExit("the model returned an empty task list")
    return str(obj.get("reasoning", "")).strip(), tasks[:MAX_TASKS]


def plan_tasks(rgb: np.ndarray, goal: str) -> tuple[str, list[str]]:
    import httpx

    base = env_str("NIM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = env_str("NIM_PLANNER_MODEL", DEFAULT_MODEL)
    key = required_env("NIM_API_KEY")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"Goal: {goal}. Plan the ordered steps from this scene."},
                {"type": "image_url", "image_url": {"url": encode_frame(rgb)}},
            ]},
        ],
        "max_tokens": 2048,
        "temperature": 0.2,
    }
    print(f"[planner] asking {model} at {base} ...")
    try:
        resp = httpx.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                          json=body, timeout=120)
    except httpx.HTTPError as exc:
        raise SystemExit(f"could not reach the NIM endpoint {base}: {exc}")
    if resp.status_code != 200:
        raise SystemExit(f"NIM returned HTTP {resp.status_code}: {resp.text[:400]}\n"
                         "Check NIM_BASE_URL, NIM_API_KEY and NIM_PLANNER_MODEL in .env.local.")
    return extract_tasks(resp.json()["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------------- output / drive


def emit(reasoning: str, tasks: list[str]) -> None:
    PLAN_PATH.write_text(json.dumps({"goal_reasoning": reasoning, "tasks": tasks}, indent=2))
    print(f"\n[planner] strategy: {reasoning}\n")
    for i, task in enumerate(tasks, 1):
        print(f"  {i}. {task}")
    args = " ".join(f'--task "{t}"' for t in tasks)
    print(f"\n[planner] plan written to {PLAN_PATH}")
    print(f"[planner] run it with:\n  uv run dream-1-tasks {args}\n")


async def drive(tasks: list[str]) -> None:
    """Walk dream-1-tasks through the plan: set each task, resume, wait for Enter. Never drives the arms here."""
    from livekit import rtc

    url, room = required_env("LIVEKIT_URL"), required_env("LIVEKIT_ROOM")
    client = rtc.Room()
    await client.connect(url, mint_token("scene-planner", room))
    try:
        if DREAM_TASKS_IDENTITY not in client.remote_participants:
            raise SystemExit(f"{DREAM_TASKS_IDENTITY} is not in the room; start `uv run dream-1-tasks` first")

        async def rpc(method: str, payload: dict) -> None:
            await client.local_participant.perform_rpc(
                destination_identity=DREAM_TASKS_IDENTITY, method=method,
                payload=json.dumps(payload), response_timeout=5.0)

        print("\n[planner] driving the plan. A hand on the park key (p). Ctrl-C stops.\n")
        for i, task in enumerate(tasks, 1):
            input(f"[planner] step {i}/{len(tasks)} -- press Enter to set and run: {task!r} > ")
            await rpc(DREAM_SET_TASK_RPC, {"task": task})
            await rpc(DREAM_RESUME_RPC, {})
            print(f"[planner] Dream-1 driving step {i}. Press Enter when it is done to continue.")
            input()
        print("[planner] plan complete.")
    finally:
        await client.disconnect()


async def amain(args: argparse.Namespace) -> None:
    if args.image:
        rgb = load_image(args.image)
    elif args.camera:
        rgb = grab_camera(args.camera)
    else:
        rgb = await grab_from_room()
    reasoning, tasks = plan_tasks(rgb, args.goal)
    emit(reasoning, tasks)
    if args.drive:
        await drive(tasks)


def run() -> None:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-room", action="store_true",
                        help="grab the overhead frame from the running robot (default)")
    source.add_argument("--image", help="plan from a saved image file instead")
    source.add_argument("--camera", help="grab one frame straight from this camera device (robot not running)")
    parser.add_argument("--goal", default=DEFAULT_GOAL, help=f"the task goal (default: {DEFAULT_GOAL!r})")
    parser.add_argument("--drive", action="store_true",
                        help="walk dream-1-tasks through the plan, one Enter per step")
    asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    run()
