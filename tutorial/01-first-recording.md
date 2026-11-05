# 1. Set up the rig

You will connect the arms and cameras and check human control before running
Dream-1. Run all commands from the repository root.

Use two Linux hosts for this walkthrough: a **robot host** for the followers,
cameras, and recorder, and a **teleop host** for the leaders. Keep the same
repository version and `portal.yaml` on both. Other layouts are covered in the
[reference](09-reference.md#other-host-layouts).

## 1. Prepare the hardware

1. Mount two Metal followers, two Metal leaders, and three USB cameras:
   overhead, left wrist, and right wrist. Left and right are from the robot's view.
2. Connect one CAN adapter per arm. Use these interface names:

   | Host | Right arm | Left arm |
   |---|---|---|
   | Robot | `can1` | `can3` |
   | Teleop | `can0` | `can2` |

3. Support the arms before powering them. Leaders are read-only and must be
   supported by hand; followers hold their pose under power. The arms have no brakes.
4. Use the vendor setup tools to check joint zeros and measure each gripper's
   motor angle with its jaws fully closed and fully open. Keep these four
   measurements for each host. Do not substitute example angles.

You need Python 3.12 and `uv` installed on both hosts, and a LiveKit server
reachable from both. Have its URL, API key, and API secret ready.

## 2. Install and configure both hosts

On **each host**, run the following. If `.env` already exists, skip the copy
and edit the existing file.

```bash
uv sync
cp .env.example .env
```

Set the following values in `.env`, using the same server credentials and room
on both hosts:

```dotenv
LIVEKIT_URL=wss://YOUR_LIVEKIT_HOST
LIVEKIT_API_KEY=YOUR_API_KEY
LIVEKIT_API_SECRET=YOUR_API_SECRET
LIVEKIT_ROOM=portal-makermods
PORTAL_DATA_TASKS=fold the towel
PORTAL_HOTKEYS_GLOBAL=0
METAL_MAX_STEP_RAD=0.02
```

Replace the three `YOUR_...` values. This walkthrough uses **fold the towel**
as the task and terminal hotkeys. Keep the teleop terminal focused when pressing keys.

Add these settings to each host's `.env`, replacing every placeholder with the
measurements for the arms attached to that host:

```dotenv
METAL_LEFT_GRIPPER_CLOSED_DEG=MEASURED_VALUE
METAL_LEFT_GRIPPER_OPEN_DEG=MEASURED_VALUE
METAL_RIGHT_GRIPPER_CLOSED_DEG=MEASURED_VALUE
METAL_RIGHT_GRIPPER_OPEN_DEG=MEASURED_VALUE
```

Check for an existing `.env.local`: its values override `.env`.
Confirm the driver imports on both hosts:

```bash
uv run python -c "from robots import metal, motor_arm, robstride; print('ok')"
```

## 3. Enable CAN and check cameras

On the **robot host**:

```bash
sudo ip link set can1 up type can bitrate 1000000
sudo ip link set can3 up type can bitrate 1000000
v4l2-ctl --list-devices
```

In its `.env`, set the camera paths to the devices listed for each view:

```dotenv
METAL_CAM_OVERHEAD=/dev/video0
METAL_CAM_LEFT_WRIST=/dev/video2
METAL_CAM_RIGHT_WRIST=/dev/video4
```

On the **teleop host**:

```bash
sudo ip link set can0 up type can bitrate 1000000
sudo ip link set can2 up type can bitrate 1000000
```

If your CAN names differ, use your actual names in the commands and set the
[CAN variables](09-reference.md#hardware) in `.env` to match.

## 4. Start the three processes

Leave each command running in its own terminal. Start them in this order:

1. **Robot host, terminal 1:**

   ```bash
   uv run robot
   ```

2. Once the robot has connected, **robot host, terminal 2:**

   ```bash
   uv run recorder
   ```

3. **Teleop host, terminal 3:**

   ```bash
   uv run teleoperator
   ```

Wait for all three camera views and the recorder's output path before continuing.
The robot must connect first because the other processes wait for its clock.

## 5. Check human control

In the teleop terminal:

1. Hold the leaders near the followers' current poses.
2. Press `c` and confirm the human operator is selected.
3. Move the leaders gently and check that both followers track them.
4. Place the towel in the workspace and move the arms to the starting pose.

Leave all three processes running and continue to
**[2. Run Dream-1 and record corrections](02-dream-1.md)**.

If you need to stop here, rest or support all arms, press `x` in teleop,
then stop the recorder and robot with Ctrl-C. Wait for the recorder to exit.

To record without a policy, follow the
[human-only alternative](09-reference.md#human-only-demonstrations).

---

Previous: [0. What is Portal?](00-what-is-portal.md) · [Tutorial index](../README.md#the-tutorial) · Next: [2. Run Dream-1 and record corrections](02-dream-1.md)
