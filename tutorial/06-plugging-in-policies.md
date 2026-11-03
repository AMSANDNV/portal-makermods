# 6. Plugging in a policy

A policy uses the same `Operator` interface as the human. This project ships two:
ACT, trained on your own takes, and Dream Labs' Dream-1, which runs zero-shot.
[`src/policies/act/inference.py`](../src/policies/act/inference.py) loads an ACT
checkpoint, receives synchronized observations, and sends actions while selected
by the teleoperator.

## Load the training contract

```bash
uv run --group train python src/policies/act/inference.py \
  --checkpoint outputs/my-act/checkpoints/020000/pretrained_model \
  --dataset-root data/local/portal-makermods
```

The dataset root supplies `meta/info.json`. State and action names are read in
training order and checked against `portal.yaml`. Do not alphabetize joint
names: normalization vectors and output tensors use the dataset’s order.

Camera names come from the checkpoint’s input features, and each must be a
track in `portal.yaml`.

## Follow one inference step

The observation callback updates a latest-observation slot. While ACT holds the
gate, the loop:

1. Packs joint state in dataset order and converts RGB images to CHW float
   tensors in `[0, 1]`.
2. Runs the saved preprocessor, `policy.select_action`, and postprocessor.
3. Maps the output back to joint names and sends it with the input timestamp.

```python
op.send_action(action, in_reply_to_ts_us=obs.timestamp_us)
```

Portal supplies the action’s send timestamp. Inference runs on a dedicated
worker so it does not block network callbacks. The result is sent only if
control remained with ACT throughout inference.

## Chunks and handoff

ACT manages its action chunks internally; Portal receives one flat action per
tick. Temporal ensembling is enabled by default with coefficient `0.01`.
The example replans each tick and resets the ensemble after a handoff.

The policy starts without control. Press `c` in teleop to select it. When control
returns after a human correction, the policy resets its buffered actions before
continuing. There is no separate SPACE engagement key or automatic claim.

## Dream-1, zero-shot

[`src/policies/dream-1/inference.py`](../src/policies/dream-1/inference.py) runs
Dream Labs' `dreamlabs/dream-1-beta` as another operator, `policy-dream-1`. It
needs no training: the model runs on Dream Labs' servers and reads the task as
a prompt. This rig is the shape Dream-1 expects: two arms and three views, so
nothing is padded.

```bash
uv run --group dream python src/policies/dream-1/inference.py --task "fold the towel"
```

The `dream` client does the arm-specific work. With `"ik": {"arm": "metal",
"head": "eef"}` it uses Dream's own model of the Metal arm and its tool center
point, so there is no `dreamcli tcp` step. On every request it adds each hand's
pose, computed from the joints, and it solves the model's hand poses back to
joints. The policy maps between Portal and the model:

| Portal | Dream-1 |
|---|---|
| `overhead_camera`, `left_wrist_camera`, `right_wrist_camera` | `video.overhead`, `video.wrist_l`, `video.wrist_r`: center-cropped to 16:9, 320x176 JPEG |
| `left_joint1..6.pos`, `right_joint1..6.pos` (rad) | `state.left_arm`, `state.right_arm`, unchanged |
| `left_gripper.pos`, `right_gripper.pos` (mm) | `state.<side>_grip` = mm / 100, Dream's Metal grip scale |

Each reply is 48 rows per arm, `q_l_chunk` and `q_r_chunk`. The policy plays
them at 20 rows a second and asks for the next chunk early, on a worker thread,
when `client.refill_due()` says so. Rows that the round trip used up are skipped.
Each tick moves the commanded target at most `DREAM_MAX_JOINT_RAD_S / fps` per
joint and 5% of the grip range per row, and keeps every joint inside the Metal
soft limits. While the newest observation is older than 0.2 s, or no row is due,
it sends nothing and the arms hold.

Every handoff to Dream-1 starts a new episode: `reset_episode()`, an empty frame
window, and a ramp from where the arms are. `--dry-run` prints the targets
instead of sending them; try it first. Set the API key in
`~/.dreamlabs/credentials` as Dream Labs' Getting started describes. A model
that is starting answers `pool_warming`, and the policy waits and asks again.

## Train and extend

[`train.py`](../src/policies/act/train.py) runs LeRobot’s ACT trainer on the converted
dataset. [`skypilot.yaml`](../src/policies/act/skypilot.yaml) is a cloud training
example; customize its cloud resources and storage mount before use.

To add another model, keep the Portal connection, observation anchor, handoff
checks, and field-order validation. Replace the model loading and preprocessing
with the new model’s requirements. The robot and recorder need no model-specific
changes.

Next: [07. Running the rig](07-running-the-rig.md).
