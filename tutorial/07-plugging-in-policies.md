# 7. Plugging in a policy

A policy uses the same `Operator` interface as the human. Chapter 4 ran
Dream-1, which needs no data; this chapter adds ACT, trained on your own takes,
and shows how the two work together.
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

## Two policies, one interface

Dream-1 ([chapter 4](04-dream-1.md)) and ACT both join as Portal operators and
send the same fourteen-value actions, so the robot, the recorder, and the `c`
handoff treat them the same. Where they differ is where they come from:

| | Dream-1 | ACT |
|---|---|---|
| Needs data | no: a task prompt | your takes |
| Runs | on Dream Labs' servers | locally, on GPU, MPS, or CPU |
| Outputs | 48-row chunks of hand poses, solved to joints by the `dream` client | joint targets every tick |
| Strength | works on day one, on new tasks | fast, small, specialised to your task and views |

## Distill Dream-1 into ACT

The two fit together. Dream-1 is the teacher: run it on your task and correct
it with the leaders whenever it slips (chapters 5 and 6). Every take records
Dream-1's motion and your fixes in one episode. Convert those takes and train
ACT on them, and ACT becomes a small local student of Dream-1 on your task,
already including your corrections. From then on, run ACT and keep correcting
it; each round of corrections is the next training set.

Discard (`[`) takes where Dream-1 did something you would not want copied,
and keep the cameras and the task prompt the same across both policies: ACT
only knows the views it was trained on.

## Train and extend

[`train.py`](../src/policies/act/train.py) runs LeRobot’s ACT trainer on the converted
dataset. [`skypilot.yaml`](../src/policies/act/skypilot.yaml) is a cloud training
example; customize its cloud resources and storage mount before use.

To add another model, keep the Portal connection, observation anchor, handoff
checks, and field-order validation. Replace the model loading and preprocessing
with the new model’s requirements. The robot and recorder need no model-specific
changes.

Next: [08. Running the rig](08-running-the-rig.md).
