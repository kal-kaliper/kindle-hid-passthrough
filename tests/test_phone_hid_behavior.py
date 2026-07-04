import asyncio
import os
import sys
import tempfile
import time
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
MODULE_ROOT = os.path.join(PROJECT_ROOT, "kindle_hid_passthrough")
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)


def install_bumble_stubs():
    bumble = types.ModuleType("bumble")
    sys.modules.setdefault("bumble", bumble)

    core = types.ModuleType("bumble.core")

    class InvalidStateError(Exception):
        pass

    class BumbleTimeoutError(Exception):
        pass

    core.InvalidStateError = InvalidStateError
    core.TimeoutError = BumbleTimeoutError
    core.BT_BR_EDR_TRANSPORT = 1
    core.BT_LE_TRANSPORT = 2
    core.BT_HUMAN_INTERFACE_DEVICE_SERVICE = 0x1124
    core.AdvertisingData = object
    core.DeviceClass = object
    sys.modules.setdefault("bumble.core", core)

    hci = types.ModuleType("bumble.hci")

    class Address:
        PUBLIC_DEVICE_ADDRESS = 0
        RANDOM_DEVICE_ADDRESS = 1
        ANY = "00:00:00:00:00:00"

        def __init__(self, value, address_type=PUBLIC_DEVICE_ADDRESS):
            self.value = value
            self.address_type = address_type
            self.is_resolvable = False

        def __str__(self):
            return str(self.value)

    class OwnAddressType:
        PUBLIC = 0

    class Command:
        def __init__(self, **_kwargs):
            pass

    class PrivacyMode:
        DEVICE_PRIVACY_MODE = 0

    class HCI_LE_Set_Privacy_Mode_Command(Command):
        pass

    HCI_LE_Set_Privacy_Mode_Command.PrivacyMode = PrivacyMode
    hci.Address = Address
    hci.OwnAddressType = OwnAddressType
    hci.HCI_LE_SET_PRIVACY_MODE_COMMAND = object()
    hci.HCI_LE_Set_Privacy_Mode_Command = HCI_LE_Set_Privacy_Mode_Command
    hci.HCI_Write_Class_Of_Device_Command = Command
    hci.HCI_Write_Local_Name_Command = Command
    hci.HCI_LE_Add_Device_To_Filter_Accept_List_Command = Command
    hci.HCI_LE_Clear_Filter_Accept_List_Command = Command
    hci.HCI_LE_Create_Connection_Cancel_Command = Command
    hci.HCI_LE_Create_Connection_Command = Command
    hci.HCI_Write_Scan_Enable_Command = Command
    hci.HCI_LE_ADD_DEVICE_TO_RESOLVING_LIST_COMMAND = object()

    class LeFeatureMask:
        LL_PRIVACY = object()

    hci.LeFeatureMask = LeFeatureMask
    sys.modules.setdefault("bumble.hci", hci)

    device = types.ModuleType("bumble.device")

    class Device:
        EVENT_CONNECTION = "connection"
        EVENT_CONNECTION_FAILURE = "connection_failure"

    class Peer:
        def __init__(self, connection):
            self.connection = connection
            self.services = []

    device.Device = Device
    device.Peer = Peer
    sys.modules.setdefault("bumble.device", device)

    gatt = types.ModuleType("bumble.gatt")
    gatt.GATT_DEVICE_NAME_CHARACTERISTIC = "device-name"
    gatt.GATT_GENERIC_ACCESS_SERVICE = "gap"
    gatt.GATT_HID_CONTROL_POINT_CHARACTERISTIC = "hid-cp"
    gatt.GATT_HUMAN_INTERFACE_DEVICE_SERVICE = "hid"
    gatt.GATT_PROTOCOL_MODE_CHARACTERISTIC = "protocol-mode"
    gatt.GATT_REPORT_CHARACTERISTIC = "report"
    gatt.GATT_REPORT_MAP_CHARACTERISTIC = "report-map"
    gatt.GATT_REPORT_REFERENCE_DESCRIPTOR = "report-reference"
    sys.modules.setdefault("bumble.gatt", gatt)

    hid = types.ModuleType("bumble.hid")

    class Message:
        class MessageType:
            DATA = 0x0A

        class ReportType:
            INPUT_REPORT = 0x01

        class ProtocolMode:
            REPORT_PROTOCOL = 1

    class BumbleHIDHost:
        EVENT_INTERRUPT_DATA = "interrupt"
        EVENT_VIRTUAL_CABLE_UNPLUG = "unplug"

    hid.HID_CONTROL_PSM = 0x11
    hid.HID_INTERRUPT_PSM = 0x13
    hid.Message = Message
    hid.Host = BumbleHIDHost
    sys.modules.setdefault("bumble.hid", hid)

    sdp = types.ModuleType("bumble.sdp")
    sdp.Client = object
    sys.modules.setdefault("bumble.sdp", sdp)

    keys = types.ModuleType("bumble.keys")

    class JsonKeyStore:
        def __init__(self, *_args, **_kwargs):
            pass

    keys.JsonKeyStore = JsonKeyStore
    sys.modules.setdefault("bumble.keys", keys)

    pairing = types.ModuleType("bumble.pairing")

    class PairingDelegate:
        DISPLAY_OUTPUT_AND_YES_NO_INPUT = object()

        def __init__(self, *_args, **_kwargs):
            pass

    class PairingConfig:
        def __init__(self, **_kwargs):
            pass

    pairing.PairingConfig = PairingConfig
    pairing.PairingDelegate = PairingDelegate
    sys.modules.setdefault("bumble.pairing", pairing)

    transport = types.ModuleType("bumble.transport")

    async def open_transport(*_args, **_kwargs):
        raise RuntimeError("not available in tests")

    transport.open_transport = open_transport
    sys.modules.setdefault("bumble.transport", transport)


