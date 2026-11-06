"""The operator's window: the newest frame from each camera and the active task.

Everything is logged `static=True`, so the viewer keeps only the latest frame
and memory stays flat over a long session. The recorder keeps the history.

Each program spawns its viewer on a port of its own, so it gets a window of its own. `spawn()` on a
port where a viewer already listens streams into that window instead, and the newest stream takes it
over: its layout replaces the window's, and with the panels collapsed the other recording is hidden.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import numpy as np
import rerun as rr
import rerun.blueprint as rrb

logger = logging.getLogger(__name__)

OBSERVER_VIEWER_PORT = 9876  # `recorder --observe-only`; Rerun's default
TELEOPERATOR_VIEWER_PORT = 9877
HAND_TELEOP_VIEWER_PORT = 9878


class Preview:
    def __init__(self, room: str, cameras: Sequence[str], *, port: int) -> None:
        self._stream: Optional[rr.RecordingStream] = rr.RecordingStream(f"portal-makermods-{room}")
        try:
            self._stream.spawn(port=port)
            self._stream.send_blueprint(rrb.Blueprint(
                rrb.Vertical(
                    rrb.Horizontal(*(
                        rrb.Spatial2DView(origin=f"/{cam}", name=cam) for cam in cameras)),
                    rrb.TextDocumentView(origin="/task", name="Task"),
                    row_shares=[5, 1],
                ),
                collapse_panels=True,
            ))
        except Exception as exc:
            logger.warning("rerun viewer unavailable (%s); continuing without a window", exc)
            self._stream = None

    def show(self, camera: str, image: np.ndarray) -> None:
        if self._stream is None:
            return
        try:
            self._stream.log(camera, rr.Image(image), static=True)
        except Exception:
            logger.debug("rerun log failed", exc_info=True)

    def show_task(self, tasks: Sequence[str], active: int) -> None:
        """Show the task list in the window, so a task switch is visible where the operator looks."""
        if self._stream is None:
            return
        if len(tasks) == 1:
            text = f"# {tasks[0]}"
        else:
            text = "\n".join(
                [f"# {active + 1} · {tasks[active]}", ""]
                + [f"- {'**' if i == active else ''}`{i + 1}` {task}"
                   f"{'**' if i == active else ''}" for i, task in enumerate(tasks)])
        try:
            self._stream.log("task", rr.TextDocument(text, media_type=rr.MediaType.MARKDOWN),
                             static=True)
        except Exception:
            logger.debug("rerun task log failed", exc_info=True)

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.flush(timeout_sec=5.0)
        except Exception:
            logger.debug("rerun flush failed", exc_info=True)
