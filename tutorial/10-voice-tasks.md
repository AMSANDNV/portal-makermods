# 10. Steer Dream-1 by voice

Dream-1 follows a one-line task prompt. In [chapter 2](02-dream-1.md) that prompt
is fixed when the process starts, and `c` is the only way to hand over control.
Here you run a Dream-1 operator whose task can change while it runs, and a voice
agent that lets you change the task, pause and resume Dream-1, and pass the arms
between Dream-1 and the leader arms, by talking:

> **You:** Put the blue cube in the box.\
> **Agent:** Task set; say "continue" when you're ready for me to start.\
> **You:** Continue. *(Dream-1 takes control and reaches for the blue cube.)*\
> **You:** Wait, stop! Put the red cube in the box instead. *(The arms hold as soon as "wait" is heard.)*\
> **Agent:** I've switched the task to the red cube; say "continue" when you're ready.\
> **You:** Continue. *(Dream-1 starts on the red cube from where the arms are.)*\
> **You:** Switch to teleop. *(The followers track the leader arms.)*\
> **You:** Okay, back to Dream-1. *(Dream-1 takes over again.)*

**A new task never starts motion.** Changing the task while Dream-1 drives pauses
it first; only "continue" (or `c`) hands the arms back. Voice is not an emergency
stop: a spoken "stop" takes effect a second or two after you say it. To intervene
faster, take over with the leaders (`c` in teleop) or park (`p`).

Complete [chapter 2](02-dream-1.md) first: this chapter uses the same rig,
Dream-1 key, and running robot, recorder, and teleoperator.

## 1. How it fits

| Process | Identity | What it does |
|---|---|---|
| `uv run dream-1-tasks` | `policy-dream-1-tasks` (Portal operator) | Chapter 2's Dream-1 loop with a switchable task; answers the `dream.*` RPCs |
| `uv run --group voice voice-agent` | `voice-agent` (not a Portal peer) | Turns what you say into those RPCs; never drives the arms |

```text
you ──speech──▶ voice agent ──RPC dream.*──▶ dream-1-tasks ──set_active_operator──▶ robot
```

The voice agent joins the robot's room as an ordinary LiveKit participant and
calls RPC methods on `policy-dream-1-tasks`, which changes its own prompt or
moves the robot's control gate, as `c` does. The agent has no Portal role, so it
never appears in teleop's `c` ring and cannot send actions.

| Method | Payload | Effect |
|---|---|---|
| `dream.status` | `{}` | None; reports the task and who drives |
| `dream.set_task` | `{"task": "put the red cube in the box"}` | Pauses Dream-1 if it drives, then changes the prompt |
| `dream.pause` | `{}` | Dream-1 releases control if it has it; the robot holds the arms |
| `dream.resume` | `{}` | Dream-1 takes control from whoever has it, starting a new episode |
| `dream.to_teleop` | `{}` | Control passes to `teleoperator`; the followers track the leaders |

Every reply is the same JSON status: `task`, `presets`, `driving`,
`active_operator`, and `teleoperator_connected`. A pause leaves no operator
active, so the robot sends no commands and the followers hold their last target
under power. A pause while the teleoperator drives changes nothing.

## 2. Install the voice agent

