"""Adapter wrapping `maker_arm.Arm` (makermods-robotics/maker-arm-sdk) for Portal / Dream-1."""
from __future__ import annotations

from dataclasses import replace
import logging
import math
from types import SimpleNamespace

import numpy as np

from maker_arm.arm import Arm as SdkArm
from maker_arm.config import ArmConfig as SdkArmConfig
from maker_arm.profiles import DEFAULT_ARM_CONFIG
from maker_arm.transport.base import create_backend
from utils.common import env_float, env_str

log = logging.getLogger(__name__)


def _resolve_backend(channel: str) -> tuple[str, dict]:
    """Map a CAN channel string to maker-arm-sdk's backend name and kwargs."""
    if channel.startswith("/dev/"):
        return "slcan", {"port": channel, "baudrate": 115200, "rtscts": False, "startup_delay": 2.5}
    return "socketcan", {"channel": channel}


class MakerSdkFollowerArm:
    """Wraps `maker_arm.Arm` behind the `.read()`, `.write()`, `.torque_off()`, `.close()` interface used by `utils.metal.Arm`."""

    def __init__(
        self,
        side: str,
        can_channel: str,
        *,
        gripper_open_deg: float = -96.0,
        gripper_closed_deg: float = -2.5,
        gripper_hold_torque_nm: float = 0.5,
        energize: bool = True,
    ) -> None:
        self.side = side
        self.can_channel = can_channel
        self._open_rad = math.radians(gripper_open_deg)
        self._closed_rad = math.radians(gripper_closed_deg)
        self._rejects = 0

        cfg = SdkArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
        cfg.gravity_scale = env_float("MAKER_GRAVITY_SCALE", cfg.gravity_scale)

        # Apply configured gripper bounds and hold torque to joint 7 (gripper)
        lo_deg = min(gripper_open_deg, gripper_closed_deg)
        hi_deg = max(gripper_open_deg, gripper_closed_deg)
        cfg.joints[6] = replace(
            cfg.joints[6],
            lo=math.radians(lo_deg),
            hi=math.radians(hi_deg),
            hold_torque_nm=gripper_hold_torque_nm,
        )

        backend_name, backend_kwargs = _resolve_backend(can_channel)
        if backend_name == "slcan":
            cfg.control_rate_hz = env_float("MAKER_CONTROL_RATE_HZ", 25.0)

        backend = create_backend(backend_name, **backend_kwargs)
        # Pace commands on the candleLight/gs_usb (SocketCAN) arm only: wait for each motor's reply
        # before the next so its RX FIFO never overflows ("no fault-status reply ...": a brakeless
        # hold). The SLCAN arm is already serial-paced and FAULTs if paced this way ("feedback
        # timeout -- check bus/power"), so leave it alone. Same scope as the single-arm kit.
        if backend_name == "socketcan" and env_str("MAKER_FEEDBACK_PACED", "1").lower() not in ("0", "false", "no"):
            backend.feedback_paced_control = True
            backend.feedback_wait_timeout = env_float("MAKER_FEEDBACK_WAIT_S", 0.015)

        self.sdk_arm = SdkArm(cfg, backend)
        self.sdk_arm.connect()
        if energize:
            self.sdk_arm.enable()

    def _rad_to_grip_fraction(self, rad: float) -> float:
        span = self._open_rad - self._closed_rad
        if abs(span) < 1e-9:
            return 0.0
        return float(min(1.0, max(0.0, (rad - self._closed_rad) / span)))

    def _grip_fraction_to_rad(self, grip: float) -> float:
        grip = float(min(1.0, max(0.0, grip)))
        return self._closed_rad + grip * (self._open_rad - self._closed_rad)

    def read(self) -> SimpleNamespace:
        if self.sdk_arm.state.name == "CONNECTED":
            self.sdk_arm.refresh(wait=True, timeout=0.05)
        pos = self.sdk_arm.get_joint_positions()
        if len(pos) != 7 or not all(map(math.isfinite, pos)):
            raise RuntimeError(f"{self.side} Maker arm: non-finite joint positions {pos}")
        return SimpleNamespace(
            positions_rad=np.asarray(pos[:6], dtype=np.float64),
            gripper=self._rad_to_grip_fraction(pos[6]),
        )

    def write(self, cmd) -> None:
        joints = [float(x) for x in cmd.positions_rad[:6]]
        grip_rad = self._grip_fraction_to_rad(float(cmd.gripper))
        if not self.sdk_arm.set_joint_targets(joints + [grip_rad]):
            # The arm transiently refused the target (a momentary CAN feedback gap put a joint in
            # FAULT; the SDK's own control loop keeps HOLDING the measured pose, so a brakeless arm
            # never drops). Drop this command instead of raising: a motor fault must never end the
            # robot loop (AGENTS.md). The operator parks/powers-cycles (`p` `p`) or resets to clear it.
            self._rejects += 1
            if self._rejects == 1 or self._rejects % 60 == 0:
                log.warning(
                    "%s Maker arm dropped a target (state=%s, fault=%s); holding its pose, %d so far",
                    self.side, self.sdk_arm.state.name, self.sdk_arm.fault_reason, self._rejects,
                )

    def torque_off(self) -> None:
        self.sdk_arm.disable()

    def close(self) -> None:
        self.sdk_arm.disconnect()
