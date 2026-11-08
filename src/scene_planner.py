"""Astra director: "make a quesadilla" -> plan + contact markers -> drive Dream-1 -> verify -> next.

Astra (NVIDIA NIM, gpt-6-astra via the Responses API) is the visual strategic planner. Given the
overhead camera's first frame and a goal, it returns an ordered list of steps, each with a contact
point (a pixel on the object where Dream-1 should make contact). The director then, for each step:
overlays the contact marker on the overhead view Dream-1 reads, tells dream-1-tasks the step and
resumes Dream-1, and polls Astra on fresh frames until it judges the step done, then advances.

    uv run scene-planner --plan-only                       # just print/save the plan (no robot needed if --image)
    uv run scene-planner --image scene.jpg --plan-only     # plan from a saved image
    uv run scene-planner --goal "make a quesadilla"        # full director: plan + drive Dream-1 + verify + next

Start `uv run robot` and `uv run dream-1-tasks` first (the director drives Dream-1 over Portal; it
never drives the arms itself). A hand on the park key. By default each step waits for Enter before
Dream-1 moves; --auto runs them back to back.

Config (env / .env.local):
    NIM_BASE_URL       OpenAI-compatible base, e.g. https://inference-api.nvidia.com/v1
    NIM_API_KEY        the gateway API key
    NIM_PLANNER_MODEL  the model id, e.g. azure/openai/gpt-6-astra
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import (CONFIG_PATH, DREAM_PAUSE_RPC, DREAM_RESUME_RPC, DREAM_SET_MARKERS_RPC, DREAM_SET_TASK_RPC,
                          DREAM_TASKS_IDENTITY, env_str, load_env, mint_token, required_env)

OVERHEAD_TRACK = "overhead_camera"
MAX_TASKS = 9
MAX_TASK_CHARS = 200
PLAN_PATH = Path(__file__).resolve().parents[1] / "plan.json"
ANNOTATED_PATH = Path(__file__).resolve().parents[1] / "plan_annotated.jpg"
GROCERIES_PATH = Path(__file__).resolve().parents[1] / "groceries.json"

DEFAULT_BASE_URL = "https://inference-api.nvidia.com/v1"
DEFAULT_MODEL = "azure/openai/gpt-6-astra"
DEFAULT_GOAL = "make a cheese quesadilla on the griddle"

SYSTEM_PROMPT = """\
You are the visual strategic planner for a bimanual MakerMods robot driven by the Dream-1 policy.
You see one photo of the workspace (an overhead camera) and a goal. First identify the ingredients
and tools the GOAL needs, and split them into which are VISIBLE in the photo ("present") and which
are NOT ("missing"). Then produce the ordered list of short manipulation steps the robot must
perform, grounded ONLY in what the photo shows (ingredients, tools, their positions).

For each step also give the contact point: the pixel [x, y] in THIS image where the gripper should
make contact (e.g. the spot on the tortilla to grasp or press). Use image pixel coordinates with
[0, 0] at the top-left; x right, y down.

Rules for each step:
- One atomic manipulation per step, phrased as a short imperative instruction Dream-1 can follow,
  e.g. "pick up the tortilla and lay it flat on the griddle".
- Name the specific object you see ("the yellow cheese", "the red bowl"), never vague references.
- Order them so the task actually succeeds (e.g. tortilla before cheese before folding before plating).
- If an ingredient the goal needs is NOT visible, do not refuse: plan the manipulation steps you CAN do
  now with the objects that ARE visible, progressing toward the goal (e.g. only a tortilla is present ->
  lay it flat, fold it, press it). Only return an empty steps list if there is truly no manipulable object.
- At most nine steps. Keep each instruction under 200 characters.

