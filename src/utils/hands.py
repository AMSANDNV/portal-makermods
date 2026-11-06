"""Webcam hands as Metal arm targets: the gesture clutch, the hand's pose, and the relative mapping.

Numpy on MediaPipe's 21 hand landmarks, so all of it tests without a camera or a model; the webcam
loop is `src/hand_teleop.py`. See tutorial/11-hand-teleop.md.

Two frames:
- camera: OpenCV's, of the raw (unmirrored) webcam image: x right, y down, z away from the lens.
  MediaPipe's world landmarks use these axes, in metres, about the hand's centre.
- base: each Metal arm's own, as dream_ik's FK has it: x forward, y to the robot's left, z up.

The operator faces the webcam as if standing behind the arms, facing the workspace: a hand pushed
toward the screen reaches forward, the operator's right is the robot's right, up is up.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from utils.metal import GRIPPER_MAX_MM, SIDES

# MediaPipe's landmark indices.
WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP = 0, 4, 5, 8
MIDDLE_MCP, MIDDLE_TIP, RING_MCP, RING_TIP, PINKY_MCP, PINKY_TIP = 9, 12, 13, 16, 17, 20
PALM = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)  # stays put while the fingers move
CLOSED_FINGERS = ((MIDDLE_MCP, MIDDLE_TIP), (RING_MCP, RING_TIP), (PINKY_MCP, PINKY_TIP))
BONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))

# Camera axes into base axes: toward the lens (-z) is forward (+x), the image's right (the operator's
# left) is the robot's left (+y), and the image's up (-y) is up (+z).
CAMERA_TO_BASE = np.array([[0.0, 0.0, -1.0], [1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])

# Tip-to-wrist over knuckle-to-wrist, per finger: about 1.8 straight. Below 1.0 the tip has folded back
# past its knuckle, into the palm; a hooked or half-curled finger stays above it.
CURLED_BELOW = 1.0
OPEN_ABOVE = 1.15


@dataclass(frozen=True)
class Hand:
    """One hand in one webcam frame, as MediaPipe reads the raw image."""

    label: str  # "left" or "right": the operator's own hand
    image: np.ndarray  # (21, 2) pixels
    world: np.ndarray  # (21, 3) m, camera axes, about the hand's centre


@dataclass(frozen=True)
class HandPose:
    """What one sighting says about a hand: where it is, how it is turned, how far the pinch is open."""

    at: float  # s, monotonic
    position: np.ndarray  # (3,) m, the palm's centre in the camera frame
    rotation: np.ndarray  # (3, 3) the palm's axes in the camera frame
    pinch: float  # m, thumb tip to index tip
    curl: np.ndarray  # (3,) middle, ring, pinky (see `curl`)


def curl(world: np.ndarray) -> np.ndarray:
    """Middle, ring and pinky: each tip's distance from the wrist over its knuckle's. Lower is more curled."""
    wrist = world[WRIST]
    return np.array([np.linalg.norm(world[tip] - wrist) / max(np.linalg.norm(world[mcp] - wrist), 1e-6)
                     for mcp, tip in CLOSED_FINGERS])


def pinch(world: np.ndarray) -> float:
    return float(np.linalg.norm(world[THUMB_TIP] - world[INDEX_TIP]))


def palm_position(image: np.ndarray, world: np.ndarray, focal_px: float, center_px: Sequence[float]
                  ) -> Optional[np.ndarray]:
    """The palm's centre in the camera frame (m), or None for a degenerate hand.

    Depth comes from size: the world landmarks are metric and camera-aligned, so their x-y spread
    against the pixel spread gives pixels per metre at the hand, which is focal / depth.
    """
    xy = world[:, :2] - world[:, :2].mean(axis=0)
    uv = image - image.mean(axis=0)
    px_per_m = float((uv * xy).sum() / max((xy * xy).sum(), 1e-12))
    if px_per_m <= 1e-6:
        return None
    # The fit's depth is the whole hand's; the palm sits this much farther (curled fingers come forward).
    depth = focal_px / px_per_m + world[list(PALM), 2].mean() - world[:, 2].mean()
    u, v = image[list(PALM)].mean(axis=0)
    return np.array([(u - center_px[0]) / focal_px * depth, (v - center_px[1]) / focal_px * depth, depth])


def palm_rotation(world: np.ndarray) -> np.ndarray:
    """The palm's axes in the camera frame: x wrist to middle knuckle, z the palm's normal, y = z × x."""
    x = world[MIDDLE_MCP] - world[WRIST]
    x = x / max(np.linalg.norm(x), 1e-9)
    z = np.cross(x, world[INDEX_MCP] - world[PINKY_MCP])
    z = z / max(np.linalg.norm(z), 1e-9)
    return np.stack([x, np.cross(z, x), z], axis=1)


def hand_pose(hand: Hand, at: float, focal_px: float, center_px: Sequence[float]) -> Optional[HandPose]:
    position = palm_position(hand.image, hand.world, focal_px, center_px)
    if position is None:
        return None
    return HandPose(at, position, palm_rotation(hand.world), pinch(hand.world), curl(hand.world))


def assign_sides(hands: Sequence[Hand], swap: bool = False) -> dict[str, Hand]:
    """Which arm each hand drives: the operator's right hand drives the right arm.

    The model's label decides, unless both hands carry the same one; then position does, the
    operator's right hand being on the raw image's left. `swap` is for a webcam that mirrors.
    """
    if swap:
        hands = [Hand({"left": "right", "right": "left"}.get(h.label, h.label), h.image, h.world) for h in hands]
    if len(hands) == 2 and hands[0].label == hands[1].label:
        on_left, on_right = sorted(hands, key=lambda h: h.image[WRIST, 0])
        return {"right": on_left, "left": on_right}
    out: dict[str, Hand] = {}
    for hand in hands:
        if hand.label in SIDES:
            out.setdefault(hand.label, hand)
    return out


def camera_to_base(yaw_deg: float = 0.0) -> np.ndarray:
    """Camera axes into one arm's base axes. `yaw_deg` is how far that arm's base x is turned to the
    left of straight ahead (counterclockwise, seen from above): 0 for an arm facing the workspace."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]).T @ CAMERA_TO_BASE


