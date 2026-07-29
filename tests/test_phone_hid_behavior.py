import asyncio
import configparser
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
            # Real Bumble appends a transport suffix to Classic/public
            # addresses ("/P" for public, "/R" for random). Reproduce that
            # so tests exercise the same normalization the daemon needs.
            text = str(self.value)
            if "/" in text or text == Address.ANY:
                return text
            suffix = "/P" if self.address_type == Address.PUBLIC_DEVICE_ADDRESS else "/R"
            return f"{text}{suffix}"

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

        def __init__(self, *args, **kwargs):
            pass

        def on(self, *args, **kwargs):
            pass

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

from bumble.hci import Address  # noqa: E402
from config import Protocol, config  # noqa: E402
from controller import DaemonController  # noqa: E402
from daemon import HIDDaemon  # noqa: E402
from host import DeviceConfig, DeviceSession, HIDHost  # noqa: E402
from scanner import BLE_APPEARANCE_CATEGORY_PHONE  # noqa: E402
from bt_chip import BtChip  # noqa: E402
from bt_mtk import MtkChip  # noqa: E402
import transport as transport_module  # noqa: E402
import daemon as daemon_module  # noqa: E402
from wifi_ready import WifiReadiness, wifi_readiness  # noqa: E402


class FakeConnection:
    def __init__(self, peer_address="AA:BB:CC:44:55:66", encrypt_succeeds=False):
        self.handle = 1
        self.is_disconnected = False
        self.is_encrypted = False
        self.peer_address = peer_address
        self.pair_called = False
        self.encrypt_succeeds = encrypt_succeeds

    async def disconnect(self):
        self.is_disconnected = True
        self.handle = None

    async def encrypt(self):
        if self.encrypt_succeeds:
            self.is_encrypted = True
            return
        self.is_disconnected = True
        self.handle = None
        raise RuntimeError("disconnect during restore")

    async def pair(self):
        self.pair_called = True


class FakeResolvableAddress:
    """A BLE peer address that resolves (via IRK) to a different identity
    address, mimicking a rotating RPA."""

    def __init__(self, rpa):
        self.value = rpa
        self.is_resolvable = True

    def __str__(self):
        return self.value


class RecordingKeystore:
    """Keystore that records which addresses get()/delete() are called with
    and only returns keys for addresses it was seeded with."""

    def __init__(self, known_addresses=()):
        self.known = set(known_addresses)
        self.requested = []
        self.deleted = []

    async def get(self, address):
        self.requested.append(address)
        return object() if address in self.known else None

    async def delete(self, address):
        self.deleted.append(address)


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
        self.host = FakeClassicController()

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
    def __init__(self):
        self.deleted = []

    async def get(self, _address):
        return object()

    async def delete(self, address):
        self.deleted.append(address)


class FakeClassicController:
    def __init__(self):
        self.commands = []
        self._listeners = {}

    async def send_command(self, command, check_result=False):
        self.commands.append(command)

    def on(self, event, handler):
        self._listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        callbacks = self._listeners.get(event, [])
        if handler in callbacks:
            callbacks.remove(handler)

    def emit(self, event, *args):
        for handler in list(self._listeners.get(event, [])):
            handler(*args)


class FakeClassicDevice:
    def __init__(self):
        self.host = FakeClassicController()
        self.le_connecting = False
        self._listeners = {}

    def on(self, event, handler):
        self._listeners[event] = handler

    def remove_listener(self, event, handler):
        self._listeners.pop(event, None)


class ScanControlTests(unittest.TestCase):
    def test_ble_phone_appearance_category_is_phone(self):
        self.assertEqual(0x01, BLE_APPEARANCE_CATEGORY_PHONE)

    def test_scan_stop_sets_inflight_stop_event(self):
        controller = DaemonController(object())
        controller._scan_stop_event = asyncio.Event()

        controller.request_scan_stop()

        self.assertTrue(controller._scan_stop_event.is_set())


class DaemonRunLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_suspend_during_power_warmup_prevents_normal_host_start(self):
        daemon = HIDDaemon()
        daemon._has_devices = lambda log_details=False: True
        host_starts = []

        class FakeChip:
            def ensure_powered(self):
                daemon._suspended = True
                daemon._suspend_reason = "operation"

        class FakeHost:
            def __init__(self):
                host_starts.append("created")

            async def run(self):
                host_starts.append("run")

        with unittest.mock.patch.object(daemon_module, "chip", return_value=FakeChip()), \
                unittest.mock.patch.object(daemon_module, "HIDHost", FakeHost):
            run_task = asyncio.create_task(daemon.run())
            for _ in range(20):
                if daemon._suspended:
                    break
                await asyncio.sleep(0.01)

            self.assertTrue(daemon._suspended)
            self.assertEqual([], host_starts)

            daemon.running = False
            daemon._resume_event.set()
            await asyncio.wait_for(run_task, timeout=1.0)


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


class MtkPowerTests(unittest.TestCase):
    def test_mtk_chip_arms_power_monitor(self):
        self.assertFalse(MtkChip(None).survives_suspend)

    def test_mtk_power_off_releases_stpbt_and_unloads_loaded_module(self):
        chip = MtkChip(None)
        chip._powered = True

        completed = types.SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        with unittest.mock.patch("bt_mtk.os.path.exists", return_value=True), \
                unittest.mock.patch(
                    "bt_mtk._is_device_free",
                    side_effect=[False, False, True],
                ) as is_free, \
                unittest.mock.patch(
                    "bt_mtk._release_own_fds",
                    return_value=1,
                ) as release_own_fds, \
                unittest.mock.patch(
                    "bt_mtk.free_device",
                ) as free_device, \
                unittest.mock.patch(
                    "bt_mtk._kill_holders_via_proc",
                    return_value=1,
                ) as kill_holders, \
                unittest.mock.patch(
                    "bt_mtk._find_bt_module",
                    return_value="/lib/modules/test/extra/wmt_cdev_bt.ko",
                ), \
                unittest.mock.patch(
                    "bt_mtk._is_module_name_loaded",
                    side_effect=lambda name: name == "wmt_cdev_bt",
                ), \
                unittest.mock.patch(
                    "bt_mtk.subprocess.run",
                    return_value=completed,
                ) as run_cmd:
            chip.power_off()

        self.assertFalse(chip._powered)
        release_own_fds.assert_called_once_with("/dev/stpbt")
        free_device.assert_not_called()
        kill_holders.assert_called_once_with("/dev/stpbt")
        run_cmd.assert_called_once()
        self.assertEqual(["/sbin/rmmod", "wmt_cdev_bt"], run_cmd.call_args[0][0])
        self.assertEqual(3, is_free.call_count)

    def test_mtk_quiesce_releases_fds_without_unloading_module(self):
        chip = MtkChip(None)
        chip._powered = True

        with unittest.mock.patch("bt_mtk.os.path.exists", return_value=True), \
                unittest.mock.patch(
                    "bt_mtk._release_own_fds",
                    return_value=1,
                ) as release_own_fds, \
                unittest.mock.patch("bt_mtk._find_bt_module") as find_module, \
                unittest.mock.patch("bt_mtk.subprocess.run") as run_cmd:
            chip.quiesce()

        self.assertFalse(chip._powered)
        release_own_fds.assert_called_once_with("/dev/stpbt")
        find_module.assert_not_called()
        run_cmd.assert_not_called()

    def test_chip_without_quiesce_support_falls_back_to_power_off(self):
        # The suspend path calls quiesce() unconditionally. Chips that have no
        # lighter option (e.g. Broadcom) must keep powering off rather than
        # raising AttributeError and stranding the daemon suspended.
        calls = []

        class PowerOffOnlyChip(BtChip):
            def power_off(self):
                calls.append("power_off")

        PowerOffOnlyChip(None).quiesce()

        self.assertEqual(["power_off"], calls)


class TransportRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_hci_reset_timeout_closes_transport_before_chip_recovery(self):
        events = []
        old_timeout = config.hci_reset_timeout
        config.hci_reset_timeout = 0.01

        class FakeTransport:
            source = object()
            sink = object()

            async def close(self):
                events.append("close")

        class FakeHost:
            async def reset(self):
                events.append("reset")
                await asyncio.sleep(3600)

        class FakeDevice:
            host = FakeHost()

        class FakeDeviceFactory:
            @staticmethod
            def with_hci(*_args, **_kwargs):
                events.append("with_hci")
                return FakeDevice()

        class FakeChip:
            def on_transport_open(self):
                events.append("transport_open_hook")

            def on_hci_reset_timeout(self):
                events.append("reset_timeout_hook")

        async def fake_open_transport(_spec):
            events.append("open_transport")
            return FakeTransport()

        try:
            with unittest.mock.patch.object(
                    transport_module, "open_transport", fake_open_transport), \
                    unittest.mock.patch.object(
                        transport_module, "Device", FakeDeviceFactory), \
                    unittest.mock.patch(
                        "bt_setup.chip", return_value=FakeChip()):
                with self.assertRaises(asyncio.TimeoutError):
                    await transport_module.create_bumble_device("file:/dev/stpbt")
        finally:
            config.hci_reset_timeout = old_timeout

        self.assertEqual(1, events.count("close"))
        self.assertLess(events.index("close"), events.index("reset_timeout_hook"))


class WifiReadinessTests(unittest.TestCase):
    def test_wifi_readiness_accepts_connected_wifi(self):
        lipc = {
            ('com.lab126.cmd', 'wirelessEnable'): '1',
            ('com.lab126.wifid', 'enable'): '1',
            ('com.lab126.cmd', 'activeInterface'): 'wifi',
            ('com.lab126.wifid', 'cmState'): 'CONNECTED',
        }

        with unittest.mock.patch(
                "wifi_ready._lipc_get",
                side_effect=lambda service, prop: lipc[(service, prop)]), \
                unittest.mock.patch(
                    "wifi_ready._read_text",
                    side_effect=lambda path: {
                        '/sys/class/net/wlan0/operstate': 'up',
                        '/sys/class/net/wlan0/carrier': '1',
                    }[path]), \
                unittest.mock.patch(
                    "wifi_ready._ifconfig_ipv4",
                    return_value='192.168.100.143'), \
                unittest.mock.patch(
                    "wifi_ready._has_default_route",
                    return_value=True), \
                unittest.mock.patch(
                    "wifi_ready.os.path.exists",
                    return_value=True):
            readiness = wifi_readiness()

        self.assertTrue(readiness.ready)
        self.assertEqual('ready', readiness.reason)
        self.assertEqual('192.168.100.143', readiness.details['ipv4'])

    def test_wifi_readiness_waits_for_wifid_connected_state(self):
        lipc = {
            ('com.lab126.cmd', 'wirelessEnable'): '1',
            ('com.lab126.wifid', 'enable'): '1',
            ('com.lab126.cmd', 'activeInterface'): 'wifi',
            ('com.lab126.wifid', 'cmState'): 'CONNECTING',
        }

        with unittest.mock.patch(
                "wifi_ready._lipc_get",
                side_effect=lambda service, prop: lipc[(service, prop)]), \
                unittest.mock.patch(
                    "wifi_ready._read_text",
                    side_effect=lambda path: {
                        '/sys/class/net/wlan0/operstate': 'up',
                        '/sys/class/net/wlan0/carrier': '1',
                    }[path]), \
                unittest.mock.patch(
                    "wifi_ready._ifconfig_ipv4",
                    return_value='192.168.100.143'), \
                unittest.mock.patch(
                    "wifi_ready._has_default_route",
                    return_value=True), \
                unittest.mock.patch(
                    "wifi_ready.os.path.exists",
                    return_value=True):
            readiness = wifi_readiness()

        self.assertFalse(readiness.ready)
        self.assertIn('cmState=CONNECTING', readiness.reason)

    def test_wifi_readiness_waits_for_default_route(self):
        lipc = {
            ('com.lab126.cmd', 'wirelessEnable'): '1',
            ('com.lab126.wifid', 'enable'): '1',
            ('com.lab126.cmd', 'activeInterface'): 'wifi',
            ('com.lab126.wifid', 'cmState'): 'CONNECTED',
        }

        with unittest.mock.patch(
                "wifi_ready._lipc_get",
                side_effect=lambda service, prop: lipc[(service, prop)]), \
                unittest.mock.patch(
                    "wifi_ready._read_text",
                    side_effect=lambda path: {
                        '/sys/class/net/wlan0/operstate': 'up',
                        '/sys/class/net/wlan0/carrier': '1',
                    }[path]), \
                unittest.mock.patch(
                    "wifi_ready._ifconfig_ipv4",
                    return_value='192.168.100.143'), \
                unittest.mock.patch(
                    "wifi_ready._has_default_route",
                    return_value=False), \
                unittest.mock.patch(
                    "wifi_ready.os.path.exists",
                    return_value=True):
            readiness = wifi_readiness()

        self.assertFalse(readiness.ready)
        self.assertIn('default_route=missing', readiness.reason)

    def test_wifi_readiness_does_not_block_when_wifi_is_disabled(self):
        with unittest.mock.patch(
                "wifi_ready._lipc_get",
                side_effect=lambda service, prop: (
                    '0' if (service, prop) == (
                        'com.lab126.cmd', 'wirelessEnable') else None
                )), \
                unittest.mock.patch(
                    "wifi_ready._read_text",
                    return_value=None), \
                unittest.mock.patch(
                    "wifi_ready._ifconfig_ipv4",
                    return_value=None), \
                unittest.mock.patch(
                    "wifi_ready._has_default_route",
                    return_value=False), \
                unittest.mock.patch(
                    "wifi_ready.os.path.exists",
                    return_value=True):
            readiness = wifi_readiness()

        self.assertTrue(readiness.ready)
        self.assertEqual('wifi-disabled', readiness.reason)