Respond with ONLY a JSON object of this exact shape and nothing else:
{"reasoning": "<one short sentence on what you see and the strategy>",
 "present": ["<ingredient/tool the goal needs that IS visible>", ...],
 "missing": ["<ingredient/tool the goal needs that is NOT visible>", ...],
 "steps": [{"task": "<imperative instruction>", "contact_xy": [x, y]}, ...]}"""

VERIFY_PROMPT = """\
You are verifying a robot's progress from an overhead photo. You are told the step it just attempted
and the overall goal. Decide whether that step now looks complete in the image.
Respond with ONLY this JSON: {"done": true|false, "reason": "<short reason>"}"""


# ---------------------------------------------------------------------------- Astra (Responses API)


def encode_frame(rgb: np.ndarray) -> str:
    ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise SystemExit("could not JPEG-encode the frame")
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode("ascii")


def astra(system: str, user_text: str, image_rgb: np.ndarray | None = None, max_tokens: int = 2048) -> str:
    """One call to Astra's Responses API; returns the model's text. Raises SystemExit on any failure."""
    import httpx

    base = env_str("NIM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = env_str("NIM_PLANNER_MODEL", DEFAULT_MODEL)
    key = required_env("NIM_API_KEY")
    content: list[dict] = [{"type": "input_text", "text": user_text}]
    if image_rgb is not None:
        content.append({"type": "input_image", "image_url": encode_frame(image_rgb)})
    body = {
        "model": model,
        "instructions": system,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": max_tokens,
    }
    try:
        resp = httpx.post(f"{base}/responses",
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json=body, timeout=120)
    except httpx.HTTPError as exc:
        raise SystemExit(f"could not reach Astra at {base}: {exc}")
    if resp.status_code != 200:
        raise SystemExit(f"Astra returned HTTP {resp.status_code}: {resp.text[:400]}\n"
                         "Check NIM_BASE_URL, NIM_API_KEY and NIM_PLANNER_MODEL in .env.local.")
    data = resp.json()
    text = data.get("output_text") or "".join(
        chunk.get("text", "")
        for item in data.get("output", [])
        for chunk in item.get("content", [])
        if chunk.get("type") == "output_text"
    )
    if not text.strip():
        raise SystemExit(f"Astra returned no text: {json.dumps(data)[:400]}")
    return text


def _extract_json(content: str) -> dict:
    """Pull the first JSON object out of a reply, tolerating <think> blocks and surrounding prose."""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise SystemExit(f"could not parse JSON from Astra's reply:\n{content[:800]}")


def _contact_point(raw) -> list[int] | None:
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and all(isinstance(v, (int, float)) for v in raw):
        return [int(raw[0]), int(raw[1])]
    return None


def _str_list(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [" ".join(str(x).split()) for x in raw if str(x).strip()]


def plan(rgb: np.ndarray, goal: str) -> tuple[str, list[dict], list[str], list[str]]:
    """Ask Astra for the ordered steps, their contact points, and the present/missing ingredients."""
    print(f"[astra] planning {goal!r} ...")
    obj = _extract_json(astra(SYSTEM_PROMPT, f"Goal: {goal}. Plan the ordered steps from this scene.", rgb))
    raw_steps = obj.get("steps")
    if not isinstance(raw_steps, list):
        raise SystemExit(f"Astra did not return a steps list:\n{json.dumps(obj)[:600]}")
    steps = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        task = " ".join(str(item.get("task", "")).split())[:MAX_TASK_CHARS]
        if task:
            steps.append({"task": task, "contact_xy": _contact_point(item.get("contact_xy"))})
    if not steps:
        raise SystemExit("Astra returned an empty plan")
    return (str(obj.get("reasoning", "")).strip(), steps[:MAX_TASKS],
            _str_list(obj.get("present")), _str_list(obj.get("missing")))


def load_groceries() -> list[str]:
    try:
        return json.loads(GROCERIES_PATH.read_text())
    except (OSError, ValueError):
        return []


def add_groceries(items: list[str]) -> list[str]:
    """Append items to the persisted groceries list, skipping ones already on it (case-insensitive)."""
    have = load_groceries()
    lower = {g.lower() for g in have}
    for item in items:
        item = str(item).strip()
        if item and item.lower() not in lower:
            have.append(item)
            lower.add(item.lower())
    GROCERIES_PATH.write_text(json.dumps(have, indent=2))
    return have


def verify(rgb: np.ndarray, task: str, goal: str) -> tuple[bool, str]:
    """Ask Astra whether the attempted step now looks complete in the current frame."""
    obj = _extract_json(astra(
        VERIFY_PROMPT,
        f"Goal: {goal}. The robot just attempted this step: {task!r}. Is this step complete now?",
        rgb, max_tokens=256))
    return bool(obj.get("done")), str(obj.get("reason", "")).strip()


# ---------------------------------------------------------------------------- frames / overlay


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"could not read image {path!r}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def grab_camera(device: str) -> np.ndarray:
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    try:
        ok, frame = False, None
        for _ in range(10):
            ok, frame = cap.read()
        if not ok or frame is None:
            raise SystemExit(f"could not grab a frame from {device!r} (is the robot using it? run the director instead)")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def overlay_markers(rgb: np.ndarray, points: list, labels: bool = True) -> np.ndarray:
    """Draw the contact points on a copy of the frame (for the saved plan image / UI)."""
    out = rgb.copy()
    for i, pt in enumerate(points, 1):
        if not pt:
            continue
        x, y = int(pt[0]), int(pt[1])
        cv2.circle(out, (x, y), 12, (0, 255, 0), 2)
        cv2.circle(out, (x, y), 3, (0, 255, 0), -1)
        if labels:
            cv2.putText(out, str(i), (x + 14, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return out


def emit(reasoning: str, steps: list[dict], frame: np.ndarray | None,
         present: list[str] | None = None, missing: list[str] | None = None) -> None:
    present, missing = present or [], missing or []
    PLAN_PATH.write_text(json.dumps(
        {"goal_reasoning": reasoning, "present": present, "missing": missing, "steps": steps}, indent=2))
    print(f"\n[astra] strategy: {reasoning}\n")
    if present or missing:
        print(f"[astra] present: {', '.join(present) or 'none'}")
        print(f"[astra] missing: {', '.join(missing) or 'none'}\n")
    for i, step in enumerate(steps, 1):
        at = f"  @ {step['contact_xy']}" if step.get("contact_xy") else ""
        print(f"  {i}. {step['task']}{at}")
    if frame is not None:
        annotated = overlay_markers(frame, [s.get("contact_xy") for s in steps])
        cv2.imwrite(str(ANNOTATED_PATH), cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR))
        print(f"\n[astra] annotated scene -> {ANNOTATED_PATH}")
    print(f"[astra] plan -> {PLAN_PATH}")
    cmd = " ".join(f'--task "{s["task"]}"' for s in steps)
    print(f"[astra] (manual run) uv run dream-1-tasks {cmd}\n")


# ---------------------------------------------------------------------------- the director


class Director:
    """One Portal connection that grabs the overhead frame and drives dream-1-tasks; it never drives the arms."""

    def __init__(self) -> None:
        self.op = None
        self._frame: np.ndarray | None = None
        self._decode = None

    async def connect(self) -> None:
        from livekit.portal import Operator, OperatorConfig, frame_bytes_to_numpy_rgb

        url, room = required_env("LIVEKIT_URL"), required_env("LIVEKIT_ROOM")
        self._decode = frame_bytes_to_numpy_rgb
        self.op = Operator(OperatorConfig.from_yaml_file(CONFIG_PATH, room))
        self.op.on_video_frame(OVERHEAD_TRACK, self._on_frame)
        await self.op.connect(url, mint_token("astra-director", room))
        if DREAM_TASKS_IDENTITY not in self.op.operators():
            await self.close()
            raise SystemExit(f"{DREAM_TASKS_IDENTITY} is not in the room; start `uv run dream-1-tasks` first")
        await self._wait_frame()

    def _on_frame(self, _name: str, frame) -> None:
        self._frame = self._decode(frame.data, frame.width, frame.height)

    async def _wait_frame(self, timeout_s: float = 10.0) -> np.ndarray:
        deadline = time.monotonic() + timeout_s
        while self._frame is None and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if self._frame is None:
            raise SystemExit(f"no {OVERHEAD_TRACK} frame within {timeout_s:g}s; is `uv run robot` running?")
        return self._frame

    @property
    def frame(self) -> np.ndarray:
        assert self._frame is not None
        return self._frame

    async def _rpc(self, method: str, payload: dict | None = None) -> str:
        return await self.op.perform_rpc(method, payload=json.dumps(payload or {}),
                                         destination=DREAM_TASKS_IDENTITY, response_timeout_ms=5000)

    async def cook(self, goal: str, *, auto: bool, verify_every_s: float, step_timeout_s: float) -> None:
        reasoning, steps, present, missing = await asyncio.to_thread(plan, self.frame, goal)
        emit(reasoning, steps, self.frame, present, missing)
        if missing and not self._gate_missing(missing, auto):
            print("[astra] stopping before Dream-1 runs; add the ingredients and plan again.")
            return
        print("[astra] directing Dream-1. A hand on the park key (p). Ctrl-C stops.\n")
        for i, step in enumerate(steps, 1):
            task, point = step["task"], step.get("contact_xy")
            if not auto:
                input(f"[astra] step {i}/{len(steps)} -- Enter to run: {task!r}{f' @ {point}' if point else ''} > ")
            await self._rpc(DREAM_SET_MARKERS_RPC, {"points": [point] if point else []})
            await self._rpc(DREAM_SET_TASK_RPC, {"task": task})
            await self._rpc(DREAM_RESUME_RPC)
            print(f"[astra] step {i} running: {task!r}")
            done = await self._watch_step(task, goal, verify_every_s, step_timeout_s)
            await self._rpc(DREAM_PAUSE_RPC)
            print(f"[astra] step {i} {'done' if done else 'timed out; pausing'}: {task!r}")
            if not done and not auto:
                if input("[astra] retry this step? [y/N] > ").strip().lower() == "y":
                    await self._retry(step, goal, verify_every_s, step_timeout_s)
        await self._rpc(DREAM_SET_MARKERS_RPC, {"points": []})
        print("\n[astra] plan complete.")

    def _gate_missing(self, missing: list[str], auto: bool) -> bool:
        """Report the missing ingredients and offer the groceries list. Returns True to proceed with what's here."""
        joined = ", ".join(missing)
        print(f"\n[astra] Missing {joined}! Should I add it to the groceries list?")
        if auto:  # unattended: log it to groceries and carry on with the visible objects
            print(f"[astra] --auto: added to groceries -> {add_groceries(missing)}")
            return True
        choice = input("[astra] [a] add to groceries, [p] proceed with what's here, [q] stop > ").strip().lower()
        if choice == "a":
            print(f"[astra] groceries -> {add_groceries(missing)}")
            return input("[astra] proceed with what's here now? [y/N] > ").strip().lower() == "y"
        return choice == "p"

    async def _watch_step(self, task: str, goal: str, every_s: float, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(every_s)
            done, reason = await asyncio.to_thread(verify, self.frame, task, goal)
            print(f"[astra]   verify: {'DONE' if done else 'not yet'} - {reason}")
            if done:
                return True
        return False

    async def _retry(self, step: dict, goal: str, every_s: float, timeout_s: float) -> None:
        await self._rpc(DREAM_RESUME_RPC)
        done = await self._watch_step(step["task"], goal, every_s, timeout_s)
        await self._rpc(DREAM_PAUSE_RPC)
        print(f"[astra] retry {'done' if done else 'still not done'}: {step['task']!r}")

    async def close(self) -> None:
        if self.op is not None:
            try:
                await self.op.disconnect()
            finally:
                self.op.close()
                self.op = None


async def amain(args: argparse.Namespace) -> None:
    if args.plan_only:
        if args.image:
            frame = load_image(args.image)
        elif args.camera:
            frame = grab_camera(args.camera)
        else:
            director = Director()
            await director.connect()
            try:
                frame = director.frame
            finally:
                await director.close()
        reasoning, steps, present, missing = await asyncio.to_thread(plan, frame, args.goal)
        emit(reasoning, steps, frame, present, missing)
        return

    director = Director()
    await director.connect()
    try:
        await director.cook(args.goal, auto=args.auto,
                            verify_every_s=args.verify_every, step_timeout_s=args.step_timeout)
    finally:
        await director.close()


def run() -> None:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--goal", default=DEFAULT_GOAL, help=f"the task goal (default: {DEFAULT_GOAL!r})")
    parser.add_argument("--plan-only", action="store_true", help="just plan and save; do not drive Dream-1")
    parser.add_argument("--image", help="plan from a saved image (with --plan-only)")
    parser.add_argument("--camera", help="grab one frame from this camera device (with --plan-only)")
    parser.add_argument("--auto", action="store_true", help="run steps back to back without an Enter per step")
    parser.add_argument("--verify-every", type=float, default=5.0, help="seconds between Astra verifications")
    parser.add_argument("--step-timeout", type=float, default=120.0, help="seconds before a step is given up on")
    asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    run()