class Clutch:
    """Whether a hand holds its arm: the three fingers curled all the way into the palm for a few frames
    in a row closes it, any of them coming back out (or the hand gone) for a frame or two opens it. The
    gap between the two thresholds keeps a finger near either one from chattering."""

    def __init__(self, close_frames: int = 3, open_frames: int = 2,
                 curled_below: float = CURLED_BELOW, open_above: float = OPEN_ABOVE) -> None:
        self.close_frames, self.open_frames = close_frames, open_frames
        self.curled_below, self.open_above = curled_below, open_above
        self.closed = False
        self._run = 0

    def update(self, curls: Optional[np.ndarray]) -> bool:
        """One sighting's curl values (None: no hand); returns whether the clutch is closed."""
        if curls is None:
            self.closed, self._run = False, 0
            return False
        towards_change = (curls > self.open_above).any() if self.closed else (curls < self.curled_below).all()
        self._run = self._run + 1 if towards_change else 0
        if self._run >= (self.open_frames if self.closed else self.close_frames):
            self.closed, self._run = not self.closed, 0
        return self.closed


class OneEuro:
    """The 1€ filter (Casiez et al., 2012): smooth when the hand is still, little lag when it moves."""

    def __init__(self, min_cutoff_hz: float, beta: float, d_cutoff_hz: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff_hz, beta, d_cutoff_hz
        self._x: Optional[np.ndarray] = None
        self._dx = None
        self._at = 0.0

    @staticmethod
    def _alpha(cutoff_hz: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff_hz)
        return 1.0 / (1.0 + tau / dt)

    def reset(self, x, at: float) -> np.ndarray:
        self._x, self._dx, self._at = np.array(x, dtype=np.float64), np.zeros_like(np.asarray(x, np.float64)), at
        return self._x.copy()

    def __call__(self, x, at: float) -> np.ndarray:
        if self._x is None or at <= self._at:
            return self.reset(x, at) if self._x is None else self._x.copy()
        dt = at - self._at
        x = np.asarray(x, dtype=np.float64)
        self._dx = self._dx + self._alpha(self.d_cutoff, dt) * ((x - self._x) / dt - self._dx)
        cutoff = self.min_cutoff + self.beta * float(np.linalg.norm(self._dx))
        self._x = self._x + self._alpha(cutoff, dt) * (x - self._x)
        self._at = at
        return self._x.copy()


def _twist_about_z(rotation: np.ndarray) -> float:
    """The part of a rotation that turns about z (swing-twist), in rad."""
    return 2.0 * math.atan2(rotation[1, 0] - rotation[0, 1], 1.0 + np.trace(rotation))


def _rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


@dataclass
class Mapping:
    """How a hand's motion becomes a tool pose. Speeds bound the target, before the IK's joint caps."""

    to_base: np.ndarray  # (3, 3), from `camera_to_base`
    scale: float = 1.0  # tool metres per hand metre
    rotate: str = "off"  # "off": keep the tool's orientation; "yaw": turn about vertical only; "full"
    max_speed_m_s: float = 0.5
    max_turn_rad_s: float = math.pi
    pinch_closed_m: float = 0.03  # thumb to index tip at and below this: gripper closed
    pinch_open_m: float = 0.09  # at and above this: fully open
    smoothing_hz: float = 1.0  # the 1€ filter's cutoff for a still hand
    slip_m: float = 0.015  # the target this far from where the IK could go moves the anchor along
    slip_rad: float = math.radians(10)
    takeover_mm: float = 0.1 * GRIPPER_MAX_MM  # the pinch must come this close to the gripper to take it

    def grip_mm(self, pinch_m: float) -> float:
        span = max(self.pinch_open_m - self.pinch_closed_m, 1e-6)
        return float(np.clip((pinch_m - self.pinch_closed_m) / span, 0.0, 1.0)) * GRIPPER_MAX_MM


@dataclass
class ArmFollower:
    """One arm following one hand's motion since the clutch closed, never the hand's absolute place.

    `engage` anchors the hand's pose to the tool's. Each new sighting is filtered (`track`); each tick
    `goal` maps the hand's displacement into the base frame onto the anchored tool pose, moving the
    target no faster than the speed limits. Where the arm cannot follow (the floor, its reach),
    `settle` moves the anchor so the target stays where the arm is, and the hand drives it from
    there at once instead of first having to come back.

    The gripper takes over softly: it holds where it was until the pinch comes to it, so gripping
    again with an open hand never drops what the arm is holding.
    """

    mapping: Mapping
    engaged: bool = False
    _hand_pos: np.ndarray = field(default=None, repr=False)  # filtered, camera frame
    _hand_turn: np.ndarray = field(default=None, repr=False)  # filtered rotvec since the anchor, camera frame
    _pinch: float = 0.0
    _anchor_hand_pos: np.ndarray = field(default=None, repr=False)
    _anchor_hand_rot: np.ndarray = field(default=None, repr=False)
    _anchor_tcp: np.ndarray = field(default=None, repr=False)  # (4, 4)
    _goal: np.ndarray = field(default=None, repr=False)  # (4, 4), the last target
    _turn: np.ndarray = field(default=None, repr=False)  # (3, 3), the last base-frame turn applied to the anchor
    grip_mm: float = 0.0
    _grip_taken: bool = False
    _grip_side: float = 0.0

    def __post_init__(self) -> None:
        m = self.mapping
        self._filters = (OneEuro(m.smoothing_hz, 4.0), OneEuro(m.smoothing_hz, 0.5), OneEuro(2 * m.smoothing_hz, 2.0))

    def engage(self, hand: HandPose, tcp: np.ndarray, grip_mm: float) -> None:
        self.engaged = True
        self._anchor_hand_pos, self._anchor_hand_rot = hand.position.copy(), hand.rotation.copy()
        self._anchor_tcp, self._goal, self._turn = tcp.copy(), tcp.copy(), np.eye(3)
        self._hand_pos = self._filters[0].reset(hand.position, hand.at)
        self._hand_turn = self._filters[1].reset(np.zeros(3), hand.at)
        self._pinch = float(self._filters[2].reset([hand.pinch], hand.at)[0])
        self.grip_mm = grip_mm
        self._grip_side = math.copysign(1.0, self.mapping.grip_mm(self._pinch) - grip_mm)
        self._grip_taken = abs(self.mapping.grip_mm(self._pinch) - grip_mm) <= self.mapping.takeover_mm

    def release(self) -> None:
        self.engaged = False

    def track(self, hand: HandPose) -> None:
        """Take one new sighting of the hand (filtered); call once per webcam frame while engaged."""
        turn = Rotation.from_matrix(hand.rotation @ self._anchor_hand_rot.T).as_rotvec()
        self._hand_pos = self._filters[0](hand.position, hand.at)
        self._hand_turn = self._filters[1](turn, hand.at)
        self._pinch = float(self._filters[2]([hand.pinch], hand.at)[0])

    def goal(self, fps: float) -> np.ndarray:
        """This tick's target tool pose (4, 4) in the base frame; `grip_mm` is the gripper's."""
        m = self.mapping
        position = self._anchor_tcp[:3, 3] + m.scale * m.to_base @ (self._hand_pos - self._anchor_hand_pos)
        turn = m.to_base @ Rotation.from_rotvec(self._hand_turn).as_matrix() @ m.to_base.T
        self._turn = {"off": np.eye(3), "yaw": _rot_z(_twist_about_z(turn))}.get(m.rotate, turn)
        rotation = self._turn @ self._anchor_tcp[:3, :3]

        step = position - self._goal[:3, 3]
        if (n := np.linalg.norm(step)) > (limit := m.max_speed_m_s / fps):
            step *= limit / n
        swing = Rotation.from_matrix(rotation @ self._goal[:3, :3].T).as_rotvec()
        if (angle := np.linalg.norm(swing)) > (limit := m.max_turn_rad_s / fps):
            swing *= limit / angle
        self._goal = self._goal.copy()
        self._goal[:3, 3] += step
        self._goal[:3, :3] = Rotation.from_rotvec(swing).as_matrix() @ self._goal[:3, :3]

        wanted = m.grip_mm(self._pinch)
        if not self._grip_taken and (abs(wanted - self.grip_mm) <= m.takeover_mm
                                     or math.copysign(1.0, wanted - self.grip_mm) != self._grip_side):
            self._grip_taken = True
        if self._grip_taken:
            self.grip_mm = wanted
        return self._goal.copy()

    @property
    def grip_waiting(self) -> bool:
        """The gripper is holding until the pinch comes to it."""
        return self.engaged and not self._grip_taken

    def settle(self, reachable: np.ndarray) -> None:
        """Where the IK says the arm can go this tick (4, 4); slide the anchor if the target is past it."""
        m = self.mapping
        miss = reachable[:3, 3] - self._goal[:3, 3]
        if np.linalg.norm(miss) > m.slip_m:
            self._anchor_tcp[:3, 3] += miss
            self._goal[:3, 3] += miss
        off = Rotation.from_matrix(reachable[:3, :3] @ self._goal[:3, :3].T).magnitude()
        if off > m.slip_rad:
            self._anchor_tcp[:3, :3] = self._turn.T @ reachable[:3, :3]
            self._goal[:3, :3] = reachable[:3, :3]

    def moved(self) -> np.ndarray:
        """The target's offset from the anchor, base frame, m."""
        return self._goal[:3, 3] - self._anchor_tcp[:3, 3] if self.engaged else np.zeros(3)


class ArmsIK:
    """Both Metal arms' IK one tick at a time: dream_ik's ChunkIK on the tool point between the fingertips.

    `table_z_m` is a floor under each arm, in its base frame (0 is the base's underside, so the table
    it stands on): targets are clipped above it and the solve keeps the arm's centreline off it. There
    is no arm-to-arm guard: the bases' spacing is not known here.
    """

    def __init__(self, fps: float, table_z_m: float, max_joint_rad_s: float) -> None:
        from dream_ik.solver import ChunkIK  # lazy: JAX
        from dream_ik.specs import Region

        floor = (Region(lo=(None, None, table_z_m)),)
        self._ik = ChunkIK("metal", "tcp", hz=fps, obstacles={side: floor for side in SIDES})
        self._rows = [self._ik.sides.index(side) for side in SIDES]  # the IK orders sides (right, left)
        self.max_joint_rad_s = max_joint_rad_s
        self.rest = self._ik.side_rest[self._rows[0]].copy()

    def warmup(self) -> None:
        """Compile the one-tick solve and the FK (several seconds the first time, cached after)."""
        self._ik.warmup(1)
        for side in SIDES:
            self.fk(self.rest, side)

    def fk(self, q: np.ndarray, side: str) -> np.ndarray:
        return self._ik.fk(np.asarray(q, dtype=np.float64), side)

    def step(self, goals: dict[str, np.ndarray], q: dict[str, np.ndarray]
             ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        """One tick toward each side's goal (4, 4) from its joints `q` (6,). Returns the joints to
        command (speed capped, floor fenced) and the tool pose the unconstrained answer reaches."""
        targets, q0 = np.empty((2, 1, 4, 4)), np.empty((2, 6))
        for side, row in zip(SIDES, self._rows):
            targets[row, 0], q0[row] = goals[side], q[side]
        targets = self._ik.clip_to_region(targets, self._ik.collision_margin)
        result = self._ik.solve(targets, q0, self.max_joint_rad_s)
        command, _ = self._ik.fence_region(result.q, q0, slack=0.01)
        return ({side: command[row, 0].copy() for side, row in zip(SIDES, self._rows)},
                {side: self.fk(result.q_solution[row, 0], side) for side, row in zip(SIDES, self._rows)})
