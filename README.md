# Human-in-the-loop with Portal and MakerMods Metal arms

Run Dream-1, take over with the leader arms when it needs help, and record the
corrected attempts. Train ACT on those recordings, then repeat the correction loop.

## The tutorial

Follow these chapters in order:

1. **[Set up the rig](tutorial/01-first-recording.md)** and check human control.
2. **[Run Dream-1 and record corrections](tutorial/02-dream-1.md)**.
3. **[Train ACT](tutorial/03-train-act.md)** on the corrected takes.

To collect human-only demonstrations instead, see the
[alternative recording path](tutorial/09-reference.md#human-only-demonstrations).

You need two Metal leaders, two Metal followers, three USB cameras, Linux hardware
hosts, Python 3.12, `uv`, and a LiveKit server. ACT training also needs a CUDA
machine. Dream-1 needs a Dream Labs API key.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="tutorial/images/hitl-loop-dark.svg">
  <img alt="Start with Dream-1 zero-shot and correct it, or collect teleop demos. Convert the archives to a LeRobot dataset and train ACT on them, distilling Dream-1 into a small local policy. Run ACT while you correct it; corrections feed the next training run." src="tutorial/images/hitl-loop-light.svg" width="100%">
</picture>

| Chapter | Purpose |
|---|---|
| [00. Overview](tutorial/00-what-is-portal.md) | Roles and vocabulary |
| [01. Setup](tutorial/01-first-recording.md) | Connect the rig and check human control |
| [02. Dream-1](tutorial/02-dream-1.md) | Run, correct, record, and convert |
| [03. ACT](tutorial/03-train-act.md) | Train, run, correct, and retrain |

Read these after completing the walkthrough, or when you need to change the code:

| Chapter | Purpose |
|---|---|
| [04. Shared schema](tutorial/04-wire-contract.md) | Cameras, state, and actions |
| [05. Robot loop](tutorial/05-robot-loop.md) | Publish observations and apply actions |
| [06. Operator loop](tutorial/06-operator-loop.md) | Send human actions and mark takes |
| [07. Recording](tutorial/07-hitl-recording.md) | Align actions with observations |
| [08. Handoff](tutorial/08-handoff.md) | Switch between human and policy |
| [09. Reference](tutorial/09-reference.md) | Settings, alternatives, and troubleshooting |

**Metal arms have no brakes.** Start the robot with the followers resting: that is
where they park. `p` parks them (walk back, switch off) and powers them up again;
Ctrl-C on the robot parks before it exits. A fault never disables the motors.

The runnable peers are in `src/`; policies are in `src/policies/`.
All peers share [`portal.yaml`](portal.yaml).
For checks without hardware, see [test/README.md](test/README.md).
