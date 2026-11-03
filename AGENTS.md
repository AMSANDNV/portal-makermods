# Working on this tutorial

This repo teaches the full HITL loop with Portal: collect demonstrations,
run Dream-1 or train ACT, hand off between policy and human, and record corrections for retraining.
Keep it small and readable. The README leads with the numbered `tutorial/`.

## Structure

- `src/robot.py`, `src/teleoperator.py`, and `src/recorder.py` show the Portal flows.
- `src/_common.py` holds shared environment, token, and scheduling helpers.
- `src/utils/metal.py` drives the Metal pair through MakerMods' `metal_sdk` (imported lazily);
  `src/utils/` also holds preview, hotkey, and task-prompt details.
- `src/policies/act/` contains training, inference, and a SkyPilot example.
- `src/policies/dream-1/` runs Dream Labs' Dream-1 with the dream client's `metal` IK.
- `test/` checks behavior without hardware.

Run the three peers as `uv run robot`, `uv run teleoperator`, and `uv run recorder`
(entry points in `pyproject.toml`); policies run as scripts under `src/policies/`.
Keep explanations in the tutorials rather than creating a separate docs layer.
Update affected chapters with behavior changes.

## Rules

- All peers load `portal.yaml`; do not duplicate schema field lists in Python.
- Model tensors use training dataset order, read from `meta/info.json`.
- Control is explicit: `c` selects an operator. No background auto-claim loop.
- Keep recording separate from the control loops. Preserve action anchors and
  both archive timelines; let Portal stamp synchronized action send times.
- Run blocking hardware or inference off the event loop. Device calls must be
  serialized and finished before disconnecting hardware.
- Never disable the Metal motors on stop or fault: the arms are brakeless and drop.
- Preserve camera/CAN recovery, the soft-limit clamp, and the target-step limiter. A small clamp on
  tracking error limits torque under MIT control and can immobilize joints.
- Check installed library APIs when restoring older examples.

## Checks

```bash
uv run --group train python -m unittest test.test_act
uv run python -m test.test_teleop
uv run python -m test.test_resilience
uv run --group dream python -m unittest test.test_dream1
# With livekit-server --dev:
uv run python -m test.test_pubsub
uv run python -m test.test_recorder
uv run python -m test.test_handoff
```
