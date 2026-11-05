# 2. A policy to interrupt: Dream-1

After [your first recording](01-first-recording.md), you can add a policy.
This chapter is optional: skip to [ACT](03-train-act.md) to train only on human demonstrations.

Even before you have collected training data, Dream Labs' Dream-1 can be that policy: a generalist model
that runs on Dream Labs' servers, reads the three camera views and both arms,
and follows a task in words. It joins the room as one more operator, so the
recorder can capture its actions and your corrections in one take.

## Run a dry run

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

## Record a correction

Stop the dry run with Ctrl-C. Start the recorder if it is not running, then
restart the policy with motion enabled:

```bash
uv run --group dream python src/policies/dream-1/inference.py --task "fold the towel"
```

Keep only one policy connected while learning the handoff. In teleop:

1. Press `c` until the human is selected, then move to the starting pose.
2. Press `r` to begin a take, then `c` to select `policy-dream-1`.
3. Keep the leaders near the followers’ poses. When the policy needs help,
   press `c` to take control and guide the leaders.
4. Press `c` to let Dream-1 continue, or `r` to finish the take.
5. Use `[` to discard an open take you would not want ACT to learn from.

Check the selected identity after pressing `c`: it cycles through connected
operators. Recording continues across every handoff. The target ramp limits
command changes; it does not replace keeping the leaders near the followers.

Stop the recorder with Ctrl-C to flush the archive. Follow
[Convert and inspect](01-first-recording.md#convert-and-inspect) before training.
Use a new dataset ID if you already converted your first take; conversion does
not append to an existing dataset directory.

## What the policy does

[`src/policies/dream-1/inference.py`](../src/policies/dream-1/inference.py) is
the operator loop from chapter 6 with the leader arms replaced by a model call.
Dream-1 answers hand poses, not Metal joints; the `dream` client converts both
ways with Dream's own model of the Metal arm, so this example uses that built-in conversion:

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
- The robot still ramps and clamps every command ([robot loop](05-robot-loop.md)).
- No fresh view, or no row due: it sends nothing, and the arms hold.
- Each handoff to Dream-1 starts a new episode, so it never resumes a plan
  made before your correction.

Dream-1 will make mistakes on your task. Keep useful takes and discard failed ones before training ACT.

---

Previous: [1. Set up and record your first take](01-first-recording.md) · [Tutorial index](../README.md#the-tutorial) · Next: [3. Train ACT and improve it with corrections](03-train-act.md)