class PhoneHidBehaviorTests(unittest.TestCase):
    ADDR = "AA:BB:CC:11:22:33"

    def setUp(self):
        self._old_serialize_mode = config.classic_serialize_keyboard_reports_mode
        self._old_report_delay = config.classic_serialized_report_delay_ms
        self._old_modifier_mask = config.classic_keyboard_modifier_mask
        self._old_ble_kindle_text_mode = config.ble_kindle_text_mode
        self._old_ble_serialize_mode = config.ble_serialize_keyboard_reports_mode
        self._old_ble_report_delay = config.ble_serialized_report_delay_ms
        self._old_ble_modifier_mask = config.ble_keyboard_modifier_mask
        self._old_defer_names = config.classic_defer_uhid_until_input_names
        self._old_idle_retry_names = config.classic_short_idle_retry_names
        self._old_passive_names = config.classic_passive_names
        self._old_include_reports = config.diagnostics_include_reports
        self._old_ble_remap_home = config.ble_remap_consumer_home_to_escape
        config.classic_serialize_keyboard_reports_mode = 'always'
        config.classic_serialized_report_delay_ms = 0
        config.classic_keyboard_modifier_mask = 0xff
        config.ble_kindle_text_mode = True
        config.ble_serialize_keyboard_reports_mode = 'always'
        config.ble_serialized_report_delay_ms = 0
        config.ble_keyboard_modifier_mask = 0x22
        config.classic_defer_uhid_until_input_names = []
        config.classic_short_idle_retry_names = []
        config.classic_passive_names = []
        config.diagnostics_include_reports = False
        config.ble_remap_consumer_home_to_escape = False

    def tearDown(self):
        config.classic_serialize_keyboard_reports_mode = self._old_serialize_mode
        config.classic_serialized_report_delay_ms = self._old_report_delay
        config.classic_keyboard_modifier_mask = self._old_modifier_mask
        config.ble_kindle_text_mode = self._old_ble_kindle_text_mode
        config.ble_serialize_keyboard_reports_mode = self._old_ble_serialize_mode
        config.ble_serialized_report_delay_ms = self._old_ble_report_delay
        config.ble_keyboard_modifier_mask = self._old_ble_modifier_mask
        config.classic_defer_uhid_until_input_names = self._old_defer_names
        config.classic_short_idle_retry_names = self._old_idle_retry_names
        config.classic_passive_names = self._old_passive_names
        config.diagnostics_include_reports = self._old_include_reports
        config.ble_remap_consumer_home_to_escape = self._old_ble_remap_home

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

    def test_device_cache_records_and_reads_is_phone(self):
        host = self.make_host()
        self.assertIsNone(host.device_cache.get_is_phone(self.ADDR))
        host.device_cache.set_class(self.ADDR, True)
        self.assertTrue(host.device_cache.get_is_phone(self.ADDR))
        host.device_cache.set_class(self.ADDR, False)
        self.assertFalse(host.device_cache.get_is_phone(self.ADDR))

    def test_descriptor_save_preserves_is_phone(self):
        host = self.make_host()
        host.device_cache.set_class(self.ADDR, True)
        # A later descriptor save (which carries no is_phone) must not clobber
        # the recorded device class.
        host.device_cache.save(self.ADDR, {
            "report_map": "0501",
            "device_name": "Phone",
        })
        self.assertTrue(host.device_cache.get_is_phone(self.ADDR))
        cache = host.device_cache.load(self.ADDR)
        self.assertEqual("0501", cache["report_map"])

    def test_status_exposes_is_phone_only_when_known(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.is_phone = True
        self.assertTrue(host._session_state(session)["is_phone"])
        session.is_phone = False
        self.assertFalse(host._session_state(session)["is_phone"])
        session.is_phone = None
        self.assertNotIn("is_phone", host._session_state(session))

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

    def test_classic_auto_serializes_phone_keyboard_reports(self):
        config.classic_serialize_keyboard_reports_mode = 'auto'
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.is_phone = True
        host.sessions[Protocol.CLASSIC] = session

        host._forward_report_for_protocol(
            Protocol.CLASSIC, b"\x01\x00\x00\x04\x00\x00\x00\x00")
        host._forward_report_for_protocol(
            Protocol.CLASSIC, b"\x01\x00\x00\x04\x05\x00\x00\x00")

        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x00\x00\x05\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_classic_auto_forwards_physical_keyboard_reports(self):
        config.classic_serialize_keyboard_reports_mode = 'auto'
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.is_phone = False
        host.sessions[Protocol.CLASSIC] = session

        report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        host._forward_report_for_protocol(Protocol.CLASSIC, report)
        host._forward_report_for_protocol(Protocol.CLASSIC, report)

        self.assertEqual([report, report], session.uhid_device.inputs)

    def test_classic_auto_resolves_a_class_cached_after_session_start(self):
        # is_phone on the session is a snapshot. The inbound connection_request
        # handler and the manual scan both write the class independently of
        # session setup, so a session that began before classification must
        # still pick it up rather than stay stuck on the snapshot.
        config.classic_serialize_keyboard_reports_mode = 'auto'
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session
        self.assertIsNone(session.is_phone)

        host.device_cache.set_class(self.ADDR, True)
        host._forward_report_for_protocol(
            Protocol.CLASSIC, b"\x01\x00\x00\x04\x00\x00\x00\x00")

        self.assertTrue(session.is_phone)
        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_classic_auto_warns_once_when_device_class_stays_unknown(self):
        # A device this host always dials first is never classified by either
        # writer, so 'auto' silently picks the physical-keyboard branch. That
        # is the safe direction but it must be visible, and the cache re-read
        # must not happen per report: this is the report path.
        config.classic_serialize_keyboard_reports_mode = 'auto'
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        with unittest.mock.patch.object(
            host.device_cache, "get_is_phone", return_value=None
        ) as get_is_phone, unittest.mock.patch("host.log.warning") as warn:
            host._forward_report_for_protocol(Protocol.CLASSIC, report)
            host._forward_report_for_protocol(Protocol.CLASSIC, report)

        self.assertEqual(1, get_is_phone.call_count)
        self.assertEqual(1, warn.call_count)
        self.assertIn("Device class unknown", warn.call_args[0][0])
        self.assertEqual([report, report], session.uhid_device.inputs)

    def test_classic_auto_forwards_unknown_keyboard_reports(self):
        config.classic_serialize_keyboard_reports_mode = 'auto'
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        host._forward_report_for_protocol(Protocol.CLASSIC, report)

        self.assertEqual([report], session.uhid_device.inputs)

    def test_classic_always_serializes_non_phone_keyboard_reports(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.is_phone = False
        host.sessions[Protocol.CLASSIC] = session

        host._forward_report_for_protocol(
            Protocol.CLASSIC, b"\x01\x00\x00\x04\x00\x00\x00\x00")

        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_classic_never_forwards_phone_keyboard_reports(self):
        config.classic_serialize_keyboard_reports_mode = 'never'
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.is_phone = True
        host.sessions[Protocol.CLASSIC] = session

        report = b"\x01\x00\x00\x04\x00\x00\x00\x00"
        host._forward_report_for_protocol(Protocol.CLASSIC, report)

        self.assertEqual([report], session.uhid_device.inputs)

    def test_legacy_serialize_keyboard_report_values_parse_as_modes(self):
        parser = configparser.ConfigParser()
        parser.read_dict({'classic': {'serialize_keyboard_reports': 'true'}})
        previous_parser = config._parser
        config._parser = parser
        try:
            self.assertEqual(
                'always',
                config._get_tristate('classic', 'serialize_keyboard_reports', 'auto'),
            )
            parser.set('classic', 'serialize_keyboard_reports', 'false')
            self.assertEqual(
                'never',
                config._get_tristate('classic', 'serialize_keyboard_reports', 'auto'),
            )
        finally:
            config._parser = previous_parser

    def test_ble_kindle_text_mode_serializes_non_phone_keyboard_reports(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        session.is_phone = False
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x00\x00\x16\x00\x00\x00\x00\x00",
        )
        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x00\x00\x16\x12\x00\x00\x00\x00",
        )

        self.assertEqual(
            [
                b"\x02\x00\x00\x16\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x12\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )
        self.assertEqual("020000161200000000", session.last_source_report_hex)
        self.assertEqual("020000000000000000", session.last_uhid_report_hex)

    def test_ble_keyboard_passthrough_is_default_outside_kindle_text_mode(self):
        config.ble_kindle_text_mode = False
        config.ble_serialize_keyboard_reports_mode = 'never'
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x00\x00\x16\x12\x00\x00\x00\x00",
        )

        self.assertEqual(
            [b"\x02\x00\x00\x16\x12\x00\x00\x00\x00"],
            session.uhid_device.inputs,
        )

    def test_ble_non_keyboard_report_is_forwarded_unchanged(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x01\x00\x00\x04\x00\x00\x00\x00",
        )

        self.assertEqual(
            [b"\x01\x00\x00\x04\x00\x00\x00\x00"],
            session.uhid_device.inputs,
        )

    def test_ble_consumer_home_is_forwarded_unchanged_by_default(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE, b"\x03\x00\x01\x00\x00")
        host._forward_report_for_protocol(
            Protocol.BLE, b"\x03\x00\x00\x00\x00")

        self.assertEqual(
            [b"\x03\x00\x01\x00\x00", b"\x03\x00\x00\x00\x00"],
            session.uhid_device.inputs,
        )

    def test_ble_consumer_home_remaps_to_escape_when_enabled(self):
        config.ble_remap_consumer_home_to_escape = True
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE, b"\x03\x00\x01\x00\x00")
        host._forward_report_for_protocol(
            Protocol.BLE, b"\x03\x00\x00\x00\x00")

        self.assertEqual(
            [
                b"\x02\x00\x00\x29\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_ble_keyboard_drops_non_shift_modifiers(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x01\x00\x2a\x00\x00\x00\x00\x00",
        )

        self.assertEqual(
            [
                b"\x02\x00\x00\x2a\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_keyboard_idle_release_is_not_forwarded_repeatedly(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
        )

        self.assertEqual([], session.uhid_device.inputs)

    def test_serialized_physical_release_is_not_forwarded_after_tap(self):
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x00\x00\x04\x00\x00\x00\x00\x00",
        )
        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
        )

        self.assertEqual(
            [
                b"\x02\x00\x00\x04\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

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

    def test_classic_modifier_release_does_not_duplicate_held_key(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        host.sessions[Protocol.CLASSIC] = session

        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x02\x00\x0c\x00\x00\x00\x00",
        )
        host._forward_report_for_protocol(
            Protocol.CLASSIC,
            b"\x01\x00\x00\x0c\x00\x00\x00\x00",
        )

        self.assertEqual(
            [
                b"\x01\x02\x00\x0c\x00\x00\x00\x00",
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
            session.report_queue.join()

        sleep.assert_called_once_with(0.008)
        self.assertEqual(
            [
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_paced_reports_return_without_sending_inline(self):
        config.ble_serialized_report_delay_ms = 8
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        with unittest.mock.patch("host.time.sleep"):
            host._forward_report_for_protocol(
                Protocol.BLE,
                b"\x02\x00\x00\x04\x00\x00\x00\x00\x00",
            )
            self.assertIsNotNone(session.report_queue)
            session.report_queue.join()

        self.assertEqual(
            [
                b"\x02\x00\x00\x04\x00\x00\x00\x00\x00",
                b"\x02\x00\x00\x00\x00\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

    def test_paced_single_report_uses_existing_ordered_queue(self):
        config.ble_serialized_report_delay_ms = 8
        host = self.make_host()
        session = self.make_session(Protocol.BLE)
        host.sessions[Protocol.BLE] = session

        host._queue_reports_for_session(
            session,
            (b"first", b"second"),
            0.008,
        )
        host._forward_report_for_protocol(
            Protocol.BLE,
            b"\x01\x00\x00\x04\x00\x00\x00\x00",
        )
        session.report_queue.join()

        self.assertEqual(
            [
                b"first",
                b"second",
                b"\x01\x00\x00\x04\x00\x00\x00\x00",
            ],
            session.uhid_device.inputs,
        )

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

    def test_classic_is_passive_matches_by_name_and_address(self):
        config.classic_passive_names = ["Example Phone"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone")
        ]

        self.assertTrue(host._classic_is_passive(self.ADDR))
        self.assertTrue(host._classic_is_passive(self.ADDR, "Example Phone"))
        self.assertFalse(host._classic_is_passive("AA:BB:CC:DD:EE:FF"))
        self.assertFalse(
            host._classic_is_passive("AA:BB:CC:DD:EE:FF", "Some Other Device")
        )

        config.classic_passive_names = ["AA:BB:CC:DD:EE:FF"]
        self.assertTrue(host._classic_is_passive("AA:BB:CC:DD:EE:FF"))

    def test_classic_is_passive_true_when_device_cache_says_phone(self):
        host = self.make_host()
        host.device_cache.set_class(self.ADDR, True)

        self.assertTrue(host._classic_is_passive(self.ADDR))

    def test_classic_is_passive_false_for_unrelated_device_by_default(self):
        host = self.make_host()

        self.assertFalse(host._classic_is_passive(self.ADDR))
        self.assertFalse(host._classic_is_passive("AA:BB:CC:DD:EE:FF"))

    def test_classic_is_passive_ignores_stale_global_device_name(self):
        # Regression: _classic_is_passive must not consult the global
        # self.device_name (the last-connected device's name). Otherwise, once
        # the passive phone has connected, a *different* non-passive device
        # would be misclassified as passive and never dialed.
        config.classic_passive_names = ["Example Phone"]
        host = self.make_host()
        other_addr = "AA:BB:CC:DD:EE:FF"
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone"),
            DeviceConfig(other_addr, Protocol.CLASSIC, "Real Keyboard"),
        ]
        # Simulate the phone having connected: the global is now stale.
        host.device_name = "Example Phone"

        self.assertTrue(host._classic_is_passive(self.ADDR, "Example Phone"))
        self.assertFalse(host._classic_is_passive(other_addr, "Real Keyboard"))
        self.assertFalse(host._classic_is_passive(other_addr))

    def test_passive_device_excluded_from_active_dialing(self):
        async def scenario():
            config.classic_passive_names = ["Example Phone"]
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone"),
                DeviceConfig("AA:BB:CC:DD:EE:FF", Protocol.CLASSIC, "Remote"),
            ]
            host.device = FakeClassicDevice()
            dialed = []

            async def active_loop(addresses):
                dialed.append(addresses)

            host._classic_active_connect_loop = active_loop
            await host._run_classic_handler()
            return dialed

        dialed = asyncio.run(scenario())

        self.assertEqual([["AA:BB:CC:DD:EE:FF"]], dialed)

    def test_all_passive_devices_do_not_start_active_connect_loop(self):
        async def scenario():
            config.classic_passive_names = ["Example Phone"]
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone"),
            ]
            host.device = FakeClassicDevice()
            called = []
            host._classic_active_connect_loop = lambda addrs: called.append(addrs)
            await host._run_classic_handler()
            return called

        called = asyncio.run(scenario())

        self.assertEqual([], called)

    @staticmethod
    def _cod(major, minor=0, service=0):
        """Build a 24-bit Class of Device value from major/minor/service."""
        return (service << 13) | (major << 8) | (minor << 2)

    def test_connection_request_phone_cod_marks_device_passive(self):
        # Inbound connection_request with a Phone-major-class CoD from a
        # configured address should auto-populate device_cache.is_phone,
        # which then makes _classic_is_passive true without any entry in
        # config.classic_passive_names. The bd_addr arrives as a real Bumble
        # Address whose str() carries the "/P" transport suffix, while the
        # configured device address is the plain devices.conf form — the
        # write and read must resolve to the same cache entry regardless.
        async def scenario():
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Some Phone")
            ]
            host.device = FakeClassicDevice()
            host._classic_active_connect_loop = lambda addrs: asyncio.sleep(0)
            await host._run_classic_handler()

            phone_cod = self._cod(major=0x02, minor=0x01)  # Phone
            bd_addr = Address(self.ADDR, Address.PUBLIC_DEVICE_ADDRESS)
            # Guard: the fake must reproduce real Bumble's "/P" suffix so this
            # test genuinely exercises the address-normalization path.
            self.assertTrue(str(bd_addr).endswith("/P"))
            host.device.host.emit(
                'connection_request', bd_addr, phone_cod, 0
            )
            return host

        host = asyncio.run(scenario())

        # Readable via the plain devices.conf-style address (no "/P").
        self.assertTrue(host.device_cache.get_is_phone(self.ADDR))
        self.assertTrue(host._classic_is_passive(self.ADDR))

    def test_connection_request_logs_the_raw_cod(self):
        # The inbound connection request is the only place the remote's Class
        # of Device is observable, so the value has to reach the log or there
        # is no way to explain a classification after the fact. Asserted for
        # both outcomes: a phone CoD and a non-phone one.
        async def scenario(cod):
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Some Device")
            ]
            host.device = FakeClassicDevice()
            host._classic_active_connect_loop = lambda addrs: asyncio.sleep(0)
            await host._run_classic_handler()

            with unittest.mock.patch("classic.log.info") as log_info:
                host.device.host.emit(
                    'connection_request',
                    Address(self.ADDR, Address.PUBLIC_DEVICE_ADDRESS),
                    cod,
                    0,
                )
            return [c[0][0] for c in log_info.call_args_list]

        phone_lines = asyncio.run(scenario(self._cod(major=0x02, minor=0x01)))
        self.assertTrue(
            any("CoD=0x000204" in line and "phone=True" in line
                for line in phone_lines),
            phone_lines,
        )

        keyboard_lines = asyncio.run(scenario(self._cod(major=0x05, minor=0x10)))
        self.assertTrue(
            any("CoD=0x000540" in line and "phone=False" in line
                for line in keyboard_lines),
            keyboard_lines,
        )

    def test_cache_key_is_transport_suffix_invariant(self):
        # Regression: a "/P"-suffixed write (as str(bd_addr) yields on a real
        # connection request) and a plain-address read must hit ONE cache
        # file, so is_phone / descriptor data round-trips.
        host = self.make_host()
        suffixed = f"{self.ADDR}/P"
        host.device_cache.set_class(suffixed, True)

        self.assertTrue(host.device_cache.get_is_phone(self.ADDR))
        self.assertTrue(host.device_cache.get_is_phone(suffixed))
        self.assertEqual(
            host.device_cache._get_cache_path(suffixed),
            host.device_cache._get_cache_path(self.ADDR),
        )
        self.assertTrue(host._classic_is_passive(self.ADDR))

    def test_connection_request_non_phone_cod_does_not_mark_phone(self):
        async def scenario():
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Some Keyboard")
            ]
            host.device = FakeClassicDevice()
            host._classic_active_connect_loop = lambda addrs: asyncio.sleep(0)
            await host._run_classic_handler()

            peripheral_cod = self._cod(major=0x05, minor=0x40)  # Peripheral/Keyboard
            bd_addr = Address(self.ADDR, Address.PUBLIC_DEVICE_ADDRESS)
            host.device.host.emit(
                'connection_request', bd_addr, peripheral_cod, 0
            )
            return host

        host = asyncio.run(scenario())

        self.assertIsNone(host.device_cache.get_is_phone(self.ADDR))
        self.assertFalse(host._classic_is_passive(self.ADDR))

    def test_connection_request_from_disallowed_address_is_ignored(self):
        async def scenario():
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Some Keyboard")
            ]
            host.device = FakeClassicDevice()
            host._classic_active_connect_loop = lambda addrs: asyncio.sleep(0)
            await host._run_classic_handler()

            phone_cod = self._cod(major=0x02, minor=0x01)
            bd_addr = Address("AA:BB:CC:DD:EE:FF", Address.PUBLIC_DEVICE_ADDRESS)
            # Must not raise even though the address isn't configured/paired.
            host.device.host.emit(
                'connection_request', bd_addr, phone_cod, 0
            )
            return host

        host = asyncio.run(scenario())

        self.assertIsNone(host.device_cache.get_is_phone("AA:BB:CC:DD:EE:FF"))

    def test_connection_request_listener_cleaned_up_on_restart(self):
        async def scenario():
            host = self.make_host()
            host.classic_devices = [
                DeviceConfig(self.ADDR, Protocol.CLASSIC, "Some Phone")
            ]
            host.device = FakeClassicDevice()
            host._classic_active_connect_loop = lambda addrs: asyncio.sleep(0)
            await host._run_classic_handler()
            first_listener = host._classic_connection_request_listener
            controller = host.device.host

            await host._run_classic_handler()

            self.assertNotIn(
                first_listener,
                controller._listeners.get('connection_request', []),
            )
            self.assertEqual(
                1, len(controller._listeners.get('connection_request', []))
            )

        asyncio.run(scenario())

    def test_passive_device_idle_drop_does_not_restore_or_restart(self):
        config.classic_passive_names = ["Example Phone"]
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone")
        ]
        session = self.make_session(Protocol.CLASSIC)
        session.device_name = "Example Phone"
        session.established_at = time.monotonic() - 1.0
        host.sessions[Protocol.CLASSIC] = session
        scheduled = []
        host._schedule_protocol_restore = scheduled.append

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x13)

        self.assertFalse(host._disconnection_event.is_set())
        self.assertEqual([], scheduled)

    def test_non_passive_classic_idle_drop_still_restores(self):
        config.classic_passive_names = ["Some Other Phone"]
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

    def test_classic_active_loop_waits_for_ble_initiate(self):
        async def scenario():
            host = self.make_host()
            host.ACTIVE_DELAY = 0
            host.device = FakeClassicDevice()
            host.device.le_connecting = True
            host._classic_page_scan_enabled = False

            task = asyncio.create_task(host._classic_active_connect_loop([self.ADDR]))
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return host.device.host.commands

        commands = asyncio.run(scenario())

        self.assertEqual([], commands)

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

    def test_ble_uses_normal_windows_when_classic_is_live(self):
        host = self.make_host()
        host.sessions[Protocol.CLASSIC] = self.make_session(Protocol.CLASSIC)

        self.assertEqual(
            host.BLE_INIT_WINDOW,
            host._ble_window_for_radio_state(host.BLE_INIT_WINDOW),
        )
        self.assertEqual(
            0.0,
            host._ble_coexist_pause_delay(),
        )

    def test_ble_initiate_runs_when_classic_session_is_live(self):
        async def scenario():
            host = self.make_host()
            host.sessions[Protocol.CLASSIC] = self.make_session(Protocol.CLASSIC)
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()

            result = await host._ble_initiate(0.01)
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

    def test_ble_initiate_pauses_classic_page_scan_when_classic_not_live(self):
        async def scenario():
            host = self.make_host()
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()
            host._classic_page_scan_enabled = True

            result = await host._ble_initiate(0.01)
            return host, result

        host, result = asyncio.run(scenario())

        self.assertIsNone(result)
        self.assertEqual(
            [0x00, 0x02],
            [
                command.kwargs["scan_enable"]
                for command in host.device.host.commands
            ],
        )
        self.assertTrue(host._classic_page_scan_enabled)

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

    def test_ble_restore_forgets_bond_when_peer_rejects_key(self):
        async def scenario():
            host = self.make_host()
            connection = FakeConnection()
            host.connection = connection
            host.connected_protocol = Protocol.BLE
            keystore = FakeKeystore()
            host.device = types.SimpleNamespace(keystore=keystore)
            host._protocol_disconnection_events[Protocol.BLE].set()
            # 0x05 = Authentication Failure: an unambiguous credential
            # rejection, forgotten immediately (one-shot).
            host._last_ble_disconnect_reason = 0x05

            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()

            return connection, keystore

        connection, keystore = asyncio.run(scenario())

        self.assertEqual([connection.peer_address], keystore.deleted)
        self.assertFalse(connection.pair_called)

    def test_ble_restore_forgets_bond_on_pin_or_key_missing(self):
        async def scenario():
            host = self.make_host()
            connection = FakeConnection()
            host.connection = connection
            host.connected_protocol = Protocol.BLE
            keystore = FakeKeystore()
            host.device = types.SimpleNamespace(keystore=keystore)
            host._protocol_disconnection_events[Protocol.BLE].set()
            # 0x06 = PIN or Key Missing: also an unambiguous credential
            # rejection, forgotten immediately (one-shot).
            host._last_ble_disconnect_reason = 0x06

            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()

            return connection, keystore

        connection, keystore = asyncio.run(scenario())

        self.assertEqual([connection.peer_address], keystore.deleted)
        self.assertFalse(connection.pair_called)

    def test_ble_restore_keeps_bond_on_transient_drop(self):
        async def scenario():
            host = self.make_host()
            connection = FakeConnection()
            host.connection = connection
            host.connected_protocol = Protocol.BLE
            keystore = FakeKeystore()
            host.device = types.SimpleNamespace(keystore=keystore)
            host._protocol_disconnection_events[Protocol.BLE].set()
            # 0x08 = Connection Timeout (supervision timeout): a transient drop,
            # not a key rejection, so the good bond must be preserved.
            host._last_ble_disconnect_reason = 0x08

            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()

            return keystore

        keystore = asyncio.run(scenario())

        self.assertEqual([], keystore.deleted)

    def test_ble_restore_keeps_bond_on_single_0x3e(self):
        """0x3E is transient (RF/timing), not a credential rejection, so a
        single occurrence must not forget the bond."""
        async def scenario():
            host = self.make_host()
            connection = FakeConnection()
            host.connection = connection
            host.connected_protocol = Protocol.BLE
            keystore = FakeKeystore()
            host.device = types.SimpleNamespace(keystore=keystore)
            host._protocol_disconnection_events[Protocol.BLE].set()
            host._last_ble_disconnect_reason = 0x3E

            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()

            return host, keystore

        host, keystore = asyncio.run(scenario())

        self.assertEqual([], keystore.deleted)
        self.assertEqual(
            1, host._ble_bond_3e_fail_counts[host.connection.peer_address]
        )

    def test_ble_restore_forgets_bond_after_repeated_0x3e(self):
        """0x3E repeated BLE_BOND_3E_FORGET_THRESHOLD times in a row against
        the same bond corroborates a real rejection and forgets it."""
        async def scenario():
            host = self.make_host()
            keystore = FakeKeystore()
            host.device = types.SimpleNamespace(keystore=keystore)
            host.connected_protocol = Protocol.BLE
            host._protocol_disconnection_events[Protocol.BLE].set()
            host._last_ble_disconnect_reason = 0x3E

            connection = None
            for _ in range(host.BLE_BOND_3E_FORGET_THRESHOLD):
                connection = FakeConnection()
                host.connection = connection
                with self.assertRaises(Exception):
                    await host._ble_restore_or_pair()

            return host, connection, keystore

        host, connection, keystore = asyncio.run(scenario())

        self.assertEqual([connection.peer_address], keystore.deleted)
        self.assertNotIn(connection.peer_address, host._ble_bond_3e_fail_counts)

    def test_ble_restore_success_resets_0x3e_counter(self):
        """A successful bonding restore must reset the corroboration counter,
        so a later isolated 0x3E does not compound with earlier failures."""
        async def scenario():
            host = self.make_host()
            keystore = FakeKeystore()
            host.device = types.SimpleNamespace(keystore=keystore)
            host.connected_protocol = Protocol.BLE
            host._protocol_disconnection_events[Protocol.BLE].set()
            host._last_ble_disconnect_reason = 0x3E

            # First 0x3E failure.
            connection = FakeConnection()
            host.connection = connection
            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()
            self.assertEqual(1, host._ble_bond_3e_fail_counts[connection.peer_address])

            # A successful restore (already encrypted) resets the counter.
            success_connection = FakeConnection()
            success_connection.is_encrypted = True
            host.connection = success_connection
            await host._ble_restore_or_pair()

            # Another isolated 0x3E afterwards should not immediately forget.
            connection2 = FakeConnection()
            host.connection = connection2
            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()

            return host, connection2, keystore

        host, connection2, keystore = asyncio.run(scenario())

        self.assertEqual([], keystore.deleted)
        self.assertEqual(1, host._ble_bond_3e_fail_counts[connection2.peer_address])

    IDENTITY_ADDR = "AA:BB:CC:44:55:66"
    RPA_ADDR = "7F:11:22:33:44:55"

    def _make_resolving_host(self, keystore):
        host = self.make_host()
        resolver = types.SimpleNamespace(
            resolve=lambda addr: self.IDENTITY_ADDR
        )
        host.device = types.SimpleNamespace(
            keystore=keystore, address_resolver=resolver
        )
        host.connected_protocol = Protocol.BLE
        host._protocol_disconnection_events[Protocol.BLE].set()
        connection = FakeConnection(peer_address=FakeResolvableAddress(self.RPA_ADDR))
        host.connection = connection
        return host, connection

    def test_ble_restore_looks_up_bond_by_resolved_identity_not_rpa(self):
        """When the peer connects via an RPA, the keystore lookup, counter, and
        forget must all key off the resolved identity address, not the RPA."""
        async def scenario():
            # Bond stored under the identity address; a raw-RPA lookup would miss.
            keystore = RecordingKeystore(known_addresses={self.IDENTITY_ADDR})
            host, connection = self._make_resolving_host(keystore)
            # 0x05 = immediate credential rejection, so we reach forget in one go.
            host._last_ble_disconnect_reason = 0x05

            with self.assertRaises(Exception):
                await host._ble_restore_or_pair()

            return host, keystore

        host, keystore = asyncio.run(scenario())

        # get() and delete() both operated on the identity address, not the RPA.
        self.assertEqual([self.IDENTITY_ADDR], keystore.requested)
        self.assertEqual([self.IDENTITY_ADDR], keystore.deleted)
        self.assertNotIn(self.RPA_ADDR, keystore.requested)

    def test_ble_restore_counts_0x3e_by_resolved_identity(self):
        """The 0x3E corroboration counter is keyed by the resolved identity
        address even when the peer connects via a rotating RPA."""
        async def scenario():
            keystore = RecordingKeystore(known_addresses={self.IDENTITY_ADDR})
            host = self.make_host()
            resolver = types.SimpleNamespace(resolve=lambda addr: self.IDENTITY_ADDR)
            host.device = types.SimpleNamespace(
                keystore=keystore, address_resolver=resolver
            )
            host.connected_protocol = Protocol.BLE
            host._protocol_disconnection_events[Protocol.BLE].set()
            host._last_ble_disconnect_reason = 0x3E

            # Each reconnect arrives via a fresh RPA; the counter must still
            # accumulate against the single identity address.
            for i in range(host.BLE_BOND_3E_FORGET_THRESHOLD):
                host.connection = FakeConnection(
                    peer_address=FakeResolvableAddress(f"7F:00:00:00:00:{i:02X}")
                )
                with self.assertRaises(Exception):
                    await host._ble_restore_or_pair()

            return host, keystore

        host, keystore = asyncio.run(scenario())

        self.assertEqual([self.IDENTITY_ADDR], keystore.deleted)
        self.assertNotIn(self.IDENTITY_ADDR, host._ble_bond_3e_fail_counts)

    def test_ble_restore_happy_path_via_encrypt_resets_counter(self):
        """The real bond-restore path (encrypt() succeeding, not the
        is_encrypted shortcut) succeeds and clears any prior 0x3E count."""
        async def scenario():
            keystore = RecordingKeystore(known_addresses={self.IDENTITY_ADDR})
            host, connection = self._make_resolving_host(keystore)
            connection.encrypt_succeeds = True
            # The link stays up for a real restore, so clear the drop event that
            # _make_resolving_host sets for the failure-path tests.
            host._protocol_disconnection_events[Protocol.BLE].clear()
            # A prior 0x3E failure left a count that a real restore must clear.
            host._ble_bond_3e_fail_counts[self.IDENTITY_ADDR] = 2

            await host._ble_restore_or_pair()

            return host, connection, keystore

        host, connection, keystore = asyncio.run(scenario())

        self.assertTrue(connection.is_encrypted)
        self.assertFalse(connection.pair_called)
        self.assertEqual([self.IDENTITY_ADDR], keystore.requested)
        self.assertEqual([], keystore.deleted)
        self.assertNotIn(self.IDENTITY_ADDR, host._ble_bond_3e_fail_counts)

    def test_ble_accept_list_session_setup_clears_stale_disconnect_reason(self):
        """A reject-class reason left over from a previous BLE session must
        not survive into a fresh accept-list session and wrongly trigger a
        forget on a later, unrelated failure (e.g. a plain timeout)."""
        async def scenario():
            host = self.make_host()
            host.ble_devices = [
                DeviceConfig(FakeConnection().peer_address, Protocol.BLE, "Keyboard")
            ]
            keystore = FakeKeystore()
            connection = FakeConnection()
            connection.on = lambda event, callback: None

            async def fake_send_command(*_args, **_kwargs):
                return None

            host.device = types.SimpleNamespace(
                keystore=keystore,
                send_command=fake_send_command,
            )
            host._session_setup_lock = asyncio.Lock()
            host._radio_lock = asyncio.Lock()
            host._keystore_address_types = {}
            # Simulate a reject-class reason lingering from a prior session.
            host._last_ble_disconnect_reason = 0x05

            async def fake_initiate(window, peer=None):
                return connection

            async def fake_reject(conn, matched_dev):
                return False

            async def fake_configured_name(addr):
                return None

            async def fake_restore_or_pair():
                # By the time session setup runs, the stale reason from the
                # previous session must already be cleared.
                assert host._last_ble_disconnect_reason is None, (
                    "stale disconnect reason leaked into new BLE session"
                )
                host._last_ble_disconnect_reason = 0x08  # plain timeout, this session
                raise RuntimeError("simulated unrelated restore failure")

            host._ble_initiate = fake_initiate
            host._reject_unconfigured_ble_connection = fake_reject
            host._configured_name = lambda addr: None
            host._ble_restore_or_pair = fake_restore_or_pair

            await host._run_ble_accept_list_handler(
                [dev.address for dev in host.ble_devices]
            )

            return host, keystore

        host, keystore = asyncio.run(scenario())

        self.assertEqual([], keystore.deleted)
        self.assertIsNone(host.connection)


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
    def setUp(self):
        self._old_power_wifi_gate_enabled = config.power_wifi_gate_enabled
        self._old_power_resume_delay = config.power_resume_delay
        self._old_power_resume_max_delay = config.power_resume_max_delay
        self._old_power_resume_min_delay = config.power_resume_min_delay
        self._old_power_resume_poll_interval = config.power_resume_poll_interval
        self._old_power_resume_stable_polls = config.power_resume_stable_polls

    def tearDown(self):
        config.power_wifi_gate_enabled = self._old_power_wifi_gate_enabled
        config.power_resume_delay = self._old_power_resume_delay
        config.power_resume_max_delay = self._old_power_resume_max_delay
        config.power_resume_min_delay = self._old_power_resume_min_delay
        config.power_resume_poll_interval = self._old_power_resume_poll_interval
        config.power_resume_stable_polls = self._old_power_resume_stable_polls

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

    async def test_user_resume_overrides_power_recovery_gate(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "power"
        daemon._power_blocked = True

        async def never_resume():
            await asyncio.sleep(60)

        daemon._power_resume_task = asyncio.create_task(never_resume())

        await daemon.resume(reason="user")
        await asyncio.gather(daemon._power_resume_task, return_exceptions=True)

        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._power_blocked)
        self.assertTrue(daemon._power_resume_task.cancelled())

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

    async def test_power_watchdog_waits_for_powerd_active(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "power"
        daemon._power_blocked = True

        config.power_wifi_gate_enabled = False
        config.power_resume_poll_interval = 0.01
        samples = ["screen saver", "ready to suspend", "active"]
        observed = []

        def fake_powerd_state():
            state = samples.pop(0)
            observed.append(state)
            return state

        with unittest.mock.patch.object(
                daemon_module, "powerd_state", fake_powerd_state):
            await daemon._delayed_power_resume(0, "power-watchdog")

        self.assertEqual(
            ["screen saver", "ready to suspend", "active"],
            observed,
        )
        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._power_blocked)

    async def test_power_watchdog_keeps_legacy_fallback_without_lipc(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "power"
        daemon._power_blocked = True

        config.power_wifi_gate_enabled = False
        with unittest.mock.patch.object(
                daemon_module, "powerd_state", return_value=None):
            await daemon._delayed_power_resume(0, "power-watchdog")

        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._power_blocked)

    async def test_power_resume_waits_for_wifi_readiness_gate(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "power"
        daemon._power_blocked = True

        config.power_wifi_gate_enabled = True
        config.power_resume_min_delay = 0
        config.power_resume_poll_interval = 0.01
        config.power_resume_stable_polls = 2

        samples = [
            WifiReadiness(False, 'cmState=CONNECTING', {}),
            WifiReadiness(True, 'ready', {}),
            WifiReadiness(True, 'ready', {}),
        ]
        observed = []

        def fake_readiness():
            sample = samples.pop(0)
            observed.append(sample.reason)
            return sample

        with unittest.mock.patch.object(
                daemon_module, "wifi_readiness", fake_readiness):
            await daemon._delayed_power_resume(1.0, "power")

        self.assertEqual(
            ['cmState=CONNECTING', 'ready', 'ready'],
            observed,
        )
        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._power_blocked)

    async def test_power_resume_gate_disabled_uses_resume_delay_fallback(self):
        daemon = HIDDaemon()
        daemon._suspended = True
        daemon._suspend_reason = "power"
        daemon._power_blocked = True

        config.power_wifi_gate_enabled = False
        config.power_resume_delay = 3.5
        sleep_calls = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)

        with unittest.mock.patch.object(
                daemon_module.asyncio, "sleep", fake_sleep):
            await daemon._delayed_power_resume(90.0, "power")

        self.assertEqual([3.5], sleep_calls)
        self.assertFalse(daemon._suspended)
        self.assertFalse(daemon._power_blocked)

    async def test_controller_system_resume_uses_wifi_gate_max_delay(self):
        config.power_resume_delay = 20.0
        config.power_resume_max_delay = 90.0

        class FakeDaemon:
            def __init__(self):
                self._suspended = True
                self._power_resume_task = None
                self.calls = []

            async def _delayed_power_resume(self, delay, reason):
                self.calls.append((delay, reason))

        daemon = FakeDaemon()
        controller = DaemonController(daemon)
        controller._suspended_by_system = True

        await controller._do_system_resume("wakeupFromSuspend")
        await daemon._power_resume_task

        self.assertEqual([(90.0, "power")], daemon.calls)


if __name__ == "__main__":
    unittest.main()
