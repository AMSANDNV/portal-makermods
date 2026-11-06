# 1. Set up the rig

You will connect the arms and cameras, start the three processes, and check
that the followers track the leaders. Run every command from the repository root.

The walkthrough uses two Linux hosts: a **robot host** for the followers,
cameras, and recorder, and a **teleop host** for the leaders. Both need Python
3.12, [`uv`](https://docs.astral.sh/uv/), and the same version of this
repository, and both must reach one LiveKit server. Other layouts are in the
[reference](09-reference.md#other-host-layouts).

## 1. Prepare the hardware

1. Mount two Metal followers, two Metal leaders, and three USB cameras:
   overhead, left wrist, and right wrist. Left and right are as the robot sees
   them, standing behind the arms.
2. Connect one USB-CAN adapter per arm.
3. Rest the followers where they can go limp, such as folded on their stops.
   The robot records this pose when it starts and parks the arms there. The
   arms have no brakes: support any arm you power off by hand.
4. In [MakerMods Lab](https://github.com/makermods-robotics/makermodslab),
   check the joint zeros and measure each gripper motor's angle with the jaws
   fully closed and fully open. Then **close MakerMods Lab**: it holds the
   adapters while it runs.

## 2. Install

On **each host**:

```bash
uv sync
cp .env.example .env
```

Fill in `.env` on both hosts with the same server and room:

```dotenv
LIVEKIT_URL=wss://YOUR_LIVEKIT_HOST
LIVEKIT_API_KEY=YOUR_API_KEY
LIVEKIT_API_SECRET=YOUR_API_SECRET
LIVEKIT_ROOM=portal-makermods
PORTAL_DATA_TASKS=fold the towel
PORTAL_HOTKEYS_GLOBAL=0
METAL_MAX_STEP_RAD=0.02
```

The last three lines set this walkthrough's task, keys read from the terminal,
and a gentle speed cap for bring-up. A `.env.local`, if present, overrides `.env`.

## 3. Name the arm adapters

Each host needs its two adapters in `.env`. Find them with the adapters
plugged in:

```bash
ls -l /dev/serial/by-id/      # serial (SLCAN) adapters
ip -br link show type can     # candleLight (gs_usb) adapters
```

- **Serial adapters** appear under `/dev/serial/by-id/`. Use those full paths.
  On Ubuntu, run `sudo usermod -aG dialout "$USER"` once and log in again.
- **candleLight adapters** appear as `can0`, `can1`, .... Bring each one up at
  1 Mbps after every boot or replug:

  ```bash
  sudo ip link set can0 up type can bitrate 1000000
  sudo ip link set can1 up type can bitrate 1000000
  ```

  If `lsusb` lists a `candleLight USB to CAN adapter` but no `canN` appears, see
  [CAN adapters](09-reference.md#can-adapters).

Set the robot host's followers, guessing left and right for now:

```dotenv
METAL_FOLLOWER_LEFT_CAN=can1
METAL_FOLLOWER_RIGHT_CAN=can0
```

and the teleop host's leaders the same way with `METAL_LEADER_LEFT_CAN` and
`METAL_LEADER_RIGHT_CAN`.

Then add the gripper angles you measured, on each host for its own arms:

```dotenv
METAL_GRIPPER_CLOSED_DEG=0
METAL_LEFT_GRIPPER_OPEN_DEG=115
METAL_RIGHT_GRIPPER_OPEN_DEG=114
```

These are one rig's numbers; use yours. If an open angle is past the real stop,
the robot logs `the gripper stops opening at N deg`: set that arm's angle to N.

**Check left and right before any arm is energized.** With the arms powered, run
on the robot host:

```bash
uv run robot --check-arms
```

It reads the followers with their motors off and prints each one's joints
twice a second. Move one arm by hand and watch which line changes. If it is the
wrong side, swap the two values in `.env`. Stop it with Ctrl-C.

## 4. Find the cameras

On the robot host:

```bash
sudo apt install v4l-utils
v4l2-ctl --list-devices
```

Each USB camera lists two devices; use the first. Set:

```dotenv
METAL_CAM_OVERHEAD=/dev/video2
METAL_CAM_LEFT_WRIST=/dev/video4
METAL_CAM_RIGHT_WRIST=/dev/video6
```

If the views come out swapped, you will see it in step 5; swap the values and restart.

## 5. Start the three processes

Start them in this order, each in its own terminal. The robot comes first
because the others wait for its clock.

1. **Robot host:** `uv run robot`. The followers power up and hold where they
   rest; wait for `connected`.
2. **Robot host:** `uv run recorder`. Wait for its `recording to ...` line.
3. **Teleop host:** `uv run teleoperator`. Its window shows the three views.

To watch the room from any machine with the repository and `.env`, run
`uv run recorder --observe-only`. It opens a Rerun window with the cameras,
both arms' joints, the actions, and connection metrics, and writes nothing.
Each program opens a window of its own, so this can run beside an operator on the same machine.

## 6. Check human control

In the teleop terminal:

1. Hold the leaders near the followers' poses.
2. Press `c` and confirm the teleoperator is selected.
3. Move the leaders gently; both followers should track them. If moving the
   left leader moves the right follower, swap the teleop host's two
   `METAL_LEADER_*_CAN` values and restart teleop.
4. Place the towel in the workspace and bring the arms to the starting pose.

Leave everything running and continue to
**[2. Run Dream-1 and record corrections](02-dream-1.md)**.

## Pausing and stopping

- **Pause:** press `p` in teleop. The robot takes control back, walks the
  followers to the pose they started in, and switches them off; video and
  joint state keep flowing. Press `p` again to power them up where they rest.
- **Stop:** press `x` in teleop, stop the recorder with Ctrl-C and wait for it
  to exit, then stop the robot with Ctrl-C. The robot parks before it exits, so
  the next start finds the arms off at rest.

To record without a policy, follow the
[human-only alternative](09-reference.md#human-only-demonstrations).

---

Previous: [0. What is Portal?](00-what-is-portal.md) · [Tutorial index](../README.md#the-tutorial) · Next: [2. Run Dream-1 and record corrections](02-dream-1.md)