install_bumble_stubs()

from config import Protocol, config  # noqa: E402
from controller import DaemonController  # noqa: E402
from daemon import HIDDaemon  # noqa: E402
from host import DeviceConfig, DeviceSession, HIDHost  # noqa: E402
from scanner import BLE_APPEARANCE_CATEGORY_PHONE  # noqa: E402


class FakeConnection:
    def __init__(self):
        self.handle = 1
        self.is_disconnected = False

    async def disconnect(self):
        self.is_disconnected = True
        self.handle = None


class FakeUhidDevice:
    def __init__(self):
        self.inputs = []
        self.destroyed = False

    def send_input(self, data):
        self.inputs.append(data)

    def destroy(self):
        self.destroyed = True


class ScanControlTests(unittest.TestCase):
    def test_ble_phone_appearance_category_is_phone(self):
        self.assertEqual(0x01, BLE_APPEARANCE_CATEGORY_PHONE)

    def test_scan_stop_sets_inflight_stop_event(self):
        controller = DaemonController(object())
        controller._scan_stop_event = asyncio.Event()

        controller.request_scan_stop()

        self.assertTrue(controller._scan_stop_event.is_set())


class PhoneHidBehaviorTests(unittest.TestCase):
    ADDR = "AA:BB:CC:11:22:33"

    def setUp(self):
        self._old_serialize = config.classic_serialize_keyboard_reports
        config.classic_serialize_keyboard_reports = True

    def tearDown(self):
        config.classic_serialize_keyboard_reports = self._old_serialize

    def make_host(self):
        cache_dir = tempfile.mkdtemp(prefix="hid-host-test-")
        config.cache_dir = cache_dir
        config.pairing_keys_file = os.path.join(cache_dir, "pairing_keys.json")
        config.devices_config_file = os.path.join(cache_dir, "devices.conf")
        host = HIDHost()
        host._disconnection_event = asyncio.Event()
        host._protocol_disconnection_events = {
            Protocol.CLASSIC: asyncio.Event(),
            Protocol.BLE: asyncio.Event(),
        }
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone")
        ]
        return host

    def make_session(self, protocol):
        session = DeviceSession(
            protocol=protocol,
            address=self.ADDR,
            connection=FakeConnection(),
            uhid_device=FakeUhidDevice(),
            disconnection_event=asyncio.Event(),
        )
        session.established_at = time.monotonic()
        return session

    def test_ble_report_id_zero_does_not_add_prefix(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._on_ble_hid_report(b"\x00\x00\x04\x00\x00\x00\x00\x00", 0)

        self.assertEqual(
            [b"\x00\x00\x04\x00\x00\x00\x00\x00"],
            session.uhid_device.inputs,
        )

    def test_classic_keyboard_overlap_is_serialized_into_taps(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x00\x00\x04\x00\x00\x00\x00",
        )
        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x00\x00\x04\x05\x00\x00\x00",
        )

        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x05\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_remote_drop_without_input_adds_classic_dial_backoff(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertGreater(host._classic_dial_delay(self.ADDR), 0.0)

    def test_classic_session_with_input_clears_backoff(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session
        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        session = self.make_session(Protocol.CLASSIC)
        session.last_report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        host.sessions[Protocol.CLASSIC] = session
        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertEqual(0.0, host._classic_dial_delay(self.ADDR))

    def test_classic_drop_does_not_restart_live_ble_session(self):
        host = self.make_host()
        host.ble_devices = [
            DeviceConfig("AA:BB:CC:44:55:66", Protocol.BLE, "Keyboard")
        ]
        classic = self.make_session(Protocol.CLASSIC)
        ble = self.make_session(Protocol.BLE)
        host.sessions[Protocol.CLASSIC] = classic
        host.sessions[Protocol.BLE] = ble

        scheduled = []
        host._schedule_protocol_restore = scheduled.append

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertFalse(host._disconnection_event.is_set())
        self.assertEqual([Protocol.CLASSIC], scheduled)
        self.assertIn(Protocol.BLE, host.sessions)
        self.assertFalse(ble.uhid_device.destroyed)

    def test_classic_virtual_unplug_does_not_restart_live_ble_session(self):
        host = self.make_host()
        host.ble_devices = [
            DeviceConfig("AA:BB:CC:44:55:66", Protocol.BLE, "Keyboard")
        ]
        classic = self.make_session(Protocol.CLASSIC)
        ble = self.make_session(Protocol.BLE)
        host.sessions[Protocol.CLASSIC] = classic
        host.sessions[Protocol.BLE] = ble

        scheduled = []
        host._schedule_protocol_restore = scheduled.append

        host._on_virtual_cable_unplug()

        self.assertFalse(host._disconnection_event.is_set())
        self.assertEqual([], scheduled)
        self.assertEqual([], host.classic_devices)
        self.assertIn(Protocol.BLE, host.sessions)
        self.assertFalse(ble.uhid_device.destroyed)

    def test_all_backed_off_classic_devices_returns_next_delay(self):
        host = self.make_host()
        host._classic_flap_until[self.ADDR] = time.monotonic() + 120.0

        delay = host._classic_backoff_delay_for_all([self.ADDR])

        self.assertGreater(delay, 0.0)
        self.assertLessEqual(delay, 120.0)

    def test_one_ready_classic_device_keeps_page_scan_enabled(self):
        host = self.make_host()
        host._classic_flap_until[self.ADDR] = time.monotonic() + 120.0

        self.assertEqual(
            0.0,
            host._classic_backoff_delay_for_all([
                self.ADDR,
                "AA:BB:CC:DD:EE:FF",
            ]),
        )


class PowerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_going_to_screensaver_suspends_for_power(self):
        daemon = HIDDaemon()

        await daemon.handle_power_event("goingToScreenSaver")

        self.assertTrue(daemon._suspended)
        self.assertEqual("power", daemon._suspend_reason)
        daemon._power_resume_task.cancel()
        await asyncio.gather(daemon._power_resume_task, return_exceptions=True)

    async def test_operation_resume_is_deferred_during_power_recovery(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "operation"
        daemon._power_blocked = True

        await daemon.resume(reason="operation")

        self.assertTrue(daemon._suspended)
        self.assertTrue(daemon._resume_after_power)
        self.assertEqual("operation", daemon._resume_after_power_reason)

        await daemon.resume(reason="power")

        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._resume_after_power)

    async def test_power_resume_does_not_override_manual_suspend(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "manual"
        daemon._power_blocked = True

        await daemon.resume(reason="power")

        self.assertTrue(daemon._suspended)
        self.assertFalse(daemon._resume_after_power)

    async def test_power_watchdog_resumes_after_missed_wake_event(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "power"
        daemon._power_blocked = True

        await daemon.resume(reason="power-watchdog")

        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._power_blocked)

    async def test_power_watchdog_does_not_override_manual_suspend(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "manual"
        daemon._power_blocked = True

        await daemon.resume(reason="power-watchdog")

        self.assertTrue(daemon._suspended)
        self.assertFalse(daemon._power_blocked)


if __name__ == "__main__":
    unittest.main()
