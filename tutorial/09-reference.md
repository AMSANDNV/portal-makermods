# 9. Configuration and troubleshooting

Use this chapter when changing hosts, tuning settings, or diagnosing a problem.
For your first run, start with [your first recording](01-first-recording.md).

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
| `METAL_URDF` | `~/metal-python-ros/metal_sdk/example/urdf/metal_with_gripper.urdf` | the SDK's arm model |
| `METAL_FOLLOWER_RIGHT_CAN`, `METAL_FOLLOWER_LEFT_CAN` | `can1`, `can3` | robot host |
| `METAL_LEADER_RIGHT_CAN`, `METAL_LEADER_LEFT_CAN` | `can0`, `can2` | teleop host |
| `METAL_CAM_OVERHEAD`, `METAL_CAM_LEFT_WRIST`, `METAL_CAM_RIGHT_WRIST` | `/dev/video0`, `/dev/video2`, `/dev/video4` | Linux: `/dev/videoN`. macOS: an integer index. |
| `METAL_CAM_WIDTH` / `_HEIGHT` | `640` / `480` | a 16:9 mode such as 1280x720 suits Dream-1 better; it center-crops otherwise |
| `METAL_MAX_STEP_RAD` | `0.087` | maximum joint target step per command (about 5°); `0` disables the limit |

`METAL_MAX_STEP_RAD` limits target movement per command (0.087 rad × 30 Hz is
about 2.6 rad/s). Use a lower value for bring-up. Grippers are not ramped. The arm joints run impedance
control: ramping the target preserves torque, while a small tracking-error
clamp can prevent motion. Every command is also clamped to MakerMods' soft
limits, and the grippers to their 0–80 mm stroke.

### Dream-1

| Variable | Default | Notes |
|---|---|---|
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
- **Arm errors:** the robot's 5 s line names the arm. Check its CAN link
  (`ip link`), adapter, and power, then restart the robot. `metal_sdk is not installed`: see
  [The Metal SDK](01-first-recording.md#the-metal-sdk-robot-and-teleop-hosts).
- **Dream-1 refuses or stalls:** a `DreamAuthError` means the key in
  `~/.dreamlabs/credentials`; `pool_warming` means the model is starting. It
  sends nothing until all three cameras publish.
- **Low frame rate:** compare device and tick times in robot diagnostics. Lower
  the requested rate or camera resolution if the hardware cannot keep up.
- **Archive writer drops:** lower JPEG quality or resolution. Check disk capacity.
- **Wrong pairing just after a stall:** Portal 0.3.0rc1 has a known video timestamp
  regression when ticks arrive very close together. The `stalls` degradation test
  can detect it; the test README records the limitation.

---

Previous: [8. Handoff](08-handoff.md) · [Tutorial index](../README.md#the-tutorial)
