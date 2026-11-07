"""Maker v1 selection and USB transport checks; no hardware or server required."""
import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import can
import numpy as np
import usb.core
from gs_usb.gs_usb_frame import GS_USB_NONE_ECHO_ID, GsUsbFrame

from utils import metal
from utils.gs_usb_transport import BoundedGsUsb, DiagnosticError, select_gs_usb


class MakerSetupTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"MAKERMODS_MODEL": "makers"})
        env.start()
        self.addCleanup(env.stop)

    def test_limits_and_gripper_units(self):
        # Maker elbow travel exceeds Metal's, while its wrist limits differ.
        out = metal.clamp_to_limits({"left_joint3.pos": 4.0, "left_joint4.pos": -2.0,
                                     "left_gripper.pos": 100.0})
        self.assertEqual(out["left_joint3.pos"], 4.0)
        self.assertAlmostEqual(out["left_joint4.pos"], math.radians(-65.2))
        self.assertEqual(out["left_gripper.pos"], 80.0)
        arm = metal.Arm("left", "gs_usb:LEFT", False)
        arm._arm = Mock()
        arm._arm.read.return_value = SimpleNamespace(positions_rad=np.zeros(6), gripper=0.5)
        self.assertEqual(arm.read()[-1], 40.0)
        arm.command([0.0] * 6 + [40.0])
        self.assertEqual(arm._arm.write.call_args.args[0].gripper, 0.5)

    def test_driver_uses_startup_rest_and_stays_read_only_when_checking(self):
        from robots import motor_arm

        driver = Mock()
        driver.read.return_value = SimpleNamespace(positions_rad=np.zeros(6), gripper=0.5)
        with patch.dict(os.environ, {"METAL_GRIPPER_OPEN_DEG": "-96", "METAL_GRIPPER_CLOSED_DEG": "-2.5"}), \
                patch.object(motor_arm, "open_arm", return_value=driver) as opened:
            arm = metal.Arm("left", "gs_usb:LEFT", False)
            arm.connect(energize=False)
        spec = opened.call_args.args[0]
        self.assertEqual(spec.name, "makers")
        self.assertIsNone(spec.rest_rad)
        self.assertFalse(opened.call_args.kwargs["energize"])
        self.assertEqual(arm.rest, [0.0] * 6)

    def test_model_scaling_in_operators(self):
        from policies.dream_1.run import to_model, to_robot
        from utils.hands import Mapping

        state = {key: 0.0 for side in metal.SIDES for key in metal.arm_fields(side)}
        state["left_gripper.pos"] = state["right_gripper.pos"] = 40.0
        row = to_model(state)
        self.assertEqual(row[6], 0.5)
        self.assertEqual(to_robot(row)["right_gripper.pos"], 40.0)
        mapping = Mapping(np.eye(3))
        self.assertAlmostEqual(mapping.grip_mm(mapping.pinch_open_m), 80.0)
        self.assertEqual(mapping.takeover_mm, 8.0)

    def test_invalid_model_and_duplicate_adapter_fail_before_open(self):
        with patch.dict(os.environ, {"MAKERMODS_MODEL": "maker"}):
            with self.assertRaises(ValueError):
                metal.arm_model()
        with patch.dict(os.environ, {"METAL_FOLLOWER_LEFT_CAN": "gs_usb:SAME",
                                     "METAL_FOLLOWER_RIGHT_CAN": "gs_usb:SAME"}):
            with self.assertRaises(ValueError):
                metal._arms("FOLLOWER")

    def test_failed_second_read_only_open_closes_first(self):
        arms = {side: Mock() for side in metal.SIDES}
        arms["right"].connect.side_effect = OSError("unplugged")
        with patch.object(metal, "_arms", return_value=arms):
            with self.assertRaises(OSError):
                metal.check_arms()
        for arm in arms.values():
            arm.close.assert_called_once()
            arm.switch_off.assert_not_called()

    def test_parked_start_never_energizes_or_applies_actions(self):
        arms = {side: Mock() for side in metal.SIDES}
        camera = Mock()
        followers = metal.Followers(arms, {"overhead_camera": camera}, metal.StepLimiter(0.02))
        followers.connect(energize=False)
        self.assertTrue(followers.parked)
        camera._cam.connect.assert_called_once()
        followers.send_action({name: 0.0 for side in metal.SIDES for name in metal.arm_fields(side)})
        for arm in arms.values():
            arm.connect.assert_called_once_with(False)
            arm.command.assert_not_called()
        followers.disconnect()
        for arm in arms.values():
            arm.close.assert_called_once()
            arm.switch_off.assert_not_called()

    def test_default_start_energizes_both_arms(self):
        arms = {side: Mock() for side in metal.SIDES}
        followers = metal.Followers(arms, {}, metal.StepLimiter(0.02))
        followers.connect()
        self.assertFalse(followers.parked)
        for arm in arms.values():
            arm.connect.assert_called_once_with(True)


class UsbTests(unittest.TestCase):
    def test_exact_serial_selection_never_defaults_to_first(self):
        first = SimpleNamespace(serial_number="LEFT", gs_usb=object())
        second = SimpleNamespace(serial_number="RIGHT", gs_usb=object())
        with patch("utils.gs_usb_transport.discover_gs_usb", return_value=[first, second]), \
                patch("utils.gs_usb_transport.dispose_usb") as dispose:
            self.assertIs(select_gs_usb("RIGHT"), second)
            dispose.assert_called_once_with(first.gs_usb)
            with self.assertRaises(DiagnosticError):
                select_gs_usb("MISSING")
        with patch("utils.gs_usb_transport.discover_gs_usb", return_value=[first, first]), \
                patch("utils.gs_usb_transport.dispose_usb"):
            with self.assertRaises(DiagnosticError):
                select_gs_usb("LEFT")

    def bus(self):
        bus = BoundedGsUsb.__new__(BoundedGsUsb)
        bus._is_shutdown = True  # the fake bus never claimed a USB interface
        bus.io_timeout = 0.1
        bus.device = SimpleNamespace(device_flags=0)
        bus.raw = Mock()
        return bus

    def test_submillisecond_usb_read_is_bounded_and_errors_propagate(self):
        bus = self.bus()
        bus.raw.read.side_effect = usb.core.USBTimeoutError("timeout")
        self.assertEqual(bus._recv_internal(0.0003), (None, False))
        self.assertEqual(bus.raw.read.call_args.kwargs["timeout"], 1)
        bus.raw.read.side_effect = usb.core.USBError("disconnected")
        with self.assertRaises(usb.core.USBError):
            bus._recv_internal(0)

    def test_transmit_echo_is_not_motor_feedback(self):
        bus = self.bus()
        packet = GsUsbFrame(can_id=1, data=b"\x00" * 8)
        packet.echo_id = 1
        bus.raw.read.return_value = packet.pack(False)
        self.assertEqual(bus._recv_internal(0.001), (None, False))
        packet.echo_id = GS_USB_NONE_ECHO_ID
        bus.raw.read.return_value = packet.pack(False)
        message, _ = bus._recv_internal(0.001)
        self.assertTrue(message.is_rx)
        self.assertEqual(message.arbitration_id, 1)

    def test_send_timeout_and_short_write(self):
        bus = self.bus()
        bus.raw.write.return_value = 0
        with self.assertRaises(DiagnosticError):
            bus.send(can.Message(arbitration_id=1, is_extended_id=False, data=b"\x00" * 8), timeout=0.0003)
        self.assertEqual(bus.raw.write.call_args.kwargs["timeout"], 1)


if __name__ == "__main__":
    unittest.main()
