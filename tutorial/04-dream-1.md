# 4. A policy to interrupt: Dream-1

Human-in-the-loop needs a policy to be in the loop with. Before you have
collected any data, Dream Labs' Dream-1 can be that policy: a generalist model
that runs on Dream Labs' servers, reads the three camera views and both arms,
and follows a task in words. It joins the room as one more operator, so the
next chapters can record it, interrupt it, and correct it.

## Run it

Save your Dream Labs API key as their
[Getting started](https://platform.dreamlabs.ai/api-documentation/) describes
(`~/.dreamlabs/credentials`). With the robot and teleoperator running:

```bash
uv run --group dream python src/policies/dream-1/inference.py --task "fold the towel" --dry-run
```

It joins as `policy-dream-1` and waits. Press `c` in teleop until it holds
control. In a dry run it prints its targets once a second instead of sending
them: check that they sit near the arms' pose before you drop `--dry-run`.
Press `c` again to take control back.

## What the policy does

[`src/policies/dream-1/inference.py`](../src/policies/dream-1/inference.py) is
the operator loop from chapter 3 with the leader arms replaced by a model call.
Dream-1 answers hand poses, not Metal joints; the `dream` client converts both
ways with Dream's own model of the Metal arm, so there is nothing to calibrate:

```python
client.policy({
    "checkpoint": "dreamlabs/dream-1-beta",
    "embodiment": embodiment(),           # the parts each request and reply carry
    "ik": {"arm": "metal", "head": "eef"},  # joints -> hand poses -> joints
})
```

Each tick it hands the client the newest observation, in the model's units:

| Portal | Dream-1 |
|---|---|
| `overhead_camera`, `left_wrist_camera`, `right_wrist_camera` | `video.overhead`, `video.wrist_l`, `video.wrist_r`: cropped to 16:9, 320x176 JPEG |
| `left_joint1..6.pos`, `right_joint1..6.pos` (rad) | `state.left_arm`, `state.right_arm`, unchanged |
| `left_gripper.pos`, `right_gripper.pos` (mm) | `state.left_grip`, `state.right_grip`: mm / 100 |

A reply is 48 rows per arm. The loop plays them at 20 rows a second, and asks
for the next chunk on a worker thread before this one runs out
(`client.refill_due()`), so the arms keep moving while the network works.

## Keeping it safe

- Every action names the observation it answers, as the human's does.
- The commanded target moves at most `DREAM_MAX_JOINT_RAD_S` (0.5 rad/s) per
  joint, so taking control ramps from where the arms are.
- The robot still ramps and clamps every command (chapter 2).
- No fresh view, or no row due: it sends nothing, and the arms hold.
- Each handoff to Dream-1 starts a new episode, so it never resumes a plan
  made before your correction.

Dream-1 will make mistakes on your task. That is the point of the next
chapters: record it, take over when it slips, and keep the correction.

Next: [05. HITL recording](05-hitl-recording.md).
