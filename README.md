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

Start with **[01. Your first recording](tutorial/01-first-recording.md)** if you
have the hardware ready. Read chapter 00 first for the vocabulary and overview.
The first three hands-on chapters take you from a demonstration to a policy you
can correct; the remaining chapters explain the code and settings.

| Chapter | What you’ll do or learn |
|---|---|
| [00. What is Portal?](tutorial/00-what-is-portal.md) | Meet the robot, operator, policy, and recorder |
| [01. Your first recording](tutorial/01-first-recording.md) | Set up the rig, record a take, and convert it to a dataset |
| [02. Try Dream-1 (optional)](tutorial/02-dream-1.md) | Run a policy without training data and record corrections |
| [03. Train ACT](tutorial/03-train-act.md) | Train on your takes, run ACT, and improve it with corrections |
| [04. Shared schema](tutorial/04-wire-contract.md) | Understand the camera, state, and action contract |
| [05. Robot loop](tutorial/05-robot-loop.md) | Follow observations out and accepted actions back in |
| [06. Operator loop](tutorial/06-operator-loop.md) | Read the leaders, display video, and timestamp actions |
| [07. Recording](tutorial/07-hitl-recording.md) | Understand takes, action timestamps, and dataset alignment |
| [08. Handoff](tutorial/08-handoff.md) | See how control switches without ending a take |
| [09. Configuration and troubleshooting](tutorial/09-reference.md) | Look up settings, deployment steps, and fixes |

For a first run, you need the Metal arms and cameras, a LiveKit server, Python
3.12, and `uv`. Chapter 1 walks through the SDK and host setup. A Dream Labs API
key is only needed for Dream-1; a CUDA training machine is only needed for ACT.
You can also run the [hardware-free checks](test/README.md).

The Metal arms have no brakes and this project never switches their motors
off: rest or support the arms before stopping the robot or teleop.

## Layout

```text
portal.yaml             # shared camera, state, and action schema
src/
  robot.py              # publish observations and apply actions
  teleoperator.py       # leader arms, handoff, and episode markers
  recorder.py           # observer writing session archives
  utils/common.py       # environment, tokens, and pacing
  utils/metal.py        # the Metal arms and cameras
  utils/                # hotkeys, preview, task prompts
  policies/dream-1/     # Dream-1 as a Portal operator
  policies/act/         # train.py, inference.py, skypilot.yaml
tutorial/               # the HITL walkthrough
scripts/                # deployment helper
test/                   # hardware-free checks
```
