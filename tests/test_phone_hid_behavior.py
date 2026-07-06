import asyncio
import os
import sys
import tempfile
import time
import types
import unittest
import unittest.mock


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
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class PrivacyMode:
        DEVICE_PRIVACY_MODE = 0

    class HCI_LE_Set_Privacy_Mode_Command(Command):
        pass

    HCI_LE_Set_Privacy_Mode_Command.PrivacyMode = PrivacyMode
    hci.Address = Address
    hci.OwnAddressType = OwnAddressType
    hci.HCI_LE_SET_PRIVACY_MODE_COMMAND = object()
    hci.HCI_LE_Set_Privacy_Mode_Command = HCI_LE_Set_Privacy_Mode_Command
    hci.HCI_Exit_Sniff_Mode_Command = Command
    hci.HCI_Write_Link_Policy_Settings_Command = Command
    hci.HCI_Write_Class_Of_Device_Command = Command
    hci.HCI_Write_Local_Name_Command = Command
    class HCI_LE_Create_Connection_Cancel_Command(Command):
        pass

    class HCI_LE_Create_Connection_Command(Command):
        pass

    hci.HCI_LE_Add_Device_To_Filter_Accept_List_Command = Command
    hci.HCI_LE_Clear_Filter_Accept_List_Command = Command
    hci.HCI_LE_Create_Connection_Cancel_Command = HCI_LE_Create_Connection_Cancel_Command
    hci.HCI_LE_Create_Connection_Command = HCI_LE_Create_Connection_Command
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
        self.is_encrypted = False
        self.peer_address = "AA:BB:CC:44:55:66"
        self.pair_called = False

    async def disconnect(self):
        self.is_disconnected = True
        self.handle = None

    async def encrypt(self):
        self.is_disconnected = True
        self.handle = None
        raise RuntimeError("disconnect during restore")

    async def pair(self):
        self.pair_called = True


class FakeUhidDevice:
    def __init__(self):
        self.name = "Fake UHID"
        self.input_paths = []
        self.inputs = []
        self.destroyed = False

    def send_input(self, data):
        self.inputs.append(data)

    def destroy(self):
        self.destroyed = True


class FakeBleDevice:
    def __init__(self, on_create=None):
        self.commands = []
        self.listeners = {}
        self.le_connecting = False
        self.connect_own_address_type = None
        self.on_create = on_create

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        callbacks = self.listeners.get(event, [])
        if callback in callbacks:
            callbacks.remove(callback)

    async def send_command(self, command, check_result=False):
        name = type(command).__name__
        self.commands.append(name)
        if name == "HCI_LE_Create_Connection_Command" and self.on_create:
            self.on_create()
        if name == "HCI_LE_Create_Connection_Cancel_Command":
            error = types.SimpleNamespace(transport=2)
            for callback in list(self.listeners.get("connection_failure", [])):
                callback(error)


class FakeKeystore:
    async def get(self, _address):
        return object()


class FakeClassicController:
    def __init__(self):
        self.commands = []

    async def send_command(self, command, check_result=False):
        self.commands.append(command)


class FakeClassicDevice:
    def __init__(self):
        self.host = FakeClassicController()


class ScanControlTests(unittest.TestCase):
    def test_ble_phone_appearance_category_is_phone(self):
        self.assertEqual(0x01, BLE_APPEARANCE_CATEGORY_PHONE)

    def test_scan_stop_sets_inflight_stop_event(self):
        controller = DaemonController(object())
        controller._scan_stop_event = asyncio.Event()

        controller.request_scan_stop()

        self.assertTrue(controller._scan_stop_event.is_set())


