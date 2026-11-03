# 7. Running the rig

This chapter runs the full cycle: collect demonstrations, train ACT, run the
policy, and record human corrections for the next training run.

## Set up

You need two MakerMods Metal followers, two Metal leaders, three USB cameras
(overhead, left wrist, right wrist), a LiveKit server, Python 3.12, and `uv`.
Run the robot on the followers' host, teleop on the leaders' host (the same
machine works), and the recorder wherever you have disk space. Policies can run
on any machine in the same room.

From the repository root on each host:

```bash
cp .env.example .env
uv sync
```

Fill in the LiveKit credentials and room. Keep `portal.yaml` identical on all
hosts. Values in `.env.local` override `.env`; the deploy script copies both.

### The Metal SDK (robot and teleop hosts)

The arms are driven by MakerMods' `metal_sdk`, from
[metal-python-ros](https://github.com/makermods-robotics/metal-python-ros). It
targets Ubuntu 22.04 with ROS 2 Humble, because the native library links ROS's
KDL. Follow that repository's README to install its dependencies, set up the CAN
adapters, and build the native library (`bash metal_sdk/build_metal_sdk.sh`). That
script installs the binding into the system Python 3.10, so install it into
this project's environment as well:

```bash
git clone https://github.com/makermods-robotics/metal-python-ros ~/metal-python-ros
# ... the repo's dependency and build steps ...
uv pip install ~/metal-python-ros/metal_sdk/binding
uv run python -c "from metal_sdk import MetalSDKInterface; print('ok')"
```

The build script must have run first: it copies the native library into the
package the binding installs.
Rerun the install after every `uv sync`, which removes packages the lockfile
does not list. `METAL_URDF` defaults to that clone's
`metal_sdk/example/urdf/metal_with_gripper.urdf`.

### CAN links and cameras

The defaults follow the vendor's two-pair layout
(`metal_controller/config/two_master_slave.yaml`): right leader `can0`, right
follower `can1`, left leader `can2`, left follower `can3`. Bring them up with the
repo's `metal_ros2/can_scripts/start_canN.sh`, and set
`METAL_{FOLLOWER,LEADER}_{LEFT,RIGHT}_CAN` if yours differ. Left and right are
as the robot sees them, standing behind the arms facing the workspace.

Cameras default to `/dev/video0` (overhead), `/dev/video2` (left wrist), and
`/dev/video4` (right wrist); set `METAL_CAM_OVERHEAD`, `METAL_CAM_LEFT_WRIST`, or
`METAL_CAM_RIGHT_WRIST` if yours differ. Use `v4l2-ctl --list-devices` to find
them.

There is no calibration step in this project: the Metal motors store their own
zero. If a joint reads wrong, re-zero it with the vendor's tools.

### Before power-on

The leaders float in gravity compensation and the followers hold their pose
under power. Nothing in this project switches the motors off, because a Metal
arm has no brakes and drops when disabled. Rest or support the arms before you
stop `uv run robot` or `uv run teleoperator`, and keep a hand on the power
switch. For the first run, lower `METAL_MAX_STEP_RAD` (for example `0.02`).

## Collect demonstrations

Run these in separate terminals, on the appropriate hosts. Start the robot
first: the recorder and operators wait for its clock when they connect, and give
up after 10 s without it.

```bash
uv run robot
uv run recorder
uv run teleoperator
```

Enter the task description when teleop starts. Wait for live video and the
recorder’s output path, then:

```text
c       select the human operator
        move to the starting pose
r       begin a take
        demonstrate the task
r       end the take
        repeat, or use [ to discard a bad take
x       quit teleop
```

Use `1`–`9` to switch tasks between takes. Stop the recorder with Ctrl-C after
collection so it flushes the archive. Files are written under `data/sessions`
unless `PORTAL_DATA_RRD_DIR` overrides it.

Global hotkeys require desktop permissions. If they do not respond, focus the
teleop terminal and press them there. `NOT RECORDED` means a boundary did not
reach an observer.

## Convert and inspect

```bash
uv run livekit-portal convert data/sessions --output lerobotv3 --dry-run
uv run livekit-portal convert data/sessions --output lerobotv3 --repo-id local/portal-makermods
```

The dataset is written under `data/local/portal-makermods` by default. Discarded and
unclosed takes are excluded. Keep the original `.rrd` files so you can inspect
them in Rerun or change conversion settings later.

For a dataset hosted on Hugging Face, use your repo ID and add `--push`.

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
that order of availability. On the teleoperator:

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

## Run Dream-1 zero-shot

Dream-1 needs no dataset. On any host with internet access, save your Dream Labs
API key as their [Getting started](https://platform.dreamlabs.ai/api-documentation/)
describes (`~/.dreamlabs/credentials`), then, with the robot, recorder, and
teleoperator running:

```bash
uv sync --group dream
uv run --group dream python src/policies/dream-1/inference.py --task "fold the towel" --dry-run
```

It joins as `policy-dream-1`. The dry run prints its targets once a second
without moving the arms; check that they are near the arms' pose and move the
right way. Then run without `--dry-run`, and hand it control with `c` as with
ACT. The first request to a cold model may wait in `pool_warming` for a while.
Takes recorded with Dream-1 and your corrections convert and train like any other.

## Deploy and troubleshoot

```bash
./scripts/deploy_to_robot.sh robotuser@robot.local '~/workspace/portal-makermods'
```

The script syncs source, `.env`, and `.env.local`, replacing the robot’s copies;
datasets, checkpoints, and training outputs are excluded. Run `uv sync` on the destination afterwards.

The processes report throughput, clock synchronization, and recording counts.
For hardware faults, missing hotkeys, or the known timestamp regression after
stalls in Portal 0.3.0rc1, see [Troubleshooting](#troubleshooting).


## Configuration

Copy `.env.example` for the settings needed to connect the rig. `src/_common.py`
loads repo-root `.env`, then `.env.local`, then the same files from the working
directory. `.env` fills unset values; `.env.local` overrides them, including
shell values. Relative output paths use the working directory.

| Variable | Default | Purpose |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_ROOM` | required | Same server and room for all peers |
| `PORTAL_FPS` | `portal.yaml` fps | Robot, human, and policy tick rate |
| `LOG_LEVEL` | `INFO` | Python logs |
| `RUST_LOG` | warn with noisy modules suppressed | Set in the shell before startup for Portal diagnostics |
| `PORTAL_HOTKEYS_GLOBAL` | `1` | Set `0` for terminal-only input |
| `PORTAL_DATA_TASK` | `MakerMods Metal bimanual manipulation` | Default task description |
| `PORTAL_DATA_TASKS` | unset | Semicolon-separated tasks; skips prompts, maximum nine |
| `PORTAL_DATA_RRD_DIR` | `data/sessions` | Archive output directory |
| `PORTAL_DATA_RRD_QUALITY` | `95` | JPEG quality; `0` stores decoded frames uncompressed |

### Hardware

| Variable | Default | Notes |
|---|---|---|
| `METAL_URDF` | `~/metal-python-ros/metal_sdk/example/urdf/metal_with_gripper.urdf` | the SDK's arm model |
| `METAL_END_TYPE` | `3` | SDK `arm_end_type`: gripper and teaching pendant, as the vendor's launches use |
| `METAL_FOLLOWER_RIGHT_CAN`, `METAL_FOLLOWER_LEFT_CAN` | `can1`, `can3` | robot host |
| `METAL_LEADER_RIGHT_CAN`, `METAL_LEADER_LEFT_CAN` | `can0`, `can2` | teleop host |
| `METAL_CAM_OVERHEAD`, `METAL_CAM_LEFT_WRIST`, `METAL_CAM_RIGHT_WRIST` | `/dev/video0`, `/dev/video2`, `/dev/video4` | Linux: `/dev/videoN`. macOS: an integer index. |
| `METAL_CAM_WIDTH` / `_HEIGHT` | `640` / `480` | a 16:9 mode such as 1280x720 suits Dream-1 better; it center-crops otherwise |
| `METAL_CAM_MAX_FRAME_AGE_MS` | `500` | how old a buffered frame may be before the tick logs it stale and serves it anyway |
| `METAL_BUS_ERRORS_BEFORE_RECONNECT` | `5` | consecutive failures of one arm before its SDK interface is reopened |
| `METAL_BUS_RECONNECT_INTERVAL_S` | `2.0` | minimum seconds between reopen attempts |
| `METAL_MAX_STEP_RAD` | `0.087` | maximum joint target step per command (about 5°); `0` disables the limit |
| `METAL_MAX_LAG_RAD` | `0.5` | how far the target may lead a joint that cannot follow |
| `METAL_UNCAPPED_JOINTS` | `left_gripper,right_gripper` | fields the ramp skips |

`METAL_MAX_STEP_RAD` limits target movement per command (0.087 rad × 30 Hz is
about 2.6 rad/s). Use a lower value for bring-up. The arm joints run impedance
control: ramping the target preserves torque, while a small tracking-error
clamp can prevent motion. Every command is also clamped to MakerMods' soft
limits, and the grippers to their 0–80 mm stroke.

### Dream-1

| Variable | Default | Notes |
|---|---|---|
| `DREAM_CHECKPOINT` | `dreamlabs/dream-1-beta` | a base model or your fine-tune |
| `DREAM_CAMERAS` | `overhead=overhead_camera,wrist_l=left_wrist_camera,wrist_r=right_wrist_camera` | role to track; an unmapped role gets black frames |
| `DREAM_MAX_JOINT_RAD_S` | `0.5` | cap on commanded joint speed; also the ramp in after a handoff |

### Training

| Training variable | Default | Purpose |
|---|---|---|
| `DATASET_REPO_ID` | `local/portal-makermods` | Local or Hugging Face dataset ID |
| `DATASET_ROOT` | unset | Local dataset directory; otherwise load by repo ID |
| `OUTPUT_DIR` | `outputs/<generated run name>` | Checkpoints and training output |
| `RUN_NAME` | `act-metal-pair` | Prefix for the generated name, followed by steps, batch, LR, and chunk size |
| `NUM_STEPS` | `100000` | Training steps |
| `BATCH_SIZE` | `64` | Training batch size |
| `LR` | `5e-5` | ACT optimizer learning rate |
| `CHUNK_SIZE` | `50` | Predicted and executed action horizon |
| `DEVICE` | `cuda` | Training device |
| `NUM_WORKERS` | `16` | Dataset workers |
| `VIDEO_BACKEND` | `pyav` | Dataset video decoder; use `torchcodec` with compatible Torch/FFmpeg libraries |

Inference requires `--checkpoint` and `--dataset-root`. `--task` defaults to
`PORTAL_DATA_TASK`, then `MakerMods Metal bimanual manipulation`.
It reads LiveKit settings and `PORTAL_FPS` as the teleoperator does, selects
CUDA/MPS/CPU automatically, and waits for the `c` handoff before producing actions.

## Troubleshooting

- **No motion:** press `c` and check the active identity. Confirm the server and
  room match on all hosts. A failed claim reports its RPC error.
- **No hotkeys:** focus the teleop terminal. Global keys require X11 on Linux or
  Accessibility permission for the terminal on macOS. Relaunch after granting it.
- **No observations in ACT:** every declared camera must publish; the checkpoint
  must use cameras available in `portal.yaml`.
- **Arm errors:** the robot's 5 s line names the arm. Check its CAN link
  (`ip link`), adapter, and power. A reopen re-energizes the arm; keep the
  leaders still during recovery. `metal_sdk is not installed`: see
  [The Metal SDK](#the-metal-sdk-robot-and-teleop-hosts).
- **Dream-1 refuses or stalls:** a `DreamAuthError` means the key in
  `~/.dreamlabs/credentials`; `pool_warming` means the model is starting. A
  missing camera track stops it sending anything: check `DREAM_CAMERAS`.
- **Low frame rate:** compare device and tick times in robot diagnostics. Lower
  the requested rate or camera resolution if the hardware cannot keep up.
- **Archive writer drops:** lower JPEG quality or resolution. Check disk capacity.
- **Wrong pairing just after a stall:** Portal 0.3.0rc1 has a known video timestamp
  regression when ticks arrive very close together. The `stalls` degradation test
  can detect it; the test README records the limitation.

Back to [the tutorial](../README.md#the-tutorial).