The agent follows LiveKit's
[agent-starter-python](https://github.com/livekit-examples/agent-starter-python):
speech-to-text, the LLM, and text-to-speech run on
[LiveKit Inference](https://docs.livekit.io/agents/models/), which needs a
LiveKit Cloud project. Run it on any host with a microphone and speaker, such as
the teleop host:

```bash
uv sync --group voice
sudo apt install libportaudio2   # Linux: audio for console mode
```

If `LIVEKIT_URL` is your Cloud project, nothing else is needed. With a
self-hosted server, such as `livekit-server --dev`, keep the robot's room there
and add a Cloud project's key for Inference only:

```dotenv
LIVEKIT_INFERENCE_API_KEY=YOUR_CLOUD_API_KEY
LIVEKIT_INFERENCE_API_SECRET=YOUR_CLOUD_API_SECRET
```

## 3. Start the task-switching Dream-1

Keep the robot, recorder, and teleoperator running, and stop `uv run dream-1` if
it is running. On the **robot host**, give the session's tasks, then check the
targets without moving as in [chapter 2](02-dream-1.md#2-check-targets-without-moving):

```bash
uv run dream-1-tasks --dry-run \
  --task "put the blue cube in the box" \
  --task "put the red cube in the box"
```

The first task is the starting one. Without `--task`, the list comes from
`PORTAL_DATA_TASKS`, the same setting teleop reads, then `PORTAL_DATA_TASK`.
In this terminal, `1`–`9` switch task (pausing Dream-1 if it drives), `c` takes
or releases control, and `p` parks or powers up the arms. When the targets look
right, stop it with Ctrl-C and start it again without `--dry-run`.

## 4. Talk to it

On the host with the microphone:

```bash
uv run --group voice voice-agent console
```

The agent greets you with the current task and who is driving. Then:

| Say | The agent calls |
|---|---|
| "Put the green cube in the bowl", "do the second one" | `dream.set_task`, then asks you to say "continue" |
| "Continue", "go", "start", "back to Dream-1" | `dream.resume` |
| "Stop", "wait", "hold on", "pause" | `dream.pause` |
| "Switch to teleop", "I'll take over" | `dream.to_teleop` |
| "What's happening?", "who's driving?" | `dream.status` |
| "Put the red cube in the box and go" | `dream.set_task`, then `dream.resume` |

Before "switch to teleop", hold the leaders near the followers' pose, as before
pressing `c`: the followers start tracking them at once, within the step limit.

"Stop", "wait", "hold on", "pause", and "freeze" are matched in the live
transcript and pause Dream-1 before you finish the sentence. The LLM still reads
the whole sentence, so "wait, stop, the red cube instead" both pauses and changes
the task. A false match only pauses.

To talk from a browser instead, run the agent in a room of its own and join that
room from any LiveKit frontend:

```bash
uv run --group voice voice-agent connect --room portal-voice
lk token create --join --room portal-voice --identity you   # a token for the frontend
```

Keep the conversation out of the robot's room: the agent listens to the first
participant it finds, and there that is a Portal peer, not you. The agent still
reaches Dream-1 through its own connection to `LIVEKIT_ROOM`.

## 5. Record the corrections

Teleop owns the takes and labels each with *its* task. A spoken task changes
what Dream-1 does, not the label of the open take. In a recording session:

1. Set the same list on both hosts with `PORTAL_DATA_TASKS`, so teleop's `1`–`9`
   name the same tasks as `dream-1-tasks`.
2. End the take with `r`, or discard it with `[`, before you change the task by voice.
3. Select the matching task in teleop with its number, start the next take with
   `r`, then say "continue".

Pauses and handoffs inside a take are fine: the recorder keeps writing, and a
correction you make after "switch to teleop" is recorded as in chapter 2.

## 6. How the code works

[`src/policies/dream_1_tasks/run.py`](../src/policies/dream_1_tasks/run.py)
reuses chapter 2's mapping functions and loop. `TaskControl` holds the task and
answers the RPCs. `set_task` releases control before it changes the prompt, and
sets `changed`. The loop then treats a task change like a handoff:

```python
if control_changed or tasks.changed:
    control_changed = tasks.changed = False
    ...
    await worker.run(client.reset_episode)
```

A new episode clears Dream-1's memory of the old task and its chunk blending, so
a chunk planned for the blue cube is never played under the red cube's prompt.
The arms ramp from where they are, as after a handoff.

[`src/voice_agent.py`](../src/voice_agent.py) keeps the starter's pipeline:
AssemblyAI speech-to-text with keyterms (`Dream-1`, `teleop`), Gemma 4 as the
LLM, Fish Audio speech with expressive mode, the LiveKit turn detector with
preemptive generation, and ai-coustics noise cancellation on the room's
microphone. On top of it, `VoiceControl` has five tools: `status`,
`change_task`, `pause`, `resume`, and `switch_to_teleop`. Each calls one RPC
through `PolicyLink`, the agent's own connection to the robot's room, and
returns the status as a sentence, so the agent reports only what happened. The
fast pause is a `user_input_transcribed` handler that matches stop words in
interim transcripts. `VOICE_STT`, `VOICE_LLM`, `VOICE_TTS`, and
`VOICE_TTS_VOICE` swap models; see the [reference](09-reference.md#voice-agent).

The [hardware-free checks](../test/README.md) cover the task and control rules
and the RPCs between the agent's connection and a real operator.

## Troubleshooting

- **The agent says Dream-1 is not in the room.** Start `uv run dream-1-tasks`,
  and check both use the same `LIVEKIT_URL` and `LIVEKIT_ROOM`.
- **"The teleoperator is not connected."** Start `uv run teleoperator` on the
  teleop host; the agent hands control only to a connected teleoperator.
- **The agent cannot reach its models.** LiveKit Inference needs a Cloud
  project's key: `LIVEKIT_API_KEY` for a Cloud `LIVEKIT_URL`, otherwise
  `LIVEKIT_INFERENCE_API_KEY` and `LIVEKIT_INFERENCE_API_SECRET`.
- **`ai_coustics ... Unable to authorize model use`.** Noise cancellation runs
  on LiveKit Cloud rooms only. In a room on a self-hosted server it logs this and
  passes the audio through; remove the `noise_cancellation` line to silence it.
- **Console mode finds no microphone.** List devices with
  `voice-agent console --list-devices` and pick one with `--input-device`.
  The CLI may print that console mode is moving to `lk agent console`; it still works.
- **"Continue" does nothing.** Dream-1 sends nothing until all three cameras
  publish, as in chapter 2. Ask "what's happening?" and check the
  `dream-1-tasks` terminal.

---

Previous: [9. Configuration and troubleshooting](09-reference.md) · [Tutorial index](../README.md#the-tutorial)
