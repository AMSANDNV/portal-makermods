# Human-in-the-loop with LiveKit Portal and MakerMods Metal arms

This tutorial builds a human-in-the-loop (HITL) training pipeline with
[LiveKit Portal](https://github.com/livekit/portal) and a pair of MakerMods Metal arms.
Let a policy attempt a task, take over with the leader arms when it needs help,
and record the correction. Use those recordings to train a policy, then repeat.

Two Metal leaders control two Metal followers over a LiveKit room, with an
overhead and two wrist cameras. A recorder writes everything to a Rerun archive
that Portal converts into a LeRobot dataset for training ACT. To have a policy
to correct from day one, start with Dream Labs' Dream-1, which needs no data;
its corrected takes can then train ACT, distilling Dream-1 into a small local
policy.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="tutorial/images/hitl-loop-dark.svg">
  <img alt="Start with Dream-1 zero-shot and correct it, or collect teleop demos. Convert the archives to a LeRobot dataset and train ACT on them, distilling Dream-1 into a small local policy. Run ACT while you correct it; corrections feed the next training run." src="tutorial/images/hitl-loop-light.svg" width="100%">
</picture>

## The tutorial

Read the chapters in order to build the loop, one Portal concept at a time.

| Chapter | What you’ll learn |
|---|---|
| [00. What is Portal?](tutorial/00-what-is-portal.md) | Robot, operator, and observer roles in a HITL session |
| [01. Wire contract](tutorial/01-wire-contract.md) | Share camera, state, and action schemas across peers |
| [02. Robot loop](tutorial/02-robot-loop.md) | Publish observations and apply accepted actions |
| [03. Operator loop](tutorial/03-operator-loop.md) | Read the leaders, display video, and anchor actions |
| [04. A policy to interrupt: Dream-1](tutorial/04-dream-1.md) | Run a generalist policy before you have any data |
| [05. HITL recording](tutorial/05-hitl-recording.md) | Record both operators and build a training dataset |
| [06. Handoff](tutorial/06-handoff.md) | Switch between human and policy without ending a take |
| [07. Plugging in a policy](tutorial/07-plugging-in-policies.md) | Run ACT, and distill Dream-1 into it |
| [08. Running the rig](tutorial/08-running-the-rig.md) | Set up, collect, train, run inference, and record corrections |

## Quick start

On each host, after building MakerMods' `metal_sdk` on the arm hosts
([08. Running the rig](tutorial/08-running-the-rig.md#the-metal-sdk-robot-and-teleop-hosts)):

```bash
cp .env.example .env        # LiveKit URL, key, secret, and room
uv sync
uv pip install ~/metal-python-ros/metal_sdk/binding   # arm hosts only

uv run robot                # followers + cameras; start it first
uv run recorder             # writes data/sessions/*.rrd
uv run teleoperator         # leaders; c = control, r = record, x = quit
```

The Metal arms have no brakes and this project never switches their motors
off: rest or support the arms before stopping the robot or teleop.

## Layout

```text
portal.yaml             # shared camera, state, and action schema
src/
  robot.py              # publish observations and apply actions
  teleoperator.py       # leader arms, handoff, and episode markers
  recorder.py           # observer writing session archives
  _common.py            # environment, tokens, and pacing
  utils/metal.py        # the Metal arms and cameras
  utils/                # hotkeys, preview, task prompts
  policies/dream-1/     # Dream-1 as a Portal operator
  policies/act/         # train.py, inference.py, skypilot.yaml
tutorial/               # the HITL walkthrough
scripts/                # deployment helper
test/                   # hardware-free checks
```

## Requirements

- A LiveKit server with URL, API key, and API secret.
- Two Metal followers and two Metal leaders on CAN, with MakerMods' `metal_sdk`
  built on the arm hosts (Ubuntu 22.04, ROS 2 Humble).
- Three USB cameras: overhead, left wrist, right wrist.
- Python 3.12 and `uv`.
- A Dream Labs API key for Dream-1, and a CUDA machine for training ACT.

See [test/README.md](test/README.md) for hardware-free checks.
