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
from classic import FALLBACK_HID_DESCRIPTOR  # noqa: E402
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
    async def set_connectable(self, connectable=True):
        self.connectable = connectable
        self.scan_enable_writes.append(0x02 if connectable else 0x00)

    def __init__(self, on_create=None):
        self.connectable = True
        self.discoverable = False
        self.scan_enable_writes = []
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
        # Mirrors bumble: set_connectable keeps `connectable` in step and the
        # scan-enable byte is derived from it plus `discoverable`.
        self.connectable = True
        self.discoverable = False
        self.scan_enable_writes = []

    async def set_connectable(self, connectable=True):
        self.connectable = connectable
        if self.discoverable and self.connectable:
            byte = 0x03
        elif self.connectable:
            byte = 0x02
        elif self.discoverable:
            byte = 0x01
        else:
            byte = 0x00
        self.scan_enable_writes.append(byte)

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
        self._old_trust_reconnect = config.classic_trust_reconnect_initiate
        self._old_max_dark = config.classic_page_scan_max_dark
        self._old_dwell = config.classic_page_scan_dwell
        self._old_pause_page_scan = config.ble_pause_classic_page_scan
        config.classic_trust_reconnect_initiate = True
        config.classic_page_scan_max_dark = 2.0
        # Zero so slicing tests exercise the loop without real sleeps; the
        # dwell length itself is asserted by overriding this per test.
        config.classic_page_scan_dwell = 0.0
        config.ble_pause_classic_page_scan = True
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
        config.classic_trust_reconnect_initiate = self._old_trust_reconnect
        config.classic_page_scan_max_dark = self._old_max_dark
        config.classic_page_scan_dwell = self._old_dwell
        config.ble_pause_classic_page_scan = self._old_pause_page_scan

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

    def test_status_payload_delivers_the_keys_the_whitelist_promises(self):
        # controller.STATUS_CONNECTION_KEYS is the contract the HTTP /status
        # endpoint publishes. Two halves of it were broken in opposite directions:
        # hid_ready was whitelisted but never emitted, and is_phone was emitted but
        # stripped, which is why the KOReader plugin's is_phone gate silently
        # treated every Classic device as a phone.
        from controller import STATUS_CONNECTION_KEYS
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.is_phone = True
        state = host._session_state(session)

        self.assertIn("hid_ready", state, "whitelisted but never emitted")
        self.assertTrue(state["hid_ready"], "session owns a UHID device")
        self.assertIn("is_phone", STATUS_CONNECTION_KEYS, "emitted but stripped")

        survives = {k: v for k, v in state.items() if k in STATUS_CONNECTION_KEYS}
        self.assertTrue(survives.get("is_phone"))
        self.assertTrue(survives.get("hid_ready"))

    def test_hid_ready_is_false_for_a_session_without_uhid(self):
        host = self.make_host()
        session = self.make_session(Protocol.CLASSIC)
        session.uhid_device = None
        self.assertFalse(host._session_state(session)["hid_ready"])

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

    def test_classic_auto_logs_once_when_device_class_stays_unknown(self):
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
        ) as get_is_phone, unittest.mock.patch("host.log.info") as info:
            host._forward_report_for_protocol(Protocol.CLASSIC, report)
            host._forward_report_for_protocol(Protocol.CLASSIC, report)

        self.assertEqual(1, get_is_phone.call_count)
        self.assertEqual(
            1,
            sum("Device class unknown" in c[0][0] for c in info.call_args_list),
        )
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

    def test_setup_failure_before_any_session_does_not_request_host_teardown(self):
        # A link that dies during setup records no session. Asking for a host
        # rebuild there is wrong, and specifically dangerous: the global event is
        # latched and never cleared, so run() would tear down the NEXT successful
        # session the instant it resolved _connection_future. Observed on a PW5 as
        # a session torn down 90ms after "Session ready".
        host = self.make_host()
        self.assertNotIn(Protocol.CLASSIC, host.sessions)

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x16)

        self.assertTrue(
            host._protocol_disconnection_events[Protocol.CLASSIC].is_set(),
            "the per-protocol event should still fire so the handler loop retries")
        self.assertFalse(
            host._disconnection_event.is_set(),
            "a pre-session failure must not latch a host-teardown request")

    def test_session_after_a_setup_failure_is_not_torn_down(self):
        # End-to-end shape of the PW5 failure: attempt 1 dies during setup,
        # attempt 2 succeeds. The successful session must survive.
        host = self.make_host()
        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x16)

        host.current_device_address = self.ADDR
        host.connection = FakeConnection(peer_address=self.ADDR)
        host.uhid_device = FakeUhidDevice()
        host.connected_protocol = Protocol.CLASSIC
        host._record_current_session(Protocol.CLASSIC)

        self.assertIn(Protocol.CLASSIC, host.sessions)
        self.assertFalse(
            host._disconnection_event.is_set(),
            "run() would return immediately and tear the new session down")

    def test_pairing_mode_setup_failure_still_requests_teardown(self):
        # continue_after_pairing() records no session and its only disconnect exit
        # is the same branch, while pair_device() populates the device lists. A
        # keep-alive gated on configured devices alone would swallow the set() that
        # pairing mode is waiting on and hang the daemon forever. Pairing mode is
        # distinguished by _protocol_disconnection_events being empty: only run()
        # populates it.
        host = self.make_host()
        host._protocol_disconnection_events = {}      # pairing mode
        host._disconnection_event = asyncio.Event()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Pairing target")
        ]

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x16)

        self.assertTrue(
            host._disconnection_event.is_set(),
            "pairing mode would wait forever without this")

    def test_teardown_still_requested_when_protocol_has_no_configured_devices(self):
        # The branch must keep working when the device really is gone, e.g. after
        # a virtual-cable unplug removed it from devices.conf.
        host = self.make_host()
        host.classic_devices = []

        host._on_protocol_disconnection(Protocol.CLASSIC, self.ADDR, 0x16)

        self.assertTrue(host._disconnection_event.is_set())

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

    def test_classic_is_passive_when_declared_and_observed_paging_us(self):
        # Declaration plus evidence: the device says it reconnects, and we
        # have actually seen it page us. Only then is it safe to stop dialing.
        host = self.make_host()
        host.device_cache.set_reconnect_initiate(self.ADDR, True)
        host.device_cache.set_seen_inbound(self.ADDR, True)

        self.assertTrue(host._classic_is_passive(self.ADDR))

    def test_declared_reconnect_initiate_alone_does_not_stop_dialing(self):
        # Firmware can claim HIDReconnectInitiate without implementing it.
        # Acting on the claim alone would strand the device: the only evidence
        # of the mistake is the inbound connection it never makes.
        host = self.make_host()
        host.device_cache.set_reconnect_initiate(self.ADDR, True)

        self.assertIsNone(host.device_cache.get_seen_inbound(self.ADDR))
        self.assertFalse(host._classic_is_passive(self.ADDR))

    def test_seen_inbound_alone_does_not_stop_dialing(self):
        # A device that pages us but never declared reconnect-initiate is
        # still dialed; only the pair suppresses it.
        host = self.make_host()
        host.device_cache.set_seen_inbound(self.ADDR, True)

        self.assertFalse(host._classic_is_passive(self.ADDR))

    def test_classic_still_dialed_when_reconnect_initiate_is_false(self):
        host = self.make_host()
        host.device_cache.set_reconnect_initiate(self.ADDR, False)

        self.assertFalse(host._classic_is_passive(self.ADDR))

    def test_classic_still_dialed_when_reconnect_initiate_unknown(self):
        # Bootstrap order: SDP is only readable once connected, so a device is
        # dialed until it has told us not to.
        host = self.make_host()

        self.assertIsNone(host.device_cache.get_reconnect_initiate(self.ADDR))
        self.assertFalse(host._classic_is_passive(self.ADDR))

    def test_reconnect_initiate_can_be_distrusted(self):
        # Escape hatch for firmware that declares true but never reconnects.
        config.classic_trust_reconnect_initiate = False
        host = self.make_host()
        host.device_cache.set_reconnect_initiate(self.ADDR, True)
        # Both conditions satisfied, so the switch is the only thing that can
        # be producing False here.
        host.device_cache.set_seen_inbound(self.ADDR, True)

        self.assertFalse(host._classic_is_passive(self.ADDR))
        config.classic_trust_reconnect_initiate = True
        self.assertTrue(host._classic_is_passive(self.ADDR))

    def test_reconnect_initiate_survives_a_descriptor_recache(self):
        # Regression: a descriptor re-cache passes only report_map/device_name.
        # Without sticky-key preservation the dial decision would silently
        # revert to "dial it" on every reconnect.
        host = self.make_host()
        host.device_cache.set_reconnect_initiate(self.ADDR, True)
        host.device_cache.set_class(self.ADDR, True)

        host.device_cache.save(self.ADDR, {
            'report_map': 'aabb',
            'device_name': 'Example Keyboard',
        })

        self.assertTrue(host.device_cache.get_reconnect_initiate(self.ADDR))
        self.assertTrue(host.device_cache.get_is_phone(self.ADDR))

    def test_parse_sdp_boolean_distinguishes_false_from_absent(self):
        # "did not tell us" and "told us no" drive opposite dial decisions and
        # must not collapse to the same value.
        host = self.make_host()

        self.assertIs(True, host._parse_sdp_boolean(types.SimpleNamespace(value=True)))
        self.assertIs(False, host._parse_sdp_boolean(types.SimpleNamespace(value=False)))
        self.assertIs(True, host._parse_sdp_boolean(types.SimpleNamespace(value=1)))
        self.assertIs(False, host._parse_sdp_boolean(types.SimpleNamespace(value=0)))
        self.assertIs(True, host._parse_sdp_boolean(types.SimpleNamespace(value=b'\x01')))
        self.assertIs(False, host._parse_sdp_boolean(types.SimpleNamespace(value=b'\x00')))
        self.assertIsNone(host._parse_sdp_boolean(types.SimpleNamespace(value='yes')))
        self.assertIsNone(host._parse_sdp_boolean(types.SimpleNamespace(value=None)))

    def _sdp_contract_host(self, search_result):
        """A host whose SDP client returns `search_result` (or raises it)."""
        import classic as classic_module

        host = self.make_host()
        host.current_device_address = self.ADDR
        host.connection = FakeConnection(peer_address=self.ADDR)

        class FakeSDPClient:
            def __init__(self, connection):
                pass

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def search_attributes(self, uuids, attribute_ids):
                if isinstance(search_result, Exception):
                    raise search_result
                return search_result

        self._old_sdp_client = classic_module.SDPClient
        classic_module.SDPClient = FakeSDPClient
        self.addCleanup(
            setattr, classic_module, 'SDPClient', self._old_sdp_client)
        return host

    def test_query_sdp_returns_true_on_a_usable_descriptor(self):
        # Pins the tri-state contract of _query_classic_sdp itself. Every other
        # test in this area stubs the method, so without this its return values
        # could be changed and the whole require_live_descriptor feature would
        # degrade silently with the suite still green.
        descriptor = types.SimpleNamespace(
            id=0x0206,
            value=[[types.SimpleNamespace(value=0x22), b"\x05\x01\x09\x06"]],
        )
        host = self._sdp_contract_host([[descriptor]])

        self.assertIs(True, asyncio.run(host._query_classic_sdp(self.ADDR)))
        self.assertEqual(b"\x05\x01\x09\x06", host.report_map)

    def test_query_sdp_returns_false_only_when_there_are_no_records(self):
        # False is what callers treat as proof the keyboard is gone.
        host = self._sdp_contract_host([])

        self.assertIs(False, asyncio.run(host._query_classic_sdp(self.ADDR)))

    def test_query_sdp_returns_none_when_records_yield_no_descriptor(self):
        # Regression: a malformed 0x0206 is swallowed by the parser, and bumble
        # returns [] when the outer element is not a SEQUENCE. Reporting either
        # as False would convict a device that does have a HID record.
        unusable = types.SimpleNamespace(id=0x0206, value="not-a-sequence")
        host = self._sdp_contract_host([[unusable]])

        self.assertIsNone(asyncio.run(host._query_classic_sdp(self.ADDR)))

    def test_query_sdp_returns_none_when_the_query_raises(self):
        host = self._sdp_contract_host(RuntimeError("sdp connect refused"))

        self.assertIsNone(asyncio.run(host._query_classic_sdp(self.ADDR)))

    def _live_descriptor_host(self, sdp_result, cached=False, is_phone=False):
        """A host poised at _handle_classic_connection with SDP stubbed."""
        host = self.make_host()
        host.current_device_address = self.ADDR
        host.connection = FakeConnection(peer_address=self.ADDR)
        host.hid_host = types.SimpleNamespace(l2cap_intr_channel=object())
        host._create_uhid_device = lambda: None
        if is_phone:
            host.device_cache.set_class(self.ADDR, True)

        async def fake_sdp(address=None):
            return sdp_result

        host._query_classic_sdp = fake_sdp

        def fake_cache():
            if cached:
                host.report_map = b"\x05\x01"
            return cached

        host._load_cached_descriptor = fake_cache
        return host

    def test_link_dropped_when_device_answers_sdp_with_no_hid_record(self):
        # The flag is called require_live_descriptor. A False result means the
        # device answered and offered no HID service -- proof the keyboard is
        # gone -- so a cached descriptor contradicts it and must not be used.
        # Observed on hardware: a phone with its keyboard app closed got a UHID
        # node built from a stale 141-byte descriptor and dropped the link 1.3s
        # later having sent nothing.
        host = self._live_descriptor_host(sdp_result=False, cached=True)

        ok = asyncio.run(host._handle_classic_connection())

        self.assertFalse(ok)
        self.assertTrue(host.connection.is_disconnected)
        self.assertIsNone(host.report_map)

    def test_link_dropped_when_sdp_unreachable_on_a_phone(self):
        # None means we could not ask. On a phone that most likely means the
        # app that registers the HID record has exited.
        host = self._live_descriptor_host(
            sdp_result=None, cached=True, is_phone=True)

        ok = asyncio.run(host._handle_classic_connection())

        self.assertFalse(ok)
        self.assertTrue(host.connection.is_disconnected)

    def test_cached_descriptor_still_used_when_sdp_times_out_on_a_keyboard(self):
        # The one defensible fallback: a non-phone whose query did not
        # complete. Dropping here would flap a physical keyboard on a
        # transient SDP timeout.
        host = self._live_descriptor_host(
            sdp_result=None, cached=True, is_phone=False)

        ok = asyncio.run(host._handle_classic_connection())

        self.assertTrue(ok)
        self.assertFalse(host.connection.is_disconnected)
        self.assertEqual(b"\x05\x01", host.report_map)

    def test_link_dropped_when_sdp_fails_and_nothing_is_cached(self):
        host = self._live_descriptor_host(
            sdp_result=None, cached=False, is_phone=False)

        ok = asyncio.run(host._handle_classic_connection())

        self.assertFalse(ok)
        self.assertTrue(host.connection.is_disconnected)

    def test_live_descriptor_accepted_without_touching_the_cache(self):
        host = self._live_descriptor_host(sdp_result=True, cached=False)

        def fail():
            raise AssertionError("cache must not be consulted on a live hit")

        host._load_cached_descriptor = fail

        ok = asyncio.run(host._handle_classic_connection())

        self.assertTrue(ok)
        self.assertFalse(host.connection.is_disconnected)

    def _pairing_flow_host(self, sdp_search_result, cached=False, is_phone=False):
        """A host driven through the REAL _pair_classic and
        _continue_classic_after_pairing, with only the SDP transport, the
        BumbleHIDHost channel setup, and UHID creation faked. Proves the
        pairing gate against what _pair_classic actually found, not a
        tri-state value the test injected directly -- a fake device fed
        this same `sdp_search_result` is the only source of truth."""
        import classic as classic_module

        addr = self.ADDR
        host = self.make_host()
        # Real make_host() keystore is the bumble-stub JsonKeyStore, which
        # has no get() -- irrelevant to the descriptor gate under test, and
        # _pair_classic's own try/except would otherwise turn that
        # AttributeError into a spurious pairing failure.
        host.keystore = None
        if is_phone:
            host.device_cache.set_class(addr, True)
        if cached:
            host.device_cache.save(addr, {
                'report_map': '0501', 'device_name': 'Cached Keyboard',
            })

        connection = FakeConnection(peer_address=addr)
        # Skips _pair_classic's encrypt() branch entirely; that branch is
        # unrelated to the descriptor gate this test drives.
        connection.is_encrypted = True

        async def authenticate():
            return None
        connection.authenticate = authenticate

        class FakeHostEvents:
            def on(self, event, callback):
                if event == 'link_key':
                    # Real hardware fires this during authenticate(); this
                    # fake never will. Firing it synchronously here avoids
                    # burning the real 5s of
                    # wait_for(link_key_received.wait(), 5.0) in the test.
                    callback(addr, object(), 0)

            def remove_listener(self, event, callback):
                pass

        class FakeDevice:
            def __init__(self):
                self.host = FakeHostEvents()

            async def connect(self, target_address, transport=None, timeout=None):
                return connection

        host.device = FakeDevice()

        class FakeSDPClient:
            def __init__(self, _connection):
                pass

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def search_attributes(self, uuids, attribute_ids):
                return sdp_search_result

        old_sdp_client = classic_module.SDPClient
        classic_module.SDPClient = FakeSDPClient
        self.addCleanup(setattr, classic_module, 'SDPClient', old_sdp_client)

        class FakeClassicHidHost:
            EVENT_INTERRUPT_DATA = "interrupt"
            EVENT_VIRTUAL_CABLE_UNPLUG = "unplug"

            def __init__(self, device):
                self.l2cap_intr_channel = object()
                self.l2cap_ctrl_channel = object()

            def on(self, event, callback):
                pass

            def on_device_connection(self, connection):
                pass

            async def connect_control_channel(self):
                pass

            async def connect_interrupt_channel(self):
                pass

            def set_protocol(self, mode):
                pass

        old_hid_host = classic_module.BumbleHIDHost
        classic_module.BumbleHIDHost = FakeClassicHidHost
        self.addCleanup(setattr, classic_module, 'BumbleHIDHost', old_hid_host)

        created = []
        host._create_uhid_device = lambda: created.append(True)
        host._created_uhid_calls = created

        return host

    def test_pairing_reuses_pair_classics_live_descriptor_without_requerying(self):
        # Item C: _continue_classic_after_pairing used to call
        # _finalize_classic_hid() directly, so classic_require_live_descriptor
        # never applied on the pairing path. This drives the REAL
        # _pair_classic (which already queries SDP live) followed by the
        # REAL _continue_classic_after_pairing, and proves the gate reuses
        # that result -- SDP is asked exactly once across both calls, not
        # re-queried by the gate.
        #
        # is_phone=True is load-bearing, not decoration: _query_classic_sdp
        # writes any descriptor it finds straight into the cache as a side
        # effect, so a non-phone host would pass this test even if the gate
        # merely fell through to its "SDP incomplete, but cached" branch --
        # incidentally right for the wrong reason. On a phone that branch is
        # refused outright regardless of cache, so only a gate that actually
        # consumed _pair_classic's True result (not a forgotten/None one)
        # can let this succeed.
        descriptor = types.SimpleNamespace(
            id=0x0206,
            value=[[types.SimpleNamespace(value=0x22), b"\x05\x01\x09\x06"]],
        )
        host = self._pairing_flow_host(
            sdp_search_result=[[descriptor]], is_phone=True)

        import classic as classic_module
        unbound_query = classic_module.ClassicMixin._query_classic_sdp
        call_count = {"n": 0}

        async def counting_query(self, address=None):
            call_count["n"] += 1
            return await unbound_query(self, address)
        host._query_classic_sdp = types.MethodType(counting_query, host)

        self.assertTrue(asyncio.run(host._pair_classic(self.ADDR)))
        asyncio.run(host._continue_classic_after_pairing())

        self.assertEqual(1, call_count["n"])
        self.assertEqual(b"\x05\x01\x09\x06", host.report_map)
        self.assertEqual([True], host._created_uhid_calls)
        self.assertFalse(host.connection.is_disconnected)

    def test_pairing_refused_when_device_confirms_no_hid_service(self):
        # False from _query_classic_sdp is proof the device has no HID
        # service, not a hiccup -- the exact case the defect let slip
        # through: FALLBACK_HID_DESCRIPTOR must not paper over that proof
        # on the pairing path either.
        host = self._pairing_flow_host(sdp_search_result=[])

        self.assertTrue(asyncio.run(host._pair_classic(self.ADDR)))
        asyncio.run(host._continue_classic_after_pairing())

        self.assertTrue(host.connection.is_disconnected)
        self.assertEqual([], host._created_uhid_calls)
        self.assertIsNone(host.report_map)
        # The literal regression: _finalize_classic_hid (and the fallback
        # descriptor it installs) must never run on a refused pairing link.
        self.assertNotEqual(FALLBACK_HID_DESCRIPTOR, host.report_map)

    def test_pairing_refused_when_sdp_incomplete_and_nothing_cached(self):
        # None (query did not complete) plus no cache plus not a phone:
        # no descriptor exists anywhere, so the gate must refuse and
        # _finalize_classic_hid (and its fallback descriptor) must never
        # be reached.
        unusable = types.SimpleNamespace(id=0x0206, value="not-a-sequence")
        host = self._pairing_flow_host(
            sdp_search_result=[[unusable]], cached=False, is_phone=False)

        self.assertTrue(asyncio.run(host._pair_classic(self.ADDR)))
        asyncio.run(host._continue_classic_after_pairing())

        self.assertTrue(host.connection.is_disconnected)
        self.assertEqual([], host._created_uhid_calls)
        self.assertIsNone(host.report_map)
        self.assertNotEqual(FALLBACK_HID_DESCRIPTOR, host.report_map)

    def test_pairing_falls_back_to_cache_when_sdp_incomplete_on_a_keyboard(self):
        # The one defensible fallback, reachable from pairing too: a
        # non-phone whose SDP query did not complete, but a real descriptor
        # is cached from a previous session.
        unusable = types.SimpleNamespace(id=0x0206, value="not-a-sequence")
        host = self._pairing_flow_host(
            sdp_search_result=[[unusable]], cached=True, is_phone=False)

        self.assertTrue(asyncio.run(host._pair_classic(self.ADDR)))
        self.assertIsNone(host.report_map)

        asyncio.run(host._continue_classic_after_pairing())

        self.assertFalse(host.connection.is_disconnected)
        self.assertEqual([True], host._created_uhid_calls)
        self.assertEqual(b"\x05\x01", host.report_map)
        self.assertNotEqual(FALLBACK_HID_DESCRIPTOR, host.report_map)

    def test_restore_does_not_rebuild_handler_when_all_devices_passive(self):
        # Regression, and the one that would have bricked a real device.
        # _run_classic_handler constructs a BumbleHIDHost on a Device that
        # already has the HID PSMs registered, which raises 'PSM already in
        # use' AFTER it has cleared both inbound listeners. With every device
        # passive the restore arm fell through to exactly that, leaving
        # Classic deaf for the life of the process.
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone")
        ]
        host.device_cache.set_class(self.ADDR, True)
        host._classic_connection_listener = lambda *a: None
        rebuilt = []
        host._run_classic_handler = lambda: rebuilt.append(True)
        dialed = []
        host._classic_active_connect_loop = lambda addrs: dialed.append(addrs)
        reasserted = []

        async def fake_page_scan(enabled, force=False):
            reasserted.append((enabled, force))

        host._set_classic_page_scan = fake_page_scan

        async def scenario():
            # Called from inside the loop, as _on_protocol_disconnection does.
            host._schedule_protocol_restore(Protocol.CLASSIC)
            await asyncio.sleep(0)

        asyncio.run(scenario())

        self.assertEqual([], rebuilt, "must not rebuild the Classic handler")
        self.assertEqual([], dialed, "nothing to dial when all are passive")
        self.assertNotIn(Protocol.CLASSIC, host._protocol_restore_tasks)
        # Page scan re-asserted rather than assumed: this path never reads the
        # tracked flag, and the keeper skips while the radio lock is held.
        self.assertEqual([(True, True)], reasserted)

    def test_restore_still_dials_when_an_active_device_remains(self):
        host = self.make_host()
        other = "AA:BB:CC:DD:EE:FF"
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Example Phone"),
            DeviceConfig(other, Protocol.CLASSIC, "Real Keyboard"),
        ]
        host.device_cache.set_class(self.ADDR, True)
        host._classic_connection_listener = lambda *a: None
        dialed = []

        async def fake_loop(addrs):
            dialed.append(addrs)

        host._classic_active_connect_loop = fake_loop

        async def scenario():
            host._schedule_protocol_restore(Protocol.CLASSIC)
            task = host._protocol_restore_tasks.get(Protocol.CLASSIC)
            if task is not None:
                await task

        asyncio.run(scenario())

        self.assertEqual([[other]], dialed)

    def test_inbound_request_records_evidence_and_flips_to_passive(self):
        # Producer -> consumer. The declaration alone must not stop dialing;
        # an observed inbound connection is what completes the pair.
        host = self.make_host()
        host.classic_devices = [
            DeviceConfig(self.ADDR, Protocol.CLASSIC, "Real Keyboard")
        ]
        host.device_cache.set_reconnect_initiate(self.ADDR, True)
        self.assertFalse(host._classic_is_passive(self.ADDR))

        # A keyboard's CoD, not a phone's: the evidence must be recorded
        # regardless of device class.
        host._on_classic_connection_request(Address(self.ADDR), 0x000540, 1)

        self.assertTrue(host.device_cache.get_seen_inbound(self.ADDR))
        self.assertTrue(host._classic_is_passive(self.ADDR))

    def test_seen_inbound_survives_a_descriptor_recache(self):
        # The SDP descriptor save passes only report_map/device_name, so
        # without the sticky merge the device would silently revert to being
        # dialed on every reconnect.
        host = self.make_host()
        host.device_cache.set_reconnect_initiate(self.ADDR, True)
        host.device_cache.set_seen_inbound(self.ADDR, True)

        host.device_cache.save(self.ADDR, {
            'report_map': 'aabb',
            'device_name': 'Real Keyboard',
        })

        self.assertTrue(host.device_cache.get_seen_inbound(self.ADDR))
        self.assertTrue(host._classic_is_passive(self.ADDR))

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

            # The property that matters is that a restart does not leak a
            # listener. Identity is no longer a useful probe for it: the
            # handler is a bound method now, not a fresh closure per call, so
            # the old and new registrations compare equal by construction.
            registered = controller._listeners.get('connection_request', [])
            self.assertEqual(1, len(registered))
            self.assertEqual(
                host._classic_connection_request_listener, registered[0]
            )
            self.assertEqual(first_listener, registered[0])

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
            return host.device.scan_enable_writes

        writes = asyncio.run(scenario())

        self.assertEqual(0x02, writes[0])

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

    def test_ble_sliced_keeps_looping_until_the_window_is_spent(self):
        # _ble_sliced hands over the FULL remaining window and lets
        # _ble_initiate clamp itself. Whatever a pass gives up must remain
        # available to the next one. Regression: an earlier version returned
        # after a single clamped pass, turning an 18s BLE window into 2s and
        # discarding the other 16.
        config.classic_page_scan_max_dark = 0.02
        config.classic_page_scan_dwell = 0.0

        async def scenario():
            host = self.make_host()
            windows = []

            async def run(window):
                # Under-consumes, as a clamped _ble_initiate does.
                windows.append(window)
                await asyncio.sleep(0.02)
                return None

            result = await host._ble_sliced(0.3, run)
            return host, windows, result

        host, windows, result = asyncio.run(scenario())

        self.assertIsNone(result)
        self.assertTrue(host._ble_should_pause_classic_page_scan())
        self.assertGreater(len(windows), 1)
        # Each pass is offered what is left, so offers shrink toward zero
        # rather than repeating a fixed slice.
        self.assertAlmostEqual(0.3, windows[0], delta=0.05)
        self.assertLess(windows[-1], windows[0])

    def test_ble_sliced_dwells_between_slices(self):
        # Asserts the dwell timer only: that consecutive `run` invocations are
        # separated by roughly page_scan_dwell. Page scan restoration happens
        # inside the real _ble_initiate, not here.
        config.classic_page_scan_max_dark = 0.02
        config.classic_page_scan_dwell = 0.05

        async def scenario():
            host = self.make_host()
            gaps = []
            last_end = None

            async def run(window):
                nonlocal last_end
                loop = asyncio.get_running_loop()
                if last_end is not None:
                    gaps.append(loop.time() - last_end)
                await asyncio.sleep(0.02)
                last_end = loop.time()
                return None

            await host._ble_sliced(0.4, run)
            return gaps

        gaps = asyncio.run(scenario())

        self.assertTrue(gaps)
        self.assertTrue(all(g >= 0.04 for g in gaps), gaps)

    def test_ble_sliced_returns_as_soon_as_a_slice_connects(self):
        config.classic_page_scan_max_dark = 0.02

        async def scenario():
            host = self.make_host()
            windows = []

            async def run(window):
                # Under-consumes, as a clamped _ble_initiate does, so the
                # loop gets a second pass to hand a connection back on.
                windows.append(window)
                await asyncio.sleep(0.02)
                return "connection" if len(windows) == 2 else None

            result = await host._ble_sliced(1.0, run)
            return windows, result

        windows, result = asyncio.run(scenario())

        self.assertEqual("connection", result)
        self.assertEqual(2, len(windows))

    def test_ble_initiate_cancel_returns_as_soon_as_the_controller_answers(self):
        # The connection/failure listeners are the ONLY things that can resolve
        # `pending`. Unhooking them before the post-cancel wait made that wait
        # always burn its full 1.0s timeout instead of returning when the real
        # failure event landed -- about a second of extra page-scan darkness on
        # every cancelled initiate, which is the common "no device found" case
        # and half again the entire page_scan_max_dark budget.
        class AnsweringDevice(FakeBleDevice):
            async def send_command(self, command, check_result=False):
                name = type(command).__name__
                self.commands.append(name)
                if name == "HCI_LE_Create_Connection_Cancel_Command":
                    # A real exception instance, unlike the shared fake's
                    # SimpleNamespace: that one makes set_exception raise
                    # TypeError inside send_command, so the wait is skipped for
                    # the wrong reason and the timing proves nothing.
                    error = RuntimeError("connection failed")
                    error.transport = 2  # BT_LE_TRANSPORT in the bumble stub
                    for cb in list(self.listeners.get("connection_failure", [])):
                        cb(error)

        async def scenario():
            host = self.make_host()
            host.device = AnsweringDevice()
            host._radio_lock = asyncio.Lock()
            loop = asyncio.get_running_loop()
            started = loop.time()
            await host._ble_initiate(0.05)
            return loop.time() - started

        elapsed = asyncio.run(scenario())

        self.assertLess(
            elapsed, 0.5,
            f"post-cancel wait burned its full timeout ({elapsed:.2f}s); the "
            "failure listener must stay hooked until after the wait")

    def test_ble_initiate_clamps_window_when_it_blanks_page_scan(self):
        # The real dark bound. _ble_sliced decides whether to slice before
        # taking the radio lock, and Classic can drop while we wait on it, so
        # the decision can be stale by the time we blank. This test drives the
        # common case, where _ble_window_for_radio_state has already capped at
        # BLE_CLASSIC_IDLE_WINDOW (12s); the uncapped 18s is reachable only in
        # the narrower race where Classic is connected at entry and drops
        # during the lock wait. Either way the clamp must bind here, at the
        # point the blanking actually happens.
        config.classic_page_scan_max_dark = 0.05

        async def scenario():
            host = self.make_host()
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()
            # Page scan must actually be up: the clamp is deliberately skipped
            # when the blank would be a no-op, so that BLE does not surrender
            # scanning time to buy inbound responsiveness it already has.
            host._classic_page_scan_enabled = True
            writes = []

            async def fake_page_scan(enabled, force=False):
                writes.append(enabled)

            host._set_classic_page_scan = fake_page_scan

            loop = asyncio.get_running_loop()
            started = loop.time()
            # Called directly, bypassing _ble_sliced, as the unsliced path does.
            await host._ble_initiate(18.0)
            return writes, loop.time() - started

        writes, elapsed = asyncio.run(scenario())

        self.assertEqual([False, True], writes)
        # Back under a second: only `le_connecting = False` has to precede the
        # cancel awaits. The listeners are unhooked in a nested finally after
        # the wait, so they are still no less protected against cancellation
        # while remaining able to resolve `pending` early. See
        # test_ble_initiate_cancel_returns_as_soon_as_the_controller_answers.
        self.assertLess(elapsed, 1.0, f"stayed dark {elapsed:.2f}s")

    def test_ble_initiate_does_not_clamp_when_page_scan_is_already_down(self):
        # Complement of the clamp test. If page scan is already down, blanking
        # it changes nothing, so surrendering BLE scanning time to bound the
        # darkness would buy inbound responsiveness that already exists.
        config.classic_page_scan_max_dark = 0.02

        async def scenario():
            host = self.make_host()
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()
            host._classic_page_scan_enabled = False
            writes = []

            async def fake_page_scan(enabled, force=False):
                writes.append(enabled)

            host._set_classic_page_scan = fake_page_scan

            loop = asyncio.get_running_loop()
            started = loop.time()
            await host._ble_initiate(0.3)
            return writes, loop.time() - started

        writes, elapsed = asyncio.run(scenario())

        self.assertEqual([], writes, "nothing to blank, so nothing written")
        self.assertGreater(
            elapsed, 0.2, f"window was clamped to {elapsed:.3f}s for no gain")

    def test_ble_sliced_is_bounded_when_slices_consume_no_time(self):
        # A failed create-connection returns without consuming its window, so
        # a wall-clock deadline alone does not bound the loop: with dwell=0
        # that spun tens of thousands of times, blanking page scan each pass.
        config.classic_page_scan_max_dark = 0.01
        config.classic_page_scan_dwell = 0.0

        async def scenario():
            host = self.make_host()
            calls = []

            async def run(window):
                calls.append(window)
                return None

            await host._ble_sliced(5.0, run)
            return calls

        calls = asyncio.run(scenario())

        self.assertLessEqual(len(calls), 502, f"{len(calls)} iterations")

    def test_ble_sliced_yields_immediately_when_classic_setup_starts(self):
        # Regression: _ble_initiate returns instantly while Classic is setting
        # up, so a naive slicing loop would re-enter it once per slice and burn
        # the whole window rather than yielding once as it did before slicing.
        config.classic_page_scan_max_dark = 0.02

        async def scenario():
            host = self.make_host()
            calls = []

            async def run(window):
                calls.append(window)
                # Classic setup begins during the first slice.
                host.connected_protocol = Protocol.CLASSIC
                host.connection = FakeConnection()
                await asyncio.sleep(window)
                return None

            result = await host._ble_sliced(1.0, run)
            return host, calls, result

        host, calls, result = asyncio.run(scenario())

        self.assertIsNone(result)
        self.assertTrue(host._ble_has_classic_setup_activity())
        self.assertEqual(1, len(calls))

    def test_ble_sliced_does_not_slice_when_page_scan_stays_up(self):
        # Nothing is being blanked, so one long window is strictly better for
        # BLE. The yield-to-classic cap must still apply to that window.
        config.ble_pause_classic_page_scan = False

        async def scenario():
            host = self.make_host()
            windows = []

            async def run(window):
                windows.append(window)
                return "connection"

            result = await host._ble_sliced(host.BLE_INIT_WINDOW, run)
            return host, windows, result

        host, windows, result = asyncio.run(scenario())

        self.assertEqual("connection", result)
        self.assertEqual(1, len(windows))
        self.assertAlmostEqual(host.BLE_CLASSIC_IDLE_WINDOW, windows[0], delta=0.1)

    def test_page_scan_pause_can_be_disabled(self):
        host = self.make_host()
        self.assertTrue(host._ble_should_pause_classic_page_scan())

        config.ble_pause_classic_page_scan = False
        self.assertFalse(host._ble_should_pause_classic_page_scan())

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
        # Paused then restored. Asserted on the resulting scan-enable byte rather
        # than on raw HCI writes: the register is owned through bumble's
        # set_connectable now, so bumble's own state stays in step with it.
        self.assertEqual([0x00, 0x02], host.device.scan_enable_writes)
        self.assertTrue(host._classic_page_scan_enabled)
        self.assertTrue(host.device.connectable)

    def test_ble_initiate_cancel_during_cancel_command_still_releases_lock(self):
        # Regression (Item A1): a cancel landing while awaiting the
        # create-connection cancel command used to leave le_connecting stuck
        # True, the listeners still registered, page scan un-restored, and
        # the radio lock held forever, since `except Exception` at that
        # await does not catch CancelledError and everything downstream of
        # it (in the old, flat finally) was simply skipped.
        async def scenario():
            host = self.make_host()
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()
            host._classic_page_scan_enabled = True

            real_send_command = host.device.send_command

            async def send_command(command, check_result=False):
                if type(command).__name__ == "HCI_LE_Create_Connection_Cancel_Command":
                    raise asyncio.CancelledError()
                return await real_send_command(command, check_result=check_result)

            host.device.send_command = send_command

            with self.assertRaises(asyncio.CancelledError):
                await host._ble_initiate(0.05)

            return host

        host = asyncio.run(scenario())

        self.assertFalse(host.device.le_connecting)
        self.assertEqual([], host.device.listeners.get("connection", []))
        self.assertEqual([], host.device.listeners.get("connection_failure", []))
        self.assertFalse(host._radio_lock.locked())
        # force=True restore still ran despite the cancel, so page scan
        # actually got re-enabled rather than being left dark.
        self.assertTrue(host._classic_page_scan_enabled)
        self.assertTrue(host.device.connectable)

    def test_ble_scan_for_rotated_cancel_during_stop_scanning_still_releases_lock(self):
        # Regression (Item A1b): _ble_scan_for_rotated has the identical
        # shape of bug as _ble_initiate -- a cancel during stop_scanning is
        # not caught by `except Exception`, so the log+release after it used
        # to be skipped, wedging the radio lock forever.
        async def scenario():
            host = self.make_host()
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()

            async def start_scanning(**kwargs):
                return None

            async def stop_scanning(legacy=True):
                raise asyncio.CancelledError()

            host.device.start_scanning = start_scanning
            host.device.stop_scanning = stop_scanning

            with self.assertRaises(asyncio.CancelledError):
                await host._ble_scan_for_rotated(set(), 0.05)

            return host

        host = asyncio.run(scenario())

        self.assertFalse(host._radio_lock.locked())

    def test_classic_active_connect_loop_propagates_parent_cancel(self):
        # Regression (Item A1c): a parent cancel (host teardown cancelling
        # this loop's own task) arriving while the per-attempt cleanup awaits
        # connect_task's own cancellation used to be swallowed by
        # `except (asyncio.CancelledError, Exception): pass`, so the loop
        # kept dialing -- uncancellable -- and cleanup()'s untimed
        # `gather(*pending, return_exceptions=True)` (host.py) would hang the
        # daemon waiting on a task that could never finish.
        async def scenario():
            host = self.make_host()
            host.ACTIVE_DELAY = 0
            # >=1: with 0 the for-loop body never runs, so connect_task is
            # cancelled before the event loop has ever stepped it even once
            # (a task cancelled pre-first-step never enters its own
            # try/except), which would race this test's own hook instead of
            # landing where a real teardown cancel actually lands.
            host.ACTIVE_CONNECT_TIMEOUT = 1
            host.device = FakeClassicDevice()
            host._radio_lock = asyncio.Lock()

            connect_calls = 0
            cancel_started = asyncio.Event()

            async def hanging_connect(*args, **kwargs):
                nonlocal connect_calls
                connect_calls += 1
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    # Still "cancelling" (not done) when the parent cancel
                    # below lands, mirroring a slow real disconnect/teardown.
                    cancel_started.set()
                    await asyncio.sleep(100)
                    raise

            host.device.connect = hanging_connect

            outer = asyncio.create_task(
                host._classic_active_connect_loop([self.ADDR]))
            await asyncio.wait_for(cancel_started.wait(), timeout=2.0)
            # The loop is now suspended awaiting connect_task's own
            # cancellation inside its per-attempt cleanup -- exactly where a
            # real host teardown cancel lands.
            outer.cancel()

            propagated = True
            try:
                await asyncio.wait_for(outer, timeout=2.0)
                propagated = False
            except asyncio.CancelledError:
                propagated = True
            except asyncio.TimeoutError:
                propagated = False
            finally:
                if not outer.done():
                    outer.cancel()
                    try:
                        await outer
                    except asyncio.CancelledError:
                        pass

            return propagated, connect_calls

        propagated, connect_calls = asyncio.run(scenario())

        self.assertTrue(
            propagated, "parent cancel did not propagate out of the loop within 2s")
        self.assertEqual(
            1, connect_calls, "loop kept dialing after the parent cancel")

    def test_ble_initiate_failed_disable_still_forces_a_restore_write(self):
        # Regression (Item A3): the original fix (moving
        # classic_page_scan_paused = True earlier) is a no-op on its own. If
        # the disable raises, _classic_page_scan_enabled is never updated
        # (only set on success), so a restore call without force=True sees
        # enabled == True already and issues nothing -- identical to the bug.
        # force=True is what makes the restore actually write.
        async def scenario():
            host = self.make_host()
            host.device = FakeBleDevice()
            host._radio_lock = asyncio.Lock()
            host._classic_page_scan_enabled = True

            real_set_connectable = host.device.set_connectable
            calls = []

            async def flaky_set_connectable(connectable=True):
                calls.append(connectable)
                if len(calls) == 1:
                    # Mirrors bumble's Device.set_connectable, which flips
                    # `connectable` before awaiting the HCI write -- so a
                    # write failure still leaves the flag at the new value.
                    host.device.connectable = connectable
                    raise RuntimeError("simulated HCI write failure")
                return await real_set_connectable(connectable=connectable)

            host.device.set_connectable = flaky_set_connectable

            # The disable's own RuntimeError propagates out of _ble_initiate
            # uncaught (it happens before any create-connection attempt, so
            # `initiated` is still False and nothing wraps this in a broad
            # except) -- the finally's cleanup still has to run first, which
            # is exactly what this test verifies.
            with self.assertRaises(RuntimeError):
                await host._ble_initiate(0.05)
            return host, calls

        host, calls = asyncio.run(scenario())

        # Both the failed disable and the forced restore were issued.
        self.assertEqual([False, True], calls)
        # Only the second (successful) call reaches the real fake and
        # actually appends a write -- proof the forced restore issued a real
        # command rather than short-circuiting on the stale tracked flag.
        self.assertEqual([0x02], host.device.scan_enable_writes)
        self.assertTrue(host._classic_page_scan_enabled)
        self.assertTrue(host.device.connectable)

    def test_set_classic_page_scan_marks_dark_before_a_failing_disable(self):
        # Regression (Item A4): _classic_page_scan_dark_since must be set
        # BEFORE the awaited disable, not after -- a disable that raises
        # (mirroring bumble's own flag flipping ahead of a failed HCI write)
        # still needs the keeper to know page scan may be down, or a stuck
        # radio lock combined with a failed disable would leave the keeper
        # believing everything is fine forever.
        async def scenario():
            host = self.make_host()
            host.device = FakeClassicDevice()
            host._classic_page_scan_enabled = True

            async def failing_set_connectable(connectable=True):
                raise RuntimeError("simulated HCI write failure")

            host.device.set_connectable = failing_set_connectable

            with self.assertRaises(RuntimeError):
                await host._set_classic_page_scan(False)

            return host._classic_page_scan_dark_since

        dark_since = asyncio.run(scenario())

        self.assertIsNotNone(dark_since)

    def test_set_classic_page_scan_clears_dark_since_on_successful_enable(self):
        async def scenario():
            host = self.make_host()
            host.device = FakeClassicDevice()
            host._classic_page_scan_enabled = True

            await host._set_classic_page_scan(False)
            after_disable = host._classic_page_scan_dark_since

            await host._set_classic_page_scan(True, force=True)
            after_enable = host._classic_page_scan_dark_since

            return after_disable, after_enable

        after_disable, after_enable = asyncio.run(scenario())

        self.assertIsNotNone(after_disable)
        self.assertIsNone(after_enable)

    def test_page_scan_keeper_defers_within_dark_ceiling_while_lock_held(self):
        # Regression (Item A4): the keeper's predicate must be page-scan
        # darkness, not lock tenure. Within the ceiling, a held lock is
        # legitimate (a BLE initiate in progress) and must not be disturbed.
        async def scenario():
            host = self.make_host()
            host.CLASSIC_PAGE_SCAN_REASSERT_INTERVAL = 0.01
            host._radio_lock = asyncio.Lock()
            await host._radio_lock.acquire()
            host._classic_page_scan_enabled = False
            host._classic_page_scan_dark_since = time.monotonic() - 5.0

            calls = []

            async def fake_set(enabled, force=False):
                calls.append((enabled, force))

            host._set_classic_page_scan = fake_set

            task = asyncio.create_task(host._classic_page_scan_keeper())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return calls

        calls = asyncio.run(scenario())

        self.assertEqual(
            [], calls,
            "keeper must not touch page scan while the lock is legitimately held")

    def test_page_scan_keeper_force_asserts_past_dark_ceiling_despite_held_lock(self):
        # Complement: once dark-for exceeds the ceiling, the keeper must
        # force-assert despite the held lock -- the harm is deafness to
        # inbound pages, and a stuck lock cannot be fixed by staying quiet.
        async def scenario():
            host = self.make_host()
            host.CLASSIC_PAGE_SCAN_REASSERT_INTERVAL = 0.01
            host.CLASSIC_PAGE_SCAN_DARK_CEILING = 0.05
            host._radio_lock = asyncio.Lock()
            await host._radio_lock.acquire()
            host._classic_page_scan_enabled = False
            host._classic_page_scan_dark_since = time.monotonic() - 1.0

            calls = []

            async def fake_set(enabled, force=False):
                calls.append((enabled, force))

            host._set_classic_page_scan = fake_set

            task = asyncio.create_task(host._classic_page_scan_keeper())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return calls

        calls = asyncio.run(scenario())

        self.assertIn((True, True), calls)

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
