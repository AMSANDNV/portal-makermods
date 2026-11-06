# 9. Configuration and troubleshooting

Use this chapter when changing hosts, tuning settings, or diagnosing a problem.
For your first run, start with [rig setup](01-first-recording.md).

## Human-only demonstrations

If you prefer to train without Dream-1, complete [chapter 1](01-first-recording.md),
then use the running teleoperator and recorder:

1. Press `c` and confirm the human is selected.
2. Reset the towel and arms, then press `r` to begin a take.
3. Demonstrate the task with the leaders.
4. Press `r` to finish. For a failed attempt, press `[` while the take is open instead.
5. Repeat steps 2–4 to collect varied successful demonstrations.
6. Rest or support all arms, press `x` in teleop, and stop the recorder and robot
   with Ctrl-C. Wait for the recorder to exit.
7. Follow [chapter 2's conversion steps](02-dream-1.md#4-convert-and-inspect),
   then continue to [ACT training](03-train-act.md).

## Other host layouts

The walkthrough uses separate robot and teleop hosts. You can run all three
peers on one Linux machine if it has the required CAN adapters and cameras.
If leader and follower gripper measurements differ, give robot and teleop
separate working directories with their own `.env.local` values. Start each
process from its directory; relative output paths follow that directory.

You can also run the recorder or policy on another machine. Copy the repository,
run `uv sync`, and configure the same LiveKit server, room, and `portal.yaml`.
ACT needs its checkpoint and training dataset metadata on its inference host.

### Deploy to another host

```bash
./scripts/deploy_to_robot.sh robotuser@robot.local '~/workspace/portal-makermods'
```

Replace the host and destination path. This replaces source, `.env`, and
`.env.local` on the destination; datasets and training outputs are excluded.
Run `uv sync` there afterwards and check its hardware settings.

## Serial adapter setup

The default walkthrough uses SLCAN USB adapters directly. Dream's installed
`robots.robstride.open_can_bus()` opens `/dev/...` paths with python-can's
SLCAN backend at 1 Mbps. It also resolves symlinks when checking whether an
existing `slcand` process owns the device.

- **No unique `/dev/serial/by-id/` entries:** identify adapters one at a time
  with `ls -l /dev/serial/by-path/` and use those paths instead. Keep each adapter
  in the same physical USB port.
- **Permission denied:** on Ubuntu, add your user to `dialout` as shown in
  chapter 1, then log out and back in.
- **An existing `slcand` owns the adapter:** Dream uses that daemon's CAN link
  if it is already up. You can keep this working setup. To switch to direct
  serial access, stop robot software, support the arms, and stop the specific
  vendor service that owns the adapter before starting Portal.
- **Only `canN` devices appear:** use the SocketCAN alternative below.

## SocketCAN alternative

Existing rigs can keep their configured `canN` interfaces. Set each
`METAL_{FOLLOWER,LEADER}_{LEFT,RIGHT}_CAN` to the interface for that arm.
Dream expects these links to be up at 1 Mbps before Portal starts.

For **native SocketCAN** adapters, bring up each interface that is down
(replace `can0` with its actual name):

```bash
sudo ip link set can0 up type can bitrate 1000000
```

For **serial adapters managed by MakerMods' SDK**, use its adapter setup and
startup scripts instead. Its `start_can0.sh` creates a CAN link with `slcand`,
then brings it up; the command above does not replace that process. Follow
[MakerMods' CAN setup](https://github.com/makermods-robotics/metal-python-ros#can-setup)
after rebooting or reconnecting the adapter. Skip startup if your service
already brings the link up.

[Dream's quickstart](https://github.com/dreamlabs-co/dreamlabs#quick-guide)
also offers `dreamcli setup` and `dreamcli robot up` for its complete control
loop. This tutorial uses Dream's driver and model client inside Portal's loop,
so do not run that robot process alongside the Portal peers.

## Training alternatives

- **Hosted dataset:** add `--push` to conversion with your Hugging Face repo ID.
  For training, set `DATASET_REPO_ID` to that ID and omit `DATASET_ROOT`.
- **Cloud training:** adapt the resources and storage in
  [`skypilot.yaml`](../src/policies/act/skypilot.yaml), then launch it with
  `sky launch src/policies/act/skypilot.yaml -e DATASET_REPO_ID=you/your-dataset`.
- **Replace a converted dataset:** conversion supports `--overwrite`. Prefer a
  new repo ID for each round so earlier datasets remain available.

## Configuration

Copy `.env.example` for the settings needed to connect the rig. `src/utils/common.py`
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
| `METAL_GRIPPER_CLOSED_DEG`, `METAL_GRIPPER_OPEN_DEG` | — (required) | gripper motor angle, jaws closed / open; measure per arm. Per-side: `METAL_{LEFT,RIGHT}_GRIPPER_*_DEG` |
| `METAL_GRIPPER_HOLD_NM` | `0.5` | the gripper's holding torque on an object, N.m |
| `METAL_FOLLOWER_RIGHT_CAN`, `METAL_FOLLOWER_LEFT_CAN` | `can1`, `can3` (legacy fallback) | Set robot-host serial paths for the walkthrough |
| `METAL_LEADER_RIGHT_CAN`, `METAL_LEADER_LEFT_CAN` | `can0`, `can2` (legacy fallback) | Set teleop-host serial paths for the walkthrough |
| `METAL_CAM_OVERHEAD`, `METAL_CAM_LEFT_WRIST`, `METAL_CAM_RIGHT_WRIST` | `/dev/video0`, `/dev/video2`, `/dev/video4` | Linux camera device path. |
| `METAL_CAM_WIDTH` / `_HEIGHT` | `640` / `480` | a 16:9 mode such as 1280x720 suits Dream-1 better; it center-crops otherwise |
| `METAL_MAX_STEP_RAD` | `0.087` | maximum joint target step per command (about 5°); `0` disables the limit |

`METAL_MAX_STEP_RAD` limits target movement per command (0.087 rad × 30 Hz is
about 2.6 rad/s). Use a lower value for bring-up. Grippers are not ramped. The arm joints run impedance
control: ramping the target preserves torque, while a small tracking-error
clamp can prevent motion. Every command is also clamped to MakerMods' soft
limits, and the gripper commands to the driver's normalized opening range.

### Dream-1

| Variable | Default | Notes |
|---|---|---|
| `DREAM_API_KEY` | none | your Dream Labs API key; `~/.dreamlabs/credentials` also works |
| `DREAM_CHECKPOINT` | `dreamlabs/dream-1-beta` | a base model or your fine-tune |
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
- **Arm errors:** the robot's 5 s line names the arm. Check its configured adapter
  path, serial permissions, and power, then restart the robot. For SocketCAN,
  also check the link with `ip link`. `dreamlabs is not
  installed`: run `uv sync`. An arm that refuses to open for its gripper needs
  `METAL_GRIPPER_{CLOSED,OPEN}_DEG` set; see
  [hardware setup](01-first-recording.md#1-prepare-the-hardware).
- **Dream-1 refuses or stalls:** for `DreamAuthError`, check `DREAM_API_KEY` in
  `.env`; `pool_warming` means the model is starting. It
  sends nothing until all three cameras publish.
- **Low frame rate:** compare device and tick times in robot diagnostics. Lower
  the requested rate or camera resolution if the hardware cannot keep up.
- **Archive writer drops:** lower JPEG quality or resolution. Check disk capacity.

---

Previous: [8. Handoff](08-handoff.md) · [Tutorial index](../README.md#the-tutorial)
