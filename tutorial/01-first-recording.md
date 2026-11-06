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
2. Connect one USB-CAN adapter per arm. This walkthrough uses SLCAN serial
   adapters, which Dream's driver opens directly at 1 Mbps. No manual CAN startup
   is needed. candleLight (`gs_usb`) adapters appear as `can0`, `can1`, ...
   instead; set them up with the
   [SocketCAN alternative](09-reference.md#socketcan-alternative).
3. Support the arms before powering them. Leaders are read-only and must be
   supported by hand; followers hold their pose under power. The arms have no brakes.
   Rest the followers where they can go limp, such as folded on their stops: the robot
   records that pose at startup and parks there when you press `p`.
4. Use [MakerMods Lab](https://github.com/makermods-robotics/makermodslab), MakerMods' setup and control app, to check joint zeros, find
   which adapter drives which arm, and measure each gripper's
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

## 3. Identify the arm adapters and cameras

On **each host**, identify its two adapters before starting any robot software:

1. With the arms powered off and supported, connect only the **right arm's USB
   adapter** to the host.
2. List its stable serial path:

   ```bash
   ls -l /dev/serial/by-id/
   ```

3. Copy the full `/dev/serial/by-id/...` path for that adapter.
4. Connect the **left arm's USB adapter**, run the command again, and copy the
   newly added path. Keep both adapters connected. Each arm must have a distinct path.
5. Set the two paths in that host's `.env`, replacing the example names below.

**Robot host:**

```dotenv
METAL_FOLLOWER_RIGHT_CAN=/dev/serial/by-id/RIGHT_ADAPTER_ID
METAL_FOLLOWER_LEFT_CAN=/dev/serial/by-id/LEFT_ADAPTER_ID
```

**Teleop host:**

```dotenv
METAL_LEADER_RIGHT_CAN=/dev/serial/by-id/RIGHT_ADAPTER_ID
METAL_LEADER_LEFT_CAN=/dev/serial/by-id/LEFT_ADAPTER_ID
```

Use the stable paths rather than `/dev/ttyACM0`, whose number can change after
reconnecting. To confirm which arm is which, run `uv run robot --check-arms`
with the arms powered: it reads the followers with their motors off and prints
each one's joints. Move one arm by hand and watch which line changes. If no unique paths appear, see [serial adapter setup](09-reference.md#serial-adapter-setup).
If `/dev/serial/by-id/` is empty and `lsusb` lists a `candleLight USB to CAN
adapter`, use the [SocketCAN alternative](09-reference.md#socketcan-alternative)
and set the two variables to the `canN` names instead.

On Ubuntu, grant your user serial access once on each host:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and back in for the group change to apply. Dream's driver handles the
serial connection and bitrate when the Portal peers start. Close MakerMods Lab
and leave `dreamcli robot up` stopped; Portal runs the control loop here.
If a vendor `slcand` service already owns the adapter, follow the
[existing-adapter instructions](09-reference.md#serial-adapter-setup).

On the **robot host**, list cameras (`v4l2-ctl` comes with `v4l-utils`):

```bash
sudo apt install v4l-utils
v4l2-ctl --list-devices
```

Each USB camera lists two devices; use the first. To tell the views apart, run
`uv run recorder --observe-only` once the robot is up (section 4).

Set its `.env` camera paths to the devices listed for each view:

```dotenv
METAL_CAM_OVERHEAD=/dev/video0
METAL_CAM_LEFT_WRIST=/dev/video2
METAL_CAM_RIGHT_WRIST=/dev/video4
```

Support the arms and power them on before continuing.

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
To look at the room from any machine with the repo and `.env`, run
`uv run recorder --observe-only`: it opens a Rerun window and writes nothing.
The robot must connect first because the other processes wait for its clock.

## 5. Check human control

In the teleop terminal:

1. Hold the leaders near the followers' current poses.
2. Press `c` and confirm the human operator is selected.
3. Move the leaders gently and check that both followers track them.
4. Place the towel in the workspace and move the arms to the starting pose.

Leave all three processes running and continue to
**[2. Run Dream-1 and record corrections](02-dream-1.md)**.

If you need to stop here, press `p` in teleop to park: the followers walk back to the pose they
started in, switch off, and the robot exits. Then press `x` in teleop and stop the
recorder with Ctrl-C. Wait for the recorder to exit. Ctrl-C on the robot instead
leaves the arms holding under power, and the next start then refuses until you
support them and cycle their power.

To record without a policy, follow the
[human-only alternative](09-reference.md#human-only-demonstrations).

---

Previous: [0. What is Portal?](00-what-is-portal.md) · [Tutorial index](../README.md#the-tutorial) · Next: [2. Run Dream-1 and record corrections](02-dream-1.md)
