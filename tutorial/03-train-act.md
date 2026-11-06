# 3. Train ACT and collect corrections

Use the corrected Dream-1 takes from
[chapter 2](02-dream-1.md#4-convert-and-inspect). ACT learns from the actions
in the takes you kept, including the policy motion and your corrections.

Run commands from the repository root. Keep the task and camera positions the
same as during collection.

## 1. Prepare the training machine

Use a CUDA machine with Python 3.12, `uv`, and this repository checked out.
Copy the **entire** `data/local/portal-makermods` directory from the robot host
to the same relative path on the training machine. Confirm it contains
`meta/info.json`, data, and videos.

If the robot host has CUDA, train there and skip the copy.

## 2. Train the first policy

On the training machine:

```bash
DATASET_REPO_ID=local/portal-makermods \
DATASET_ROOT=data/local/portal-makermods \
OUTPUT_DIR=outputs/my-act \
NUM_STEPS=20000 \
BATCH_SIZE=64 \
uv run --only-group train python src/policies/act/train.py
```

Wait for training to finish. The final model is saved at:

```text
outputs/my-act/checkpoints/020000/pretrained_model
```

If training is interrupted, rerun the same command to resume from the latest
complete checkpoint. For a new experiment, choose a new `OUTPUT_DIR`.
This 20,000-step run is a starting point; evaluate it before collecting the next round.

## 3. Start ACT on the robot host

Copy the entire `pretrained_model` directory back to the robot host at the path
shown above. Keep the training dataset at `data/local/portal-makermods`; ACT
reads its metadata to recover the training field order.

Stop Dream-1 if it is running. Start these commands in separate terminals,
waiting for the robot to connect before starting the others:

1. Robot host: `uv run robot`
2. Robot host: `uv run recorder`
3. Teleop host: `uv run teleoperator`
4. Robot host:

   ```bash
   uv run act \
     --checkpoint outputs/my-act/checkpoints/020000/pretrained_model \
     --dataset-root data/local/portal-makermods \
     --task "fold the towel"
   ```

ACT joins as `policy-act` and waits for you to select it. It automatically uses
the available CUDA, MPS, or CPU device.

## 4. Record corrections

In the teleop terminal:

1. Hold the leaders near the followers, press `c`, and confirm the human is selected.
2. Reset the towel and arms, then press `r` to start a take.
3. Press `c` and confirm `policy-act` is selected.
4. When ACT needs help, bring the leaders near the followers, press `c`, and
   confirm the human is selected. Guide the leaders to correct the motion.
5. Press `c` to let ACT continue. Repeat step 4 when needed.
6. Press `r` to finish a successful take. Use `[` before ending a take to discard it.
7. Repeat steps 2–6 to collect more corrections.

After the final take, select the human. Stop ACT with Ctrl-C, quit teleop with
`x`, stop the recorder with Ctrl-C, then stop the robot with Ctrl-C: it parks the followers first (they walk back to
the pose they started in and switch off), then exits.
Wait for the recorder to exit before conversion.

## 5. Convert and retrain

On the robot host, convert all retained sessions into a **new** dataset:

```bash
uv run livekit-portal convert data/sessions --output lerobotv3 --dry-run
uv run livekit-portal convert data/sessions --output lerobotv3 --repo-id local/portal-makermods-v2
```

Copy `data/local/portal-makermods-v2` to the training machine, then run:

```bash
DATASET_REPO_ID=local/portal-makermods-v2 \
DATASET_ROOT=data/local/portal-makermods-v2 \
OUTPUT_DIR=outputs/my-act-v2 \
NUM_STEPS=20000 \
BATCH_SIZE=64 \
uv run --only-group train python src/policies/act/train.py
```

Repeat steps 3–4 with `outputs/my-act-v2/checkpoints/020000/pretrained_model`
and `data/local/portal-makermods-v2`. Use a new dataset ID and output directory
for each later round.

## How ACT uses Portal

[`run.py`](../src/policies/act/run.py) reads state and action names from
`meta/info.json` in training order and checks them against `portal.yaml`.
Checkpoint camera names must also exist in the schema. Do not sort the fields:
the model's tensors and normalization statistics depend on their original order.

While ACT has control, it packs the latest observation, runs inference on a
worker thread, and sends the action with that observation's timestamp:

```python
op.send_action(action, in_reply_to_ts_us=obs.timestamp_us)
```

Portal stamps the send time separately. If control changes during inference,
the result is discarded. ACT resets its action history when it regains control.

For cloud training and hosted datasets, see [the reference](09-reference.md#training-alternatives).

---

Previous: [2. Run Dream-1 and record corrections](02-dream-1.md) · [Tutorial index](../README.md#the-tutorial) · Next: [4. The wire contract](04-wire-contract.md)
