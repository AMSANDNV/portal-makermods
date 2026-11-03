# 2. Robot loop

Open [`src/robot.py`](../src/robot.py).
The loop reads both Metal followers and the three cameras, publishes
observations, and applies the latest accepted action. Hardware setup and
recovery live in `src/utils/metal.py`, over MakerMods' `metal_sdk`: each arm is
one `MetalSDKInterface` on its own CAN link, in real-time joint-position mode.

## Receive an action

Portal calls `on_action` for actions that pass the active-operator gate.
The callback saves their values; the hardware loop applies the latest values
on its next tick. This avoids issuing a motor transaction for every packet
when actions arrive in a burst.

The robot does not need to know whether an action came from a human or ACT.
Both use the same schema and gate.

## Publish one tick

The main loop contains this sequence:

```python
ts_us = robot.now_us()
observation = await hardware.run(followers.get_observation)
state, frames = split_state_frames(observation, tracks)
if state:
    robot.send_state(state, timestamp_us=ts_us)
for name, frame in frames.items():
    robot.send_video_frame(name, frame, timestamp_us=ts_us)
if latest_action:
    await hardware.run(followers.send_action, latest_action)
```

State is published only when both arms read: half a row would be invented
data. State and frames share one timestamp. That lets Portal match them into an
observation and lets the recorder align responses later. Frames are RGB
`uint8` arrays with shape `(height, width, 3)`.

`HardwareThread` serializes device calls while leaving the asyncio loop free
for network callbacks. `pace(fps)` also yields when a tick overruns its budget;
otherwise an overloaded hardware loop could starve the network.

## Handle a handoff

The robot starts with no active operator. On a gate change it clears its cached
action, so the previous operator’s command is not reused while waiting for the
new operator. If the active operator disconnects, it also releases the gate.

The followers limit each commanded joint target step (`METAL_MAX_STEP_RAD`),
keep every joint inside MakerMods' soft limits, and reseed the ramp after pauses
or an arm reopen. An arm is never commanded before it has been read. This limits command changes during takeover; it is not
a guarantee of physical motor speed.

Camera reads reuse the last good frame during a USB interruption and reconnect
in the background. Arm I/O failures, including motor faults the SDK reports, are counted and
retried per arm; repeated failures reopen that arm's SDK interface on the same
worker thread, which re-enables its motors. The target ramp resets from the
present pose before commands resume.

Nothing in this project disables the motors, on stop or on fault: a Metal arm
has no brakes and drops when disabled. Rest or support the arms before you stop
the robot. These hardware details stay in `src/utils/metal.py` so the Portal
loop remains readable.

Next: [03. Operator loop](03-operator-loop.md).
