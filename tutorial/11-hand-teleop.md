# 11. Teleop with your hands and a webcam

`src/hand_teleop.py` is an operator like the leader-arm teleop, driven by a laptop webcam instead.
MediaPipe tracks up to two hands; your right hand drives the right arm, your left hand the left.

- **Grip:** curl middle, ring and pinky all the way into your palm, leaving thumb and index as a
  gripper. After three frames the arm follows that hand. A half curl does nothing.
- **Gripper:** the gap between thumb and index tip sets it: 3 cm or less closed, 9 cm or more open.
- **Let go:** lift any of the three fingers out of your palm. Nothing more is sent for that arm, and
  the robot holds it.
- **Motion:** the arm follows how far your hand has moved since you gripped, never where your hand
  is. To move farther than your reach, let go, move back, and grip again, as with a mouse.

Picture yourself standing behind the arms, facing the workspace: pushing your hand toward the
screen reaches forward, your right is the robot's right, up is up. Depth comes from how large your
hand looks, so forward and back are a little noisier than the other directions.

## 1. Try it at your desk

```bash
uv run --group hands hand-teleop --offline
```

No robot or LiveKit: both arms start at the IK's rest pose and nothing is sent. The first run
downloads MediaPipe's hand model to `~/.cache/portal-makermods/` and compiles the IK (several
seconds; cached after). On macOS, allow camera access for the terminal.

The rerun window mirrors your webcam. A hand's skeleton is yellow while seen, green while it holds
its arm, and the label above it shows how far that arm's target has moved (mm forward, right, up)
and its gripper. Check the directions here first: move a gripping hand to your right and its label
should read `right` growing. Until a hand grips, its label shows the three fingers' curl; all three
must drop below 1.0, which takes the fingertips folded into the palm.

## 2. Drive the robot

With the robot running (chapter 1), on any machine with a webcam and the repo's `.env`:

```bash
uv run --group hands hand-teleop
```

Its window shows the robot's cameras beside the webcam, apart from any window the teleoperator or
`recorder --observe-only` has open. Press `c` to take control (and again to release it), as in a
policy; `p` parks, `x` quits. With [chapter 10](10-voice-tasks.md)'s voice agent and
`dream-1-tasks` running, saying "hand teleop" hands it control too, and "back to Dream-1" takes it back.
Gripping does nothing until this operator has control and a fresh robot view. Each time control
comes back, the next grip starts from where the arms are.

Safety, on top of the robot's own step limiter and soft limits:

- The target moves at most `HAND_MAX_SPEED_M_S` and the joints `HAND_MAX_JOINT_RAD_S`.
- A floor at `HAND_TABLE_Z_M` (each arm's base frame; 0 is the underside of its base) keeps the
  tool point and the whole arm above the table. Pushing into it slides along it.
- An arm lets go when its hand jumps 8 cm in one frame (a glitch, or two hands swapped), when the
  webcam stalls, or when the robot view goes stale.
- There is no arm-to-arm guard: keep the arms' paths apart yourself.

The gripper takes over softly: on a new grip it holds until your pinch reaches its opening, so
gripping again with an open hand does not drop what the arm holds. The label says so while it waits.

## 3. Settings

| Setting | Default | Purpose |
|---|---|---|
| `HAND_CAMERA` | `0` | Webcam index (`/dev/videoN` on Linux), or a video file played in real time |
| `HAND_ROTATE` / `--rotate` | `off` | `off` keeps the gripper's orientation; `yaw` turns it about vertical; `full` follows the hand's turn |
| `HAND_SCALE` | `1.0` | Arm metres per hand metre |
| `HAND_LEFT_YAW_DEG`, `HAND_RIGHT_YAW_DEG` | `0` | How far an arm's base is turned left of facing the workspace |
| `HAND_TABLE_Z_M` | `0.0` | The floor under each arm |
| `HAND_MAX_SPEED_M_S` | `0.5` | Target speed limit |
| `HAND_MAX_JOINT_RAD_S` | `1.5` | Joint speed limit in the IK |
| `HAND_CAMERA_HFOV_DEG` | `70` | The webcam's field of view; only scales forward and back |
| `HAND_SWAP_SIDES` | `0` | `1` for a webcam that mirrors its image (your hands drive the wrong arms) |
| `HAND_TELEOP_IDENTITY` | `hand-teleop` | Set `teleoperator` to stand in for the leader arms (not alongside them) |
| `HAND_MODEL_PATH` | | A local `hand_landmarker.task` instead of the download |

## How it works

`src/utils/hands.py` holds the pieces, all testable without a camera (`test/test_hands.py`):
the clutch (finger curl as tip-to-wrist over knuckle-to-wrist: about 1.8 straight, below 1.0 once the
tip folds back past its knuckle into the palm; closing below 1.0 and opening above 1.15), the palm's position (MediaPipe's metric world
landmarks against their pixel spread give the depth), a 1€ filter, the mapping into each arm's base
frame, and the IK: `dream_ik`'s `ChunkIK` for the Metal arm, one tick per call, the same solver
Dream-1's client uses (chapter 2).

MediaPipe labels each hand as it appears in the image it is given, so detection runs on the raw
webcam frame and only the preview is mirrored.

The operator's actions are ordinary Portal actions: the recorder archives them during a take, but
takes are still marked from the leader-arm teleop (`r`).
