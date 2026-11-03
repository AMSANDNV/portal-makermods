# 1. The wire contract

Every participant needs the same camera tracks, state fields, action fields,
and observation rate. Put them in one file so the robot, human, policy, and
recorder share the same definition: [`portal.yaml`](../portal.yaml).

This rig declares three cameras, one overhead and one on each wrist, and
fourteen positions: for each arm, `joint1`..`joint6` plus the gripper, left arm
first. Both state and action use `.pos` fields in the Metal SDK's own units:
joints in radians, the gripper as its stroke in mm (0 closed, 80 open). Left and
right are as the robot sees them.

```yaml
version: 1
fps: 30
reuse_stale_frames: true
videos:
  - { name: overhead_camera, codec: h264, max_bitrate_kbps: 20000, screencast: true }
  - { name: left_wrist_camera, codec: h264, max_bitrate_kbps: 20000, screencast: true }
  - { name: right_wrist_camera, codec: h264, max_bitrate_kbps: 20000, screencast: true }
```

The complete file also lists the fourteen `f32` fields under `state` and `action`.
Their order matters: model tensors and normalization statistics are positional.

## Load it for each role

```python
robot_cfg = RobotConfig.from_yaml_file(CONFIG_PATH, room)
operator_cfg = OperatorConfig.from_yaml_file(CONFIG_PATH, room)
observer_cfg = ObserverConfig.from_yaml_file(CONFIG_PATH, room)
```

The leader-arm process, ACT, and Dream-1 all use `OperatorConfig`. The room and identity
come from the environment and token, so the YAML can be shared across hosts.
Mismatched schemas produce fingerprint warnings and dropped packets. Deploy
schema changes to every peer together.

## Choose a transport

H264 uses WebRTC media and keeps video off the reliable state channel.
`screencast: true` preserves resolution under pressure by dropping frames.
The bitrate setting is a ceiling, not a target.

MJPEG sends independent JPEG frames over the reliable data channel. That avoids
inter-frame prediction but can delay state when video exceeds available
bandwidth. PNG and RAW use the same channel with larger payloads. See
[Running the rig](08-running-the-rig.md#configuration) for settings.

State and actions go over unreliable data packets, Portal's default. A reliable
channel retransmits a lost packet and holds every newer one until it arrives; at
30 ticks a second, a newer reading is already on its way, so it is better to
skip the lost one. The cost is that the recorder may miss the occasional row.
Packets can also arrive out of order; Portal drops any state or action stamped
behind the newest it already has from that sender, so neither stream goes
backwards. Keypoints and control stay reliable.

## Cameras

Each track's device comes from `METAL_CAM_<TRACK>`, dropping the `_camera`
suffix: `METAL_CAM_OVERHEAD`, `METAL_CAM_LEFT_WRIST`, and `METAL_CAM_RIGHT_WRIST`.
These are the three views Dream-1 reads; a 16:9 camera mode suits it best.

Every declared camera must publish. The human operator waits for a frame from
each before assigning observation anchors; synchronized observations for the
policy also depend on those tracks.

Next: [02. Robot loop](02-robot-loop.md).