class ControllerStatusTests(unittest.TestCase):
    def setUp(self):
        self._old_include_reports = config.diagnostics_include_reports

    def tearDown(self):
        config.diagnostics_include_reports = self._old_include_reports

    def test_status_exposes_report_counts_without_raw_report_history(self):
        config.diagnostics_include_reports = False
        daemon = types.SimpleNamespace(
            running=True,
            _suspended=False,
            connection_state={
                "connected": True,
                "connections": [
                    {
                        "address": "AA:BB:CC:11:22:33",
                        "protocol": "classic",
                        "name": "Phone HID App",
                        "source_report_count": 2,
                        "uhid_report_count": 4,
                        "last_source_report": "0100000400000000",
                        "last_uhid_report": "0100000000000000",
                        "recent_source_reports": ["0100000400000000"],
                        "recent_uhid_reports": [
                            "0100000400000000",
                            "0100000000000000",
                        ],
                        "classic_setup_ms": 1200,
                        "classic_channels_ms": 600,
                        "classic_hid_ready_ms": 1100,
                        "classic_channel_origin": "remote",
                        "classic_set_protocol_ok": True,
                    }
                ],
                "address": "AA:BB:CC:11:22:33",
                "protocol": "classic",
                "name": "Phone HID App",
                "source_report_count": 2,
                "uhid_report_count": 4,
                "last_source_report": "0100000400000000",
                "last_uhid_report": "0100000000000000",
                "recent_source_reports": ["0100000400000000"],
                "recent_uhid_reports": [
                    "0100000400000000",
                    "0100000000000000",
                ],
                "classic_setup_ms": 1200,
                "classic_channels_ms": 600,
                "classic_hid_ready_ms": 1100,
                "classic_channel_origin": "remote",
                "classic_set_protocol_ok": True,
            },
        )
        controller = DaemonController(daemon)
        controller._devices_cache = []

        status = controller.get_status()

        self.assertEqual(2, status["source_report_count"])
        self.assertEqual(4, status["uhid_report_count"])
        self.assertNotIn("last_source_report", status)
        self.assertNotIn("last_uhid_report", status)
        self.assertNotIn("recent_source_reports", status)
        self.assertNotIn("recent_uhid_reports", status)
        self.assertNotIn("classic_setup_ms", status)
        self.assertNotIn("classic_channels_ms", status)
        self.assertNotIn("classic_hid_ready_ms", status)
        self.assertNotIn("classic_channel_origin", status)
        self.assertNotIn("classic_set_protocol_ok", status)
        self.assertEqual(2, status["connections"][0]["source_report_count"])
        self.assertNotIn("recent_source_reports", status["connections"][0])

    def test_status_exposes_raw_report_history_in_diagnostic_mode(self):
        config.diagnostics_include_reports = True
        daemon = types.SimpleNamespace(
            running=True,
            _suspended=False,
            connection_state={
                "connected": True,
                "connections": [
                    {
                        "address": "AA:BB:CC:11:22:33",
                        "protocol": "classic",
                        "name": "Phone HID App",
                        "source_report_count": 2,
                        "uhid_report_count": 4,
                        "last_source_report": "0100000400000000",
                        "last_uhid_report": "0100000000000000",
                        "recent_source_reports": ["0100000400000000"],
                        "recent_uhid_reports": [
                            "0100000400000000",
                            "0100000000000000",
                        ],
                    }
                ],
                "address": "AA:BB:CC:11:22:33",
                "protocol": "classic",
                "name": "Phone HID App",
                "source_report_count": 2,
                "uhid_report_count": 4,
                "last_source_report": "0100000400000000",
                "last_uhid_report": "0100000000000000",
                "recent_source_reports": ["0100000400000000"],
                "recent_uhid_reports": [
                    "0100000400000000",
                    "0100000000000000",
                ],
            },
        )
        controller = DaemonController(daemon)
        controller._devices_cache = []

        status = controller.get_status()

        self.assertEqual("0100000400000000", status["last_source_report"])
        self.assertEqual("0100000000000000", status["last_uhid_report"])
        self.assertEqual(["0100000400000000"], status["recent_source_reports"])
        self.assertEqual(
            ["0100000400000000", "0100000000000000"],
            status["connections"][0]["recent_uhid_reports"],
        )


