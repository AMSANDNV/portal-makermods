# 3. Train ACT and improve it with corrections

Start with the dataset from [your first recording](01-first-recording.md#convert-and-inspect).
If you also collected Dream-1 corrections, include those sessions when converting.
ACT learns from the actions you keep, so discard takes you would not want it to copy.
Training requires a CUDA machine; copy the dataset there if it is on another host.
Run commands from the repository root.

## Train ACT

On a CUDA training machine:

```bash
DATASET_REPO_ID=local/portal-makermods \
DATASET_ROOT=data/local/portal-makermods \
OUTPUT_DIR=outputs/my-act \
NUM_STEPS=20000 \
BATCH_SIZE=64 \
uv run --only-group train python src/policies/act/train.py
```

For a hosted dataset, set `DATASET_REPO_ID` and omit `DATASET_ROOT`. Training
saves checkpoints every quarter of the requested steps, with a minimum interval
of 1,000 steps. The command above ends at
`outputs/my-act/checkpoints/020000/pretrained_model`.

The wrapper resumes the latest checkpoint with saved configuration and training
weights, processor configuration, and optimizer/RNG state. It refuses an existing output directory without such a checkpoint.
Use a new `OUTPUT_DIR` for a new experiment. `DEVICE` defaults to `cuda`;
`NUM_WORKERS` defaults to `16`. `LR` and `CHUNK_SIZE` control the ACT optimizer
and action horizon. Video decoding uses PyAV by default; set `VIDEO_BACKEND`
to use another installed LeRobot backend.

For cloud training, edit the resources and storage mount in
[`src/policies/act/skypilot.yaml`](../src/policies/act/skypilot.yaml), then launch:

```bash
sky launch src/policies/act/skypilot.yaml -e DATASET_REPO_ID=you/your-dataset
```

The example uses a Nebius H100 and an `/outputs` object-storage mount. Change
the bucket and output directory for your own account and experiment.

## Run ACT and collect corrections

Start the robot, recorder, and teleoperator again. On the inference host, make
the checkpoint and training dataset metadata available, then run:

```bash
uv run --group train python src/policies/act/inference.py \
  --checkpoint outputs/my-act/checkpoints/020000/pretrained_model \
  --dataset-root data/local/portal-makermods
```

ACT joins as `policy-act` and waits for control. It uses CUDA, MPS, or CPU in
that order of availability. Keep only ACT connected as a policy for these steps; stop Dream-1 if it is
still running. Check the selected identity after pressing `c`. On the teleoperator:

1. Press `c` to select the human and position the arms.
2. Press `r` to start a take.
3. Press `c` to hand control to ACT.
4. Press `c` when you need to correct it with the leader arms.
5. Press `c` to let ACT continue, or `r` to end the take.

Recording spans the handoffs. Move each leader near its follower’s current pose
before taking over. The command ramp limits target changes, but does not replace
careful operation around the arm.

After stopping the recorder, convert the combined sessions into a new dataset:

```bash
uv run livekit-portal convert data/sessions --output lerobotv3 --repo-id local/portal-makermods-v2
```

Train on that dataset with a new output directory, then repeat the policy and
correction cycle. To rebuild an existing dataset root, conversion requires
`--overwrite`; it does not append to a non-empty root.

## How the ACT operator works

The rest of this chapter explains [`inference.py`](../src/policies/act/inference.py).
You can return to it after completing your first training and correction cycle.

### Load the training contract

The dataset root supplies `meta/info.json`. State and action names are read in
training order and checked against `portal.yaml`. Do not alphabetize joint
names: normalization vectors and output tensors use the dataset’s order.

Camera names come from the checkpoint’s input features, and each must be a
track in `portal.yaml`.

### Follow one inference step

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

### Chunks and handoff

ACT manages its action chunks internally; Portal receives one flat action per
tick. Temporal ensembling is enabled by default with coefficient `0.01`.
The example replans each tick and resets the ensemble after a handoff.

The policy starts without control. Press `c` in teleop to select it. When control
returns after a human correction, the policy resets its buffered actions before
continuing. There is no separate SPACE engagement key or automatic claim.

## Two policies, one interface

Dream-1 ([chapter 2](02-dream-1.md)) and ACT both join as Portal operators and
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
it with the leaders whenever it slips (chapters 7 and 8). Every take records
Dream-1's motion and your fixes in one episode. Convert those takes and train
ACT on them, and ACT becomes a small local student of Dream-1 on your task,
already including your corrections. From then on, run ACT and keep correcting
it; each round of corrections is the next training set.

Discard (`[`) takes where Dream-1 did something you would not want copied,
and keep the cameras and the task prompt the same across both policies: ACT
only knows the views it was trained on.

## Add another policy

[`train.py`](../src/policies/act/train.py) runs LeRobot’s ACT trainer on the converted
dataset. [`skypilot.yaml`](../src/policies/act/skypilot.yaml) is a cloud training
example; customize its cloud resources and storage mount before use.

To add another model, keep the Portal connection, observation anchor, handoff
checks, and field-order validation. Replace the model loading and preprocessing
with the new model’s requirements. The robot and recorder need no model-specific
changes.

---

Previous: [2. A policy to interrupt: Dream-1](02-dream-1.md) · [Tutorial index](../README.md#the-tutorial) · Next: [4. The wire contract](04-wire-contract.md)
