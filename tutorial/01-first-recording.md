# 1. Set up and record your first take

By the end of this chapter, you will have one recorded demonstration and a
LeRobot dataset. You do not need a policy, a Dream Labs key, or a training GPU yet.

A **take** is one attempt at a task, marked with `r` to begin and `r` to end.
The recorder saves a session archive (`.rrd`); conversion extracts finished
takes as training episodes. Run every command below from the repository root.

## 1. Prepare the hosts

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

## 2. Record a demonstration

Run these in separate terminals, on the appropriate hosts. Start the robot
first: the recorder and operators wait for its clock when they connect, and give
up after 10 s without it.

```bash
# Terminal 1 — follower and camera host
uv run robot

# Terminal 2 — recording host
uv run recorder

# Terminal 3 — leader host
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

Before continuing, check that all three camera views are live, `c` lets the
leaders move the followers, and the recorder has written a `.rrd` file. Finish
the take with `r` before quitting; `x` discards an open take. Rest or support
the arms before stopping the robot or teleoperator.

## Convert and inspect

```bash
uv run livekit-portal convert data/sessions --output lerobotv3 --dry-run
uv run livekit-portal convert data/sessions --output lerobotv3 --repo-id local/portal-makermods
```

The dataset is written under `data/local/portal-makermods` by default. Discarded and
unclosed takes are excluded. Keep the original `.rrd` files so you can inspect
them in Rerun or change conversion settings later.

For a dataset hosted on Hugging Face, use your repo ID and add `--push`.

You now have the inputs for training: the original session archive and the
converted dataset. Check the conversion report for included episodes and rows.
If it contains no usable data, fix recording before starting a training job.

---

Previous: [0. What is Portal?](00-what-is-portal.md) · [Tutorial index](../README.md#the-tutorial) · Next: [2. A policy to interrupt: Dream-1](02-dream-1.md)