class PhoneHidBehaviorTests(unittest.TestCase):
    ADDR = "AA:BB:CC:11:22:33"

    def setUp(self):
        self._old_serialize = config.classic_serialize_keyboard_reports
        self._old_report_delay = config.classic_serialized_report_delay_ms
        self._old_defer_names = config.classic_defer_uhid_until_input_names
        self._old_idle_retry_names = config.classic_short_idle_retry_names
        self._old_include_reports = config.diagnostics_include_reports
        config.classic_serialize_keyboard_reports = True
        config.classic_serialized_report_delay_ms = 0
        config.classic_defer_uhid_until_input_names = []
        config.classic_short_idle_retry_names = []
        config.diagnostics_include_reports = False

    def tearDown(self):
        config.classic_serialize_keyboard_reports = self._old_serialize
        config.classic_serialized_report_delay_ms = self._old_report_delay
        config.classic_defer_uhid_until_input_names = self._old_defer_names
        config.classic_short_idle_retry_names = self._old_idle_retry_names
        config.diagnostics_include_reports = self._old_include_reports

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
        self.assertEqual("0100000405000000", session.last_source_report_hex)
        self.assertEqual("0100000000000000", session.last_uhid_report_hex)

    def test_session_state_keeps_status_telemetry_minimal(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.classic_setup_ms = 1200
        session.classic_channels_ms = 500
        session.classic_hid_ready_ms = 900
        session.classic_channel_origin = "remote"
        session.classic_set_protocol_ok = False
        session.classic_set_protocol_error = "channel not open"

        state = host._session_state(session)

        self.assertEqual(0, state["source_report_count"])
        self.assertEqual(0, state["uhid_report_count"])
        self.assertNotIn("classic_setup_ms", state)
        self.assertNotIn("classic_channels_ms", state)
        self.assertNotIn("classic_hid_ready_ms", state)
        self.assertNotIn("classic_channel_origin", state)
        self.assertNotIn("classic_set_protocol_ok", state)
        self.assertNotIn("classic_set_protocol_error", state)

    def test_disconnect_warning_omits_raw_reports_by_default(self):
        host = self.make_host()
        host._schedule_protocol_restore = lambda _protocol: None
        session = self.make_session(Protocol.CLASSIC)
        session.last_source_report_hex = "0100000400000000"
        session.last_uhid_report_hex = "0100000000000000"
        host.sessions[Protocol.CLASSIC] = session

        with self.assertLogs("ble_hid", level="WARNING") as captured:
            host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        output = "\n".join(captured.output)
        self.assertIn("Device disconnected", output)
        self.assertNotIn("0100000400000000", output)
        self.assertNotIn("0100000000000000", output)

    def test_drop_warning_omits_raw_report_by_default(self):
        host = self.make_host()
        host.sessions[Protocol.BLE] = self.make_session(Protocol.BLE)

        with self.assertLogs("ble_hid", level="WARNING") as captured:
            host._forward_report_for_protocol(
                Protocol.CLASSIC,
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
            )

        output = "\n".join(captured.output)
        self.assertIn("Dropping report without live session", output)
        self.assertNotIn("0100000400000000", output)

    def test_classic_keyboard_report_serializes_all_key_slots(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x00\x00\x04\x05\x06\x07\x08",
        )

        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x05\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x06\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x07\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x08\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_classic_repeated_key_report_is_not_dropped(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        host._forward_report_for_protocol(Protocol.CLASSIC, report)
        host._forward_report_for_protocol(Protocol.CLASSIC, report)

        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_classic_serialized_report_preserves_modifier(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x02\x00\x38\x00\x00\x00\x00",
        )

        self.assertEqual(
            [
                b"\x01\x02\x00\x38\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_classic_serialized_reports_are_paced(self):
        config.classic_serialized_report_delay_ms = 8
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        with unittest.mock.patch("host.time.sleep") as sleep:
            host._forward_report_for_protocol(
                Protocol.CLASSIC,
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
            )

        sleep.assert_called_once_with(0.008)

    def test_remote_drop_without_input_adds_classic_dial_backoff(self):
        host = self.make_host()
        host._schedule_protocol_restore = lambda _protocol: None
        session = self.make_session(Protocol.CLASSIC)
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertGreater(host._classic_dial_delay(self.ADDR), 0.0)

    def test_classic_session_with_input_clears_backoff(self):
        host = self.make_host()
        host._schedule_protocol_restore = lambda _protocol: None
        session = self.make_session(Protocol.CLASSIC)
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session
        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        session = self.make_session(Protocol.CLASSIC)
        session.last_report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        session.source_report_count = 1
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

    def test_idle_classic_drop_restores_when_ble_configured_but_not_live(self):
        host = self.make_host()
        host.ble_devices = [
            DeviceConfig("AA:BB:CC:44:55:66", Protocol.BLE, "Keyboard")
        ]
        classic = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = classic

        scheduled = []
        host._schedule_protocol_restore = scheduled.append

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertFalse(host._disconnection_event.is_set())
        self.assertEqual([Protocol.CLASSIC], scheduled)
        self.assertGreater(host._classic_dial_delay(self.ADDR), 0.0)

    def test_configured_phone_classic_link_can_be_parked_until_input(self):
        config.classic_defer_uhid_until_input_names = ["Phone HID App"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone HID App")
        ]
        host.current_device_address = self.ADDR
        host.device_name = "Phone HID App\x00"
        host.connection = FakeConnection()
        host.hid_host = object()
        host.report_map = b"descriptor"
        host.connected_protocol = Protocol.CLASSIC

        host._park_classic_session_until_input()

        self.assertTrue(host._is_classic_parked())
        self.assertNotIn(Protocol.CLASSIC, host.sessions)
        self.assertIsNone(host._classic_pending_session.uhid_device)

    def test_parked_classic_phone_promotes_on_first_input(self):
        config.classic_defer_uhid_until_input_names = ["Phone HID App"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone HID App")
        ]
        host.current_device_address = self.ADDR
        host.device_name = "Phone HID App\x00"
        host.connection = FakeConnection()
        host.hid_host = object()
        host.report_map = b"descriptor"
        host.connected_protocol = Protocol.CLASSIC
        host._park_classic_session_until_input()

        def finalize():
            host.uhid_device = FakeUhidDevice()
            host._uhid_created_at = time.monotonic()

        host._finalize_classic_hid = finalize

        host._on_classic_interrupt_data(
            b"\xA1\x01\x00\x00\x04\x00\x00\x00\x00"
        )

        self.assertIsNone(host._classic_pending_session)
        self.assertIn(Protocol.CLASSIC, host.sessions)
        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            host.sessions[Protocol.CLASSIC].uhid_device.inputs,
        )

    def test_parked_classic_phone_drop_keeps_host_alive(self):
        config.classic_defer_uhid_until_input_names = ["Phone HID App"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone HID App")
        ]
        host.current_device_address = self.ADDR
        host.device_name = "Phone HID App\x00"
        host.connection = FakeConnection()
        host.hid_host = object()
        host.report_map = b"descriptor"
        host.connected_protocol = Protocol.CLASSIC
        host._park_classic_session_until_input()
        host._classic_pending_session.established_at = time.monotonic() - 2.0

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertFalse(host._disconnection_event.is_set())
        self.assertIsNone(host._classic_pending_session)
        self.assertGreater(host._classic_dial_delay(self.ADDR), 0.0)
        self.assertLessEqual(
            host._classic_dial_delay(self.ADDR),
            host.CLASSIC_PARKED_RETRY_DELAY,
        )
        self.assertNotIn(self.ADDR, host._classic_flap_counts)

    def test_configured_phone_idle_drop_with_uhid_uses_short_retry(self):
        config.classic_short_idle_retry_names = ["Phone HID App"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone HID App")
        ]
        session = self.make_session(Protocol.CLASSIC)
        session.device_name = "Phone HID App\x00"
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session
        host._schedule_protocol_restore = lambda _protocol: None

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertGreater(host._classic_dial_delay(self.ADDR), 0.0)
        self.assertLessEqual(
            host._classic_dial_delay(self.ADDR),
            host.CLASSIC_PARKED_RETRY_DELAY,
        )
        self.assertNotIn(self.ADDR, host._classic_flap_counts)

    def test_configured_phone_idle_drop_restores_without_host_restart(self):
        config.classic_short_idle_retry_names = ["Phone HID App"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone HID App")
        ]
        session = self.make_session(Protocol.CLASSIC)
        session.device_name = "Phone HID App\x00"
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session
        scheduled = []
        host._schedule_protocol_restore = scheduled.append

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertFalse(host._disconnection_event.is_set())
        self.assertEqual([Protocol.CLASSIC], scheduled)

    def test_phone_idle_drop_waits_longer_when_ble_is_pending(self):
        config.classic_short_idle_retry_names = ["Phone HID App"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Phone HID App")
        ]
        host.ble_devices = [
            DeviceConfig("AA:BB:CC:44:55:66", Protocol.BLE, "Keyboard")
        ]
        session = self.make_session(Protocol.CLASSIC)
        session.device_name = "Phone HID App\x00"
        session.established_at = time.monotonic() - 2.0
        host.sessions[Protocol.CLASSIC] = session
        host._schedule_protocol_restore = lambda _protocol: None

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertGreaterEqual(
            host._classic_dial_delay(self.ADDR),
            host.CLASSIC_AUTH_RETRY_DELAY_WITH_PENDING_BLE - 1.0,
        )

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

    def test_classic_restore_reuses_existing_listener(self):
        async def scenario():
            host = self.make_host()
            host._classic_connection_listener = object()
            active_calls = []
            full_calls = []

            async def active_loop(addresses):
                active_calls.append(addresses)

            async def full_handler():
                full_calls.append(True)

            host._classic_active_connect_loop = active_loop
            host._run_classic_handler = full_handler

            host._schedule_protocol_restore(Protocol.CLASSIC)
            await asyncio.gather(*host._connection_tasks)

            return active_calls, full_calls

        active_calls, full_calls = asyncio.run(scenario())

        self.assertEqual([[self.ADDR]], active_calls)
        self.assertEqual([], full_calls)

    def test_classic_active_connect_loop_does_not_stack(self):
        async def scenario():
            host = self.make_host()
            existing = asyncio.create_task(asyncio.sleep(30))
            host._classic_active_connect_task = existing
            try:
                await host._classic_active_connect_loop([self.ADDR])
            finally:
                existing.cancel()
                try:
                    await existing
                except asyncio.CancelledError:
                    pass
            return host._classic_active_connect_task

        active_task = asyncio.run(scenario())

        self.assertTrue(active_task.cancelled())

    def test_classic_hid_channels_must_be_open(self):
        class State:
            CLOSED = "closed"
            OPEN = "open"
            WAIT_DISCONNECT = "wait-disconnect"

        closed_ctrl = types.SimpleNamespace(state=State.CLOSED, State=State)
        open_intr = types.SimpleNamespace(state=State.OPEN, State=State)
        hid_host = types.SimpleNamespace(
            l2cap_ctrl_channel=closed_ctrl,
            l2cap_intr_channel=open_intr,
        )
        host = self.make_host()

        self.assertFalse(host._classic_hid_channels_open(hid_host))

        host._classic_clear_closed_l2cap_channels(hid_host)

        self.assertIsNone(hid_host.l2cap_ctrl_channel)
        self.assertIs(open_intr, hid_host.l2cap_intr_channel)

    def test_stale_classic_report_does_not_hit_live_ble_uhid(self):
        host = self.make_host()
        ble = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = ble
        host.uhid_device = FakeUhidDevice()
        host.uhid_device.destroy()

        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x00\x00\x04\x00\x00\x00\x00",
        )

        self.assertEqual([], ble.uhid_device.inputs)
        self.assertEqual([], host.uhid_device.inputs)

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

    def test_all_backed_off_classic_devices_keep_passive_page_scan_enabled(self):
        async def scenario():
            host = self.make_host()
            host.ACTIVE_DELAY = 0
            host.device = FakeClassicDevice()
            host._classic_page_scan_enabled = False
            host._classic_flap_until[self.ADDR] = time.monotonic() + 120.0

            task = asyncio.create_task(host._classic_active_connect_loop([self.ADDR]))
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return host.device.host.commands

        commands = asyncio.run(scenario())

        self.assertEqual(0x02, commands[0].kwargs["scan_enable"])

    def test_ble_initiate_aborts_when_classic_setup_starts(self):
        async def scenario():
            host = self.make_host()

            def start_classic_setup():
                host.connected_protocol = Protocol.CLASSIC
                host.connection = FakeConnection()

            host.device = FakeBleDevice(on_create=start_classic_setup)
            host._radio_lock = asyncio.Lock()

            result = await host._ble_initiate(5.0)
            return host, result

        host, result = asyncio.run(scenario())

        self.assertIsNone(result)
        self.assertEqual(
            [
                "HCI_LE_Create_Connection_Command",
                "HCI_LE_Create_Connection_Cancel_Command",
            ],
            host.device.commands,
        )
        self.assertFalse(host.device.le_connecting)

    def test_ble_uses_frequent_windows_when_classic_is_only_configured(self):
        host = self.make_host()

        self.assertEqual(
            host.BLE_CLASSIC_IDLE_WINDOW,
            host._ble_window_for_radio_state(host.BLE_INIT_WINDOW),
        )
        self.assertEqual(
            host.BLE_CLASSIC_IDLE_RETRY_DELAY,
            host._ble_coexist_pause_delay(),
        )

    def test_ble_uses_conservative_windows_when_classic_is_live(self):
        host = self.make_host()
        host.sessions[Protocol.CLASSIC] = self.make_session(Protocol.CLASSIC)

        self.assertEqual(
            host.BLE_COEXIST_WINDOW,
            host._ble_window_for_radio_state(host.BLE_INIT_WINDOW),
        )
        self.assertEqual(
            host.BLE_COEXIST_RETRY_DELAY,
            host._ble_coexist_pause_delay(),
        )

    def test_ble_restore_disconnect_does_not_fall_through_to_pair(self):
        async def scenario():
            host = self.make_host()
            connection = FakeConnection()
            host.connection = connection
            host.connected_protocol = Protocol.BLE
            host.device = types.SimpleNamespace(keystore=FakeKeystore())
            host._protocol_disconnection_events[Protocol.BLE].set()

            with self.assertRaises(Exception) as raised:
                await host._ble_restore_or_pair()

            return connection, str(raised.exception)

        connection, error = asyncio.run(scenario())

        self.assertIn("Disconnected during bonding restore", error)
        self.assertFalse(connection.pair_called)


class DaemonHostStateTests(unittest.TestCase):
    ADDR = "AA:BB:CC:11:22:33"

    def test_daemon_carries_classic_backoff_between_hosts(self):
        cache_dir = tempfile.mkdtemp(prefix="hid-daemon-test-")
        config.cache_dir = cache_dir
        config.pairing_keys_file = os.path.join(cache_dir, "pairing_keys.json")
        config.devices_config_file = os.path.join(cache_dir, "devices.conf")
        daemon = HIDDaemon()
        first_host = HIDHost()
        retry_until = time.monotonic() + 15.0
        flap_until = time.monotonic() + 30.0
        first_host._classic_flap_counts[self.ADDR] = 2
        first_host._classic_flap_until[self.ADDR] = flap_until
        first_host._classic_retry_not_before = retry_until

        daemon._capture_host_state(first_host)

        second_host = HIDHost()
        daemon._seed_host_state(second_host)

        self.assertIs(second_host._classic_flap_counts, daemon._classic_flap_counts)
        self.assertIs(second_host._classic_flap_until, daemon._classic_flap_until)
        self.assertEqual(2, second_host._classic_flap_counts[self.ADDR])
        self.assertEqual(flap_until, second_host._classic_flap_until[self.ADDR])
        self.assertEqual(retry_until, second_host._classic_retry_not_before)


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
