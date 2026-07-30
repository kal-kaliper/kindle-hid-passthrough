#!/usr/bin/env python3
"""HID Host — runs BLE + Classic handlers on a single Bumble device."""

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from bumble.core import InvalidStateError
from bumble.hci import HCI_LE_SET_PRIVACY_MODE_COMMAND, HCI_LE_Set_Privacy_Mode_Command, HCI_Write_Class_Of_Device_Command, HCI_Write_Local_Name_Command

from ble import BLEMixin
from bt_setup import ensure_uhid
from classic import ClassicMixin
from config import Protocol, config, get_version, normalize_addr
from device_cache import DeviceCache
from logging_utils import log
from pairing import create_keystore, create_pairing_config
from transport import create_bumble_device
from uhid_handler import Bus, UHIDDevice, strip_digitizer_collections

__all__ = ['HIDHost']

# Some BLE keyboards bind their Esc key to the Consumer-page "AC Home"
# control (usage 0x0223) instead of the standard Keyboard-page Escape
# (usage 0x29), so it never reaches KOReader/terminal apps as Escape.
# These are the exact 5-byte Consumer report payloads that keyboard sends
# for that key (report_id 3, bit for AC Home set/cleared); see
# docs/troubleshooting.md for how they were captured.
_CONSUMER_HOME_PRESS = bytes.fromhex('0300010000')
_CONSUMER_HOME_RELEASE = bytes.fromhex('0300000000')
_KEYBOARD_ESCAPE_USAGE = 0x29


@dataclass
class DeviceConfig:
    """Device configuration from devices.conf."""
    address: str
    protocol: Protocol
    name: Optional[str] = None


@dataclass
class DeviceSession:
    protocol: Protocol
    address: str
    connection: object
    peer: object = None
    hid_host: object = None
    device_name: Optional[str] = None
    report_map: Optional[bytes] = None
    is_phone: Optional[bool] = None
    device_class_resolved: bool = False
    uhid_device: Optional[UHIDDevice] = None
    disconnection_event: Optional[asyncio.Event] = None
    last_report: Optional[bytes] = None
    keyboard_last_keys: tuple = field(default_factory=tuple)
    keyboard_last_modifier: int = 0
    report_queue: Optional[queue.Queue] = None
    report_worker: Optional[threading.Thread] = None
    report_sender_stopped: bool = False
    established_at: float = 0.0
    uhid_created_at: float = 0.0
    source_report_count: int = 0
    report_count: int = 0
    last_source_report_hex: Optional[str] = None
    last_uhid_report_hex: Optional[str] = None
    recent_source_reports: List[str] = field(default_factory=list)
    recent_uhid_reports: List[str] = field(default_factory=list)
    classic_setup_ms: Optional[int] = None
    classic_channels_ms: Optional[int] = None
    classic_hid_ready_ms: Optional[int] = None
    classic_channel_origin: Optional[str] = None
    classic_set_protocol_ok: Optional[bool] = None
    classic_set_protocol_error: Optional[str] = None


class HIDHost(ClassicMixin, BLEMixin):
    """HID Host supporting both BLE and Classic Bluetooth.

    Protocol-specific handlers live in ClassicMixin and BLEMixin.
    This class owns init, start, run, pairing dispatch, and cleanup.
    """

    PROTOCOL_NAME = "HID"

    ACTIVE_DELAY = 2.0
    # Do not keep waking the shared MTK radio just because a configured
    # keyboard is not nearby.  The first probe remains quick; subsequent
    # probes are deliberately sparse and happen on the already-open host.
    IDLE_PROBE_INITIAL_DELAY = 60.0
    IDLE_PROBE_MAX_DELAY = 300.0
    CLASSIC_BACKOFF_POLL_INTERVAL = 30.0
    # One HCI command per interval; cheap enough to be frequent, and the ceiling
    # on how long an inbound-only device stays unreachable if page scan is lost.
    CLASSIC_PAGE_SCAN_REASSERT_INTERVAL = 30.0
    ACTIVE_CONNECT_TIMEOUT = 10
    CLASSIC_AUTH_RETRY_DELAY = 8.0
    CLASSIC_AUTH_RETRY_DELAY_WITH_PENDING_BLE = 20.0
    CLASSIC_FLAP_WINDOW = 30.0
    CLASSIC_FLAP_BACKOFF_BASE = 20.0
    CLASSIC_FLAP_BACKOFF_MAX = 300.0
    CLASSIC_PARKED_RETRY_DELAY = 5.0
    CLASSIC_REMOTE_DISCONNECT_REASONS = frozenset({0x13, 0x14, 0x15})
    # HCI disconnect reasons that unambiguously mean the BLE peer rejected our
    # stored bond: 0x05 Authentication Failure, 0x06 PIN or Key Missing. A bond
    # that fails this way will fail every future restore identically, so it is
    # forgotten immediately to let reconnection fall back to fresh pairing.
    BLE_BOND_REJECT_REASONS = frozenset({0x05, 0x06})
    # 0x3E "Connection Failed to be Established" is overwhelmingly a transient
    # RF/timing failure, not a credential rejection, so it is NOT one-shot
    # forgotten. Instead we require this many *consecutive* restore failures
    # against the same (resolved) identity address before treating it as a
    # rejected bond. Any successful restore resets the count to zero.
    BLE_BOND_3E_FORGET_THRESHOLD = 3

    def __init__(self, transport_spec: str = None):
        self.transport_spec = transport_spec or config.transport
        self.transport = None
        self.device = None
        self.connection = None
        self.peer = None

        self.hid_host = None
        self.connected_protocol = None

        self._connection_tasks: set = set()

        self.current_device_address = None
        self.device_name = None
        self.report_map: Optional[bytes] = None
        self.hid_reports = []

        self.classic_devices: List[DeviceConfig] = []
        self.ble_devices: List[DeviceConfig] = []
        self._keystore_addresses: set = set()
        self._keystore_address_types: dict = {}

        self.keystore = create_keystore(config.pairing_keys_file)
        self.device_cache = DeviceCache(config.cache_dir)

        self.uhid_device = None
        self._uhid_created_at = None
        self.sessions: dict[Protocol, DeviceSession] = {}

        self._disconnection_event = None
        self._protocol_disconnection_events: dict[Protocol, asyncio.Event] = {}
        self._last_ble_disconnect_reason = None
        # Consecutive-0x3E-restore-failure count per resolved BLE identity
        # address; carried across host restarts via daemon._seed_host_state /
        # _capture_host_state (lost on suspend/process restart, which is fine).
        self._ble_bond_3e_fail_counts: dict[str, int] = {}
        self._connection_future = None
        self._session_setup_lock = None
        self._allow_legacy_connection_state = False
        self._protocol_restore_tasks: dict[Protocol, asyncio.Task] = {}
        self._last_report = None
        self._auth_failure_address = None
        self._virtual_cable_unplug_address = None
        self._classic_retry_not_before = 0.0
        self._classic_flap_counts: dict[str, int] = {}
        self._classic_flap_until: dict[str, float] = {}
        self._classic_pending_session: Optional[DeviceSession] = None
        self._classic_active_connect_task = None
        self._classic_page_scan_enabled = False
        self._classic_setup_started_at = None
        self._classic_channels_opened_at = None
        self._classic_hid_ready_at = None
        self._classic_channel_origin = None
        self._classic_set_protocol_ok = None
        self._classic_set_protocol_error = None
        self.last_pair_error = None
        self._radio_lock = None
        self._connection_wait_started_at = None
        self._idle_probe_failures = {
            Protocol.CLASSIC: 0,
            Protocol.BLE: 0,
        }

    @property
    def connection_state(self) -> dict:
        """Current connection state as a dict for API consumers."""
        connections = [
            self._session_state(session)
            for session in self.sessions.values()
            if self._is_session_alive(session)
        ]
        if not connections:
            if self._allow_legacy_connection_state and self._is_connection_alive():
                return self._legacy_connection_state()
            return {"connected": False}

        primary = connections[0]
        state = {"connected": True, "connections": connections}
        state.update({
            "address": primary.get("address"),
            "protocol": primary.get("protocol"),
            "name": primary.get("name"),
        })
        for key in (
            "uhid_name",
            "input_paths",
            "descriptor_size",
            "source_report_count",
            "uhid_report_count",
        ):
            if key in primary:
                state[key] = primary[key]
        return state

    def _session_state(self, session: DeviceSession) -> dict:
        state = {
            "address": normalize_addr(session.address) if session.address else None,
            "protocol": session.protocol.value,
            "name": session.device_name,
        }
        # controller.STATUS_CONNECTION_KEYS has always whitelisted hid_ready, but
        # this method never emitted it, so API consumers saw the key promised and
        # never delivered. It is per-session here: a session has HID if it owns a
        # UHID device.
        state["hid_ready"] = session.uhid_device is not None
        if session.uhid_device:
            state["uhid_name"] = session.uhid_device.name
            if session.uhid_device.input_paths:
                state["input_paths"] = session.uhid_device.input_paths
        if session.report_map:
            state["descriptor_size"] = len(session.report_map)
        if session.is_phone is not None:
            state["is_phone"] = session.is_phone
        state["source_report_count"] = session.source_report_count
        state["uhid_report_count"] = session.report_count
        if config.diagnostics_include_reports:
            state["last_source_report"] = session.last_source_report_hex
            state["last_uhid_report"] = session.last_uhid_report_hex
            state["recent_source_reports"] = list(session.recent_source_reports)
            state["recent_uhid_reports"] = list(session.recent_uhid_reports)
        return state

    def _legacy_connection_state(self) -> dict:
        state = {
            "connected": True,
            "address": normalize_addr(self.current_device_address) if self.current_device_address else None,
            "protocol": self.connected_protocol.value if self.connected_protocol else None,
            "name": self.device_name,
            "hid_ready": self.uhid_device is not None,
        }
        if self.uhid_device:
            state["uhid_name"] = self.uhid_device.name
            if self.uhid_device.input_paths:
                state["input_paths"] = self.uhid_device.input_paths
        if self.report_map:
            state["descriptor_size"] = len(self.report_map)
        return state

    def _parse_devices(self):
        """Parse devices from config and group by protocol."""
        devices = config.get_all_devices()
        self.classic_devices = []
        self.ble_devices = []

        for addr, protocol, name in devices:
            dev = DeviceConfig(address=addr, protocol=protocol, name=name)
            if protocol == Protocol.CLASSIC:
                self.classic_devices.append(dev)
            else:
                self.ble_devices.append(dev)

        log.info(f"Devices: {len(self.classic_devices)} Classic, {len(self.ble_devices)} BLE")

    async def start(self):
        """Initialize the Bumble device with both protocols."""
        log.info(f"HID Host v{get_version()}")

        def configure(device):
            device.classic_enabled = bool(self.classic_devices)
            device.le_enabled = bool(self.ble_devices)
            device.keystore = self.keystore
            device.pairing_config_factory = create_pairing_config
            if self.classic_devices:
                device.classic_ssp_enabled = True
                device.classic_sc_enabled = True
                # Own the scan-enable register through bumble's own state rather
                # than writing HCI_Write_Scan_Enable behind its back. bumble
                # recomputes the whole byte from these two flags whenever
                # set_connectable/set_discoverable is called, including during
                # power_on, and it defaults both to True — which made us
                # discoverable (0x03) and then relied on a side-channel write to
                # narrow it. Setting them here means power_on already writes the
                # value we want, and any later recompute produces the same byte.
                device.connectable = True     # page scan: accept inbound links
                device.discoverable = False   # inquiry scan: not advertised

        self.transport, self.device = await create_bumble_device(
            self.transport_spec, configure=configure)

        if self.device.address_resolution_offload:
            await self._set_device_privacy_modes()
            log.info("Controller address resolution enabled")

        # Classic-specific setup
        if self.classic_devices:
            class_of_device = 0x000104  # Computer/Desktop
            await self.device.host.send_command(
                HCI_Write_Class_Of_Device_Command(class_of_device=class_of_device),
                check_result=True
            )
            log.info(f"Classic enabled: CoD 0x{class_of_device:06X}")

            local_name_bytes = config.device_name.encode('utf-8') + b'\x00'
            await self.device.host.send_command(
                HCI_Write_Local_Name_Command(local_name=local_name_bytes),
                check_result=True
            )

        if self.ble_devices:
            log.info("BLE enabled")

        # Load keystore addresses
        await self._load_keystore_addresses()


    async def _set_device_privacy_modes(self):
        """Keep bonded peers visible when they advertise with their
        identity address instead of an RPA."""
        if not self.device.host.supports_command(HCI_LE_SET_PRIVACY_MODE_COMMAND):
            return
        for _, address in await self.keystore.get_resolving_keys():
            try:
                await self.device.send_command(
                    HCI_LE_Set_Privacy_Mode_Command(
                        peer_identity_address_type=address.address_type,
                        peer_identity_address=address,
                        privacy_mode=HCI_LE_Set_Privacy_Mode_Command.PrivacyMode.DEVICE_PRIVACY_MODE,
                    ), check_result=True)
            except Exception as e:
                log.warning(f"Privacy mode for {address}: {e}")

    async def _load_keystore_addresses(self):
        """Load addresses from keystore for connection filtering."""
        self._keystore_addresses = set()
        self._keystore_address_types = {}
        if self.keystore:
            try:
                keys = await self.keystore.get_all()
                if keys:
                    for entry in keys:
                        addr = str(entry[0]) if isinstance(entry, (list, tuple)) else str(entry)
                        self._keystore_addresses.add(normalize_addr(addr))
                        pairing_keys = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else None
                        if pairing_keys is not None and pairing_keys.address_type is not None:
                            self._keystore_address_types[normalize_addr(addr)] = pairing_keys.address_type
                    log.info(f"Keystore has {len(self._keystore_addresses)} entries")
            except Exception as e:
                log.warning(f"Failed to load keystore: {e}")

    def _configured_name(self, addr: str) -> Optional[str]:
        """Return the configured devices.conf name for addr, if any."""
        if not addr:
            return None
        norm = normalize_addr(addr)
        for dev in self.classic_devices + self.ble_devices:
            if normalize_addr(dev.address) == norm and dev.name:
                return dev.name
        return None

    def _format_device(self, addr: str) -> str:
        """Format device address with name if available."""
        name = self._configured_name(addr)
        return f"{name} ({addr})" if name else addr

    async def run(self):
        """Main run loop - handle both protocols concurrently."""
        self._disconnection_event = asyncio.Event()
        self._protocol_disconnection_events = {
            Protocol.CLASSIC: asyncio.Event(),
            Protocol.BLE: asyncio.Event(),
        }
        self._connection_future = asyncio.get_event_loop().create_future()
        self._session_setup_lock = asyncio.Lock()
        self._allow_legacy_connection_state = False
        self._radio_lock = asyncio.Lock()
        self._connection_wait_started_at = time.monotonic()
        self._protocol_restore_tasks = {}
        self._classic_active_connect_task = None

        self._parse_devices()
        await self.start()

        for dev in self.classic_devices + self.ble_devices:
            if dev.address != '*':
                cache = self.device_cache.load(dev.address)
                if cache and 'report_map' in cache:
                    log.info(f"Cached descriptor for {self._format_device(dev.address)}")

        tasks = []

        if self.classic_devices:
            tasks.append(asyncio.create_task(
                self._run_classic_handler(),
                name="classic_handler"
            ))

        if self.ble_devices:
            tasks.append(asyncio.create_task(
                self._run_ble_handler(),
                name="ble_handler"
            ))

        if not tasks:
            log.error("No devices configured")
            return

        log.info(f"Waiting for connection (Classic: {len(self.classic_devices)}, BLE: {len(self.ble_devices)})")

        try:
            # An absent keyboard is normal.  Keeping this host alive avoids
            # the old 60-second failure path, which destroyed the transport
            # and caused the daemon to issue a fresh HCI Reset every minute.
            await self._connection_future
            log.success("\nReceiving HID reports. Press Ctrl+C to exit.")
            # Re-derive on wake rather than trusting the flag. The event is a
            # latch that nothing clears, so a teardown requested while no session
            # existed would otherwise fire the moment one appears. Any legitimate
            # setter only runs when no session is live, so a live session here
            # means the request is stale: drop it and keep waiting. This makes a
            # future mis-set harmless instead of fatal.
            while True:
                await self._disconnection_event.wait()
                if not any(
                    self._is_session_alive(s) for s in self.sessions.values()
                ):
                    break
                log.info(
                    "Host teardown requested while a session is live; "
                    "treating it as stale"
                )
                self._disconnection_event.clear()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def _acquire_radio_lock(self, operation: str) -> float:
        """Acquire the shared controller lock and log how long it queued."""
        started = time.monotonic()
        await self._radio_lock.acquire()
        waited = time.monotonic() - started
        log.info(f"[Radio] {operation}: lock acquired after {waited:.2f}s")
        return time.monotonic()

    def _connection_wait_elapsed(self) -> float:
        """Seconds since this host began waiting for a configured device."""
        if self._connection_wait_started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._connection_wait_started_at)

    def _next_idle_probe_delay(self, protocol: Protocol) -> float:
        """Return a bounded exponential delay after an unsuccessful probe."""
        failures = self._idle_probe_failures.get(protocol, 0) + 1
        self._idle_probe_failures[protocol] = failures
        delay = min(
            self.IDLE_PROBE_INITIAL_DELAY * (2 ** (failures - 1)),
            self.IDLE_PROBE_MAX_DELAY,
        )
        log.info(
            f"[{protocol.value.upper()}] No device present; idle for "
            f"{delay:.0f}s before probe {failures + 1}"
        )
        return delay

    def _reset_idle_probe_backoff(self, protocol: Protocol):
        self._idle_probe_failures[protocol] = 0

    # ==================== PAIRING ====================

    async def pair_device(self, address: str, protocol: Protocol = None, name: str = None) -> bool:
        """Pair with a device (first-time setup)."""
        if protocol is None:
            protocol = Protocol.BLE

        self._parse_devices()

        if protocol == Protocol.CLASSIC:
            self.classic_devices = [DeviceConfig(address=address, protocol=protocol, name=name)]
            self.ble_devices = []
        else:
            self.ble_devices = [DeviceConfig(address=address, protocol=protocol, name=name)]
            self.classic_devices = []

        await self.start()

        if protocol == Protocol.CLASSIC:
            return await self._pair_classic(address)
        else:
            return await self._pair_ble(address)

    async def continue_after_pairing(self):
        """Continue into run mode after successful pairing."""
        if not self.connected_protocol:
            raise InvalidStateError("No paired device - call pair_device first")

        if self.connected_protocol == Protocol.CLASSIC and not self.connection:
            raise InvalidStateError("No connection - call pair_device first")

        self._disconnection_event = asyncio.Event()
        self._allow_legacy_connection_state = True

        if self.connection:
            self.connection.on('disconnection', self._on_disconnection)

        if self.connected_protocol == Protocol.CLASSIC:
            await self._continue_classic_after_pairing()
        else:
            await self._continue_ble_after_pairing()

        proto_name = self.connected_protocol.value.upper()
        log.success(f"\n[{proto_name}] Paired and receiving HID reports. Press Ctrl+C to exit.")

        await self._disconnection_event.wait()

    # ==================== COMMON ====================

    def _on_disconnection(self, reason):
        """Handle device disconnection."""
        self._on_protocol_disconnection(
            self.connected_protocol, self.current_device_address, reason)

    def _on_protocol_disconnection(self, protocol, address, reason):
        proto = protocol.value.upper() if protocol else "Unknown"
        addr = address or "unknown"
        now = time.monotonic()
        if protocol == Protocol.BLE:
            # Recorded so an in-flight bond restore can tell a credential
            # rejection (forget the bond) from a transient drop (keep it).
            self._last_ble_disconnect_reason = reason
        session = self.sessions.get(protocol) if protocol else None
        pending_session = None
        was_pending = False
        if (
            not session
            and protocol == Protocol.CLASSIC
            and self._classic_pending_session
        ):
            pending_addr = normalize_addr(self._classic_pending_session.address)
            if not address or normalize_addr(address) == pending_addr:
                pending_session = self._classic_pending_session
                session = pending_session
                was_pending = True
        if session:
            session_age = now - session.established_at if session.established_at else 0.0
            uhid_age = now - session.uhid_created_at if session.uhid_created_at else 0.0
            input_paths = (
                getattr(session.uhid_device, "input_paths", [])
                if session.uhid_device else []
            )
            report_detail = ""
            if config.diagnostics_include_reports:
                report_detail = (
                    f"last_source={session.last_source_report_hex}, "
                    f"last_uhid={session.last_uhid_report_hex}, "
                )
            log.warning(
                f"[{proto}] Device disconnected: {addr} (reason={reason}, "
                f"session_age={session_age:.2f}s, uhid_age={uhid_age:.2f}s, "
                f"source_reports={session.source_report_count}, "
                f"uhid_reports={session.report_count}, "
                f"{report_detail}"
                f"classic_setup_ms={session.classic_setup_ms}, "
                f"classic_channels_ms={session.classic_channels_ms}, "
                f"classic_hid_ready_ms={session.classic_hid_ready_ms}, "
                f"classic_channel_origin={session.classic_channel_origin}, "
                f"classic_set_protocol_ok={session.classic_set_protocol_ok}, "
                f"input_paths={input_paths}, "
                f"live={self._live_protocols()})"
            )
        else:
            log.warning(f"[{proto}] Device disconnected: {addr} (reason={reason})")

        if reason == 5 and address and proto == "CLASSIC":
            log.info("[Classic] Authentication failure; keeping bond and retrying")
            retry_delay = self.CLASSIC_AUTH_RETRY_DELAY
            if self.ble_devices and not self._is_protocol_connected(Protocol.BLE):
                retry_delay = self.CLASSIC_AUTH_RETRY_DELAY_WITH_PENDING_BLE
            self._classic_retry_not_before = time.monotonic() + retry_delay
            log.info(f"[Classic] Deferring retry for {retry_delay:.0f}s")

        if was_pending:
            session = pending_session
            self._classic_pending_session = None
        else:
            session = self.sessions.pop(protocol, None) if protocol else None
        if session and session.uhid_device:
            try:
                session.uhid_device.destroy()
            except Exception:
                pass
        if session and protocol == Protocol.CLASSIC:
            self._update_classic_flap_backoff(session, reason)
        short_idle_classic_drop = bool(
            session
            and protocol == Protocol.CLASSIC
            and session.source_report_count == 0
            and self._classic_short_idle_retry(session)
        )
        # A passive (connect-on-demand) device's idle drop is not restored:
        # never dial it back, just leave the host alive and let page-scan
        # accept its next self-initiated connection whenever it comes.
        passive_classic_idle_drop = bool(
            short_idle_classic_drop
            and self._classic_is_passive(session.address, session.device_name)
        )

        if protocol in self._protocol_disconnection_events:
            self._protocol_disconnection_events[protocol].set()
        live_sessions = any(
            self._is_session_alive(s) for s in self.sessions.values()
        )
        if self._disconnection_event:
            if was_pending and self._has_configured_devices(protocol):
                log.info(f"[{proto}] Parked link dropped before input; keeping host alive")
            elif passive_classic_idle_drop:
                log.info(
                    f"[Classic] Passive device {self._format_device(session.address)} "
                    "idle drop; awaiting inbound reconnect"
                )
            elif session and self._has_configured_devices(protocol):
                if live_sessions:
                    log.info(
                        f"[{proto}] Restoring dropped session without restarting "
                        "other live protocol"
                    )
                    self._schedule_protocol_restore(protocol)
                elif short_idle_classic_drop:
                    log.info(
                        "[Classic] Restoring idle phone link without host restart"
                    )
                    self._schedule_protocol_restore(protocol)
                else:
                    log.info(f"[{proto}] Restarting host to restore configured device")
                    self._disconnection_event.set()
            elif not live_sessions:
                # No session was ever recorded, so this is a link that failed
                # during setup rather than a session that ended. Asking for a
                # host rebuild here is wrong while the protocol still has
                # configured devices: the event is latched and never cleared, so
                # a failed attempt would tear down the NEXT successful session
                # the moment it resolves _connection_future, 90ms after it came
                # up. Stay alive and let the device try again, as the parked-link
                # branch above already does.
                # Gated on _protocol_disconnection_events, not just on configured
                # devices: that dict is populated only by run(), so it is the
                # signal for "daemon mode". continue_after_pairing() also ends up
                # here with no session, and pair_device() populates the device
                # lists, so gating on those alone would swallow the set() that
                # pairing mode waits on and hang it forever.
                if (
                    protocol in self._protocol_disconnection_events
                    and self._has_configured_devices(protocol)
                ):
                    log.info(
                        f"[{proto}] Link dropped before a session was recorded; "
                        "keeping host alive"
                    )
                else:
                    log.info(f"[{proto}] Link dropped; requesting host teardown")
                    self._disconnection_event.set()
        if protocol == self.connected_protocol and protocol not in self.sessions:
            self.connection = None
            self.peer = None
            self.current_device_address = None
            self.connected_protocol = None

    def _forward_report(self, data: bytes):
        """Deduplicate, log, and forward an HID report to UHID."""
        self._forward_report_for_protocol(self.connected_protocol, data)

    def _forward_report_for_protocol(self, protocol: Protocol, data: bytes):
        session = self.sessions.get(protocol)
        if session:
            self._forward_report_for_session(session, data)
            return

        if self.sessions and protocol is not None:
            report_detail = (
                f": {data.hex()}" if config.diagnostics_include_reports else ""
            )
            log.warning(
                f"[{protocol.value.upper()}] Dropping report without live "
                f"session{report_detail}"
            )
            return

        if data != self._last_report:
            if config.diagnostics_include_reports:
                log.debug(f"Report: {data.hex()}")
            self._last_report = data
        if self.uhid_device:
            try:
                self.uhid_device.send_input(data)
            except Exception as e:
                log.warning(f"UHID send failed: {e}")

    def _forward_report_for_session(self, session: DeviceSession, data: bytes):
        session.source_report_count += 1
        source_hex = data.hex()
        session.last_source_report_hex = source_hex
        self._append_recent_report(session.recent_source_reports, source_hex)

        escape_report = self._remap_consumer_home_to_escape(session, data)
        reports = (
            (escape_report,)
            if escape_report is not None
            else self._reports_for_session(session, data)
        )
        delay_ms = self._serialized_report_delay_ms(session)
        if delay_ms > 0:
            self._queue_reports_for_session(session, reports, delay_ms / 1000.0)
            return

        for report in reports:
            self._send_report_for_session(session, report)

    def _queue_reports_for_session(self, session: DeviceSession, reports, delay: float):
        if not reports:
            return
        if session.report_queue is None:
            session.report_sender_stopped = False
            session.report_queue = queue.Queue()
            session.report_worker = threading.Thread(
                target=self._report_sender_worker,
                args=(session,),
                name=f"hid_report_sender_{session.protocol.value}",
                daemon=True,
            )
            session.report_worker.start()
        for index, report in enumerate(reports):
            session.report_queue.put((report, delay if index else 0.0))

    def _report_sender_worker(self, session: DeviceSession):
        while True:
            item = session.report_queue.get()
            try:
                if item is None:
                    return
                report, delay = item
                if session.report_sender_stopped:
                    return
                if delay > 0:
                    time.sleep(delay)
                if session.report_sender_stopped:
                    return
                self._send_report_for_session(session, report)
            finally:
                session.report_queue.task_done()

    def _stop_report_sender(self, session: DeviceSession):
        session.report_sender_stopped = True
        if session.report_queue is not None:
            session.report_queue.put(None)
        if session.report_worker is not None:
            session.report_worker.join(timeout=0.5)
        session.report_queue = None
        session.report_worker = None

    def _send_report_for_session(self, session: DeviceSession, data: bytes):
        session.report_count += 1
        uhid_hex = data.hex()
        session.last_uhid_report_hex = uhid_hex
        self._append_recent_report(session.recent_uhid_reports, uhid_hex)
        if data != session.last_report:
            if config.diagnostics_include_reports:
                log.debug(f"Report: {data.hex()}")
            session.last_report = data
        if session.uhid_device:
            try:
                session.uhid_device.send_input(data)
            except Exception as e:
                log.warning(f"UHID send failed: {e}")

    def _append_recent_report(self, reports: List[str], report_hex: str):
        reports.append(report_hex)
        max_count = max(0, config.diagnostics_recent_report_count)
        if max_count == 0:
            reports.clear()
        elif len(reports) > max_count:
            del reports[:-max_count]

    def _remap_consumer_home_to_escape(self, session: DeviceSession, data: bytes) -> Optional[bytes]:
        """Translate a mis-bound Consumer 'AC Home' press/release into a
        synthesized Keyboard-page Escape report, for keyboards whose Esc
        key sends the wrong HID usage. Returns None if remapping is
        disabled or `data` isn't one of the known Home reports."""
        if session.protocol != Protocol.BLE or not config.ble_remap_consumer_home_to_escape:
            return None
        report_ids = self._keyboard_report_ids(session)
        if not report_ids:
            return None
        report_id = report_ids[0]
        if data == _CONSUMER_HOME_PRESS:
            return self._make_keyboard_report(report_id, 0, (_KEYBOARD_ESCAPE_USAGE,), 9)
        if data == _CONSUMER_HOME_RELEASE:
            return self._make_keyboard_report(report_id, 0, (), 9)
        return None

    def _reports_for_session(self, session: DeviceSession, data: bytes):
        if not self._serialize_keyboard_reports(session):
            return (data,)

        parsed = self._parse_keyboard_report(
            data, self._keyboard_report_ids(session))
        if not parsed:
            session.keyboard_last_keys = ()
            session.keyboard_last_modifier = 0
            return (data,)

        report_id, modifier, keys, report_len = parsed
        modifier &= self._keyboard_modifier_mask(session)
        current = tuple(key for key in keys if key)
        previous = session.keyboard_last_keys
        previous_modifier = session.keyboard_last_modifier
        previous_set = set(previous)
        session.keyboard_last_keys = current
        session.keyboard_last_modifier = modifier

        release = self._make_keyboard_report(report_id, 0, (), report_len)
        if not current:
            return ()

        if current == previous:
            if modifier != previous_modifier:
                return ()
            keys_to_tap = current
        else:
            keys_to_tap = tuple(key for key in current if key not in previous_set)

        reports = []
        for key in keys_to_tap:
            reports.append(self._make_keyboard_report(report_id, modifier, (key,), report_len))
            reports.append(release)
        return tuple(reports)

    def _serialize_keyboard_reports(self, session: DeviceSession) -> bool:
        if session.protocol == Protocol.BLE:
            mode = config.ble_serialize_keyboard_reports_mode
        elif session.protocol == Protocol.CLASSIC:
            mode = config.classic_serialize_keyboard_reports_mode
        else:
            return False
        if mode == 'always':
            return True
        if mode == 'never':
            return False
        return self._session_is_phone(session) is True

    def _session_is_phone(self, session: DeviceSession) -> Optional[bool]:
        """Resolve this session's device class, re-reading the cache once.

        `is_phone` is a snapshot taken when the session was recorded, and a
        device can be classified after that: both writers (the inbound
        connection_request handler and the manual scan) run independently of
        session setup. Re-read once per session rather than per report, since
        this is on the report path.

        A device this host always dials first is never classified by either
        writer, so its class can stay unknown indefinitely. Unknown means
        "treat as a physical keyboard", which is the safe direction, but say so
        once instead of letting it decide silently.
        """
        if session.is_phone is None and not session.device_class_resolved:
            session.device_class_resolved = True
            session.is_phone = self.device_cache.get_is_phone(session.address)
            if session.is_phone is None:
                # Info, not a warning: an unknown class is the normal state for
                # any device paired before class capture existed, and the
                # physical-keyboard fallback is correct for almost all of them.
                # It only needs acting on if the device really is a phone.
                log.info(
                    f"[{session.protocol.value.upper()}] Device class unknown "
                    f"for {self._format_device(session.address)}; treating it "
                    "as a physical keyboard, reports not serialized. If it is "
                    "a phone running a HID keyboard app, re-add it through a "
                    "scan to cache its class."
                )
        return session.is_phone

    def _serialized_report_delay_ms(self, session: DeviceSession) -> int:
        if session.protocol == Protocol.BLE:
            return config.ble_serialized_report_delay_ms
        if session.protocol == Protocol.CLASSIC:
            return config.classic_serialized_report_delay_ms
        return 0

    def _keyboard_modifier_mask(self, session: DeviceSession) -> int:
        if session.protocol == Protocol.BLE:
            return config.ble_keyboard_modifier_mask
        if session.protocol == Protocol.CLASSIC:
            return config.classic_keyboard_modifier_mask
        return 0xff

    def _keyboard_report_ids(self, session: DeviceSession):
        if session.protocol == Protocol.BLE:
            return (2,)
        if session.protocol == Protocol.CLASSIC:
            return (1,)
        return ()

    def _parse_keyboard_report(self, data: bytes, report_ids=(1, 2)):
        if len(data) not in (8, 9):
            return None
        report_id = data[0]
        if report_id not in report_ids:
            return None
        return report_id, data[1], tuple(data[3:]), len(data)

    def _make_keyboard_report(self, report_id: int, modifier: int, keys, report_len: int):
        slot_count = max(0, report_len - 3)
        slots = list(keys[:slot_count])
        slots.extend([0] * (slot_count - len(slots)))
        return bytes([report_id, modifier, 0, *slots])

    def _load_cached_descriptor(self, address: str = None) -> bool:
        """Load report descriptor and device name from cache. Returns True if found."""
        address = address or self.current_device_address
        cache = self.device_cache.load(address)
        if cache and 'report_map' in cache:
            self.report_map = bytes.fromhex(cache['report_map'])
            self.device_name = cache.get('device_name')
            log.success(f"Loaded cached descriptor ({len(self.report_map)} bytes)")
            return True
        return False

    def _create_uhid_device(self):
        """Create UHID virtual device."""
        if not self.report_map:
            log.warning("No report descriptor for UHID")
            return

        if not ensure_uhid():
            log.error("uhid unavailable; connected device will have no input path")
            return

        try:
            name = self._configured_name(self.current_device_address) or self.device_name or "HID Device"
            descriptor = strip_digitizer_collections(self.report_map)
            self.uhid_device = UHIDDevice(
                name=name,
                report_descriptor=descriptor,
                bus=Bus.BLUETOOTH,
                vendor=0,
                product=0,
                uniq=self.current_device_address or "",
            )
            self._uhid_created_at = time.monotonic()
            log.success(f"UHID device created: {name}")
            log.info(
                f"UHID telemetry: protocol="
                f"{self.connected_protocol.value if self.connected_protocol else None}, "
                f"address={self.current_device_address}, "
                f"descriptor={len(self.report_map)} bytes, "
                f"stripped={len(descriptor)} bytes, live={self._live_protocols()}"
            )
            asyncio.get_event_loop().call_later(
                0.5, self.uhid_device.discover_input_paths)
        except Exception as e:
            log.error(f"Failed to create UHID device: {e}")

    def _record_current_session(self, protocol: Protocol):
        self._reset_idle_probe_backoff(protocol)
        event = self._protocol_disconnection_events.get(protocol)
        recorded_at = time.monotonic()
        session = DeviceSession(
            protocol=protocol,
            address=self.current_device_address,
            connection=self.connection,
            peer=self.peer,
            hid_host=self.hid_host if protocol == Protocol.CLASSIC else None,
            device_name=self.device_name,
            report_map=self.report_map,
            is_phone=self.device_cache.get_is_phone(self.current_device_address),
            uhid_device=self.uhid_device,
            disconnection_event=event,
            uhid_created_at=self._uhid_created_at or 0.0,
            classic_setup_ms=self._classic_elapsed_ms(recorded_at),
            classic_channels_ms=self._classic_elapsed_ms(self._classic_channels_opened_at),
            classic_hid_ready_ms=self._classic_elapsed_ms(self._classic_hid_ready_at),
            classic_channel_origin=self._classic_channel_origin,
            classic_set_protocol_ok=self._classic_set_protocol_ok,
            classic_set_protocol_error=self._classic_set_protocol_error,
        )
        session.established_at = recorded_at
        self.sessions[protocol] = session
        log.info(
            f"[{protocol.value.upper()}] Session recorded: "
            f"address={session.address}, name={session.device_name}, "
            f"has_uhid={bool(session.uhid_device)}, live={self._live_protocols()}"
        )
        if self._connection_future and not self._connection_future.done():
            self._connection_future.set_result(session)

    def _protocol_event_is_set(self, protocol: Protocol) -> bool:
        event = self._protocol_disconnection_events.get(protocol)
        return bool(event and event.is_set())

    def _clear_protocol_event(self, protocol: Protocol):
        event = self._protocol_disconnection_events.get(protocol)
        if event:
            event.clear()

    def _is_connection_alive(self) -> bool:
        """Check if the connection is still alive and usable."""
        if self._disconnection_event and self._disconnection_event.is_set():
            return False
        return self._is_raw_connection_alive(self.connection)

    def _is_session_alive(self, session: DeviceSession) -> bool:
        if session.disconnection_event and session.disconnection_event.is_set():
            return False
        return self._is_raw_connection_alive(session.connection)

    def _is_protocol_connected(self, protocol: Protocol) -> bool:
        session = self.sessions.get(protocol)
        return bool(session and self._is_session_alive(session))

    def _has_configured_devices(self, protocol: Protocol) -> bool:
        if protocol == Protocol.CLASSIC:
            return bool(self.classic_devices)
        if protocol == Protocol.BLE:
            return bool(self.ble_devices)
        return False

    def _is_classic_parked(self) -> bool:
        return bool(
            self._classic_pending_session
            and self._is_session_alive(self._classic_pending_session)
        )

    def _live_protocols(self):
        return [
            protocol.value
            for protocol, session in self.sessions.items()
            if self._is_session_alive(session)
        ]

    def _classic_defer_uhid_until_input(self, address: str) -> bool:
        names = config.classic_defer_uhid_until_input_names
        if not names:
            return False
        return self._classic_name_matches(address, names)

    def _classic_elapsed_ms(self, end_time: Optional[float]) -> Optional[int]:
        if self._classic_setup_started_at is None or end_time is None:
            return None
        return max(0, int((end_time - self._classic_setup_started_at) * 1000))

    def _classic_short_idle_retry(self, session: DeviceSession) -> bool:
        names = config.classic_short_idle_retry_names
        if not names:
            return True
        return self._classic_name_matches(session.address, names, session.device_name)

    def _classic_is_passive(self, address: str, name: str = None) -> bool:
        """True if a Classic device is connect-on-demand: never actively
        dialed and never auto-restored after an idle drop, but still
        accepted whenever it initiates the connection (page-scan stays on).

        Primary signal is the configured `classic.passive_names` list
        (matched by this device's own name or address), which still works as
        a manual override. Belt-and-suspenders: also treat a device as
        passive if the device-class cache has resolved it as a phone.
        `is_phone` is populated by the manual-scan flow
        (`device_cache.set_class()` in controller.py) and, live, by
        `classic.py`'s `on_connection_request` handler, which reads the
        remote's Class of Device off the inbound HCI connection request and
        auto-classifies phones — no name needs to be hardcoded for this to
        work. That handler only sees INBOUND connections, so a device this
        host always dials first (never yet seen inbound) won't be
        auto-classified until its first self-initiated connection.

        Deliberately does NOT use `_classic_name_matches`: that helper also
        matches against the global `self.device_name`, which is a stale field
        (the last-connected device's name) and would leak one device's name
        onto another in the multi-device dial loop, silently making a real
        keyboard passive. The match is scoped to this address's identifiers.
        """
        names = config.classic_passive_names
        if names:
            normalized = {
                n.replace("\x00", "").strip().lower()
                for n in names if n and n.strip()
            }
            candidates = [self._configured_name(address), name]
            if any(
                c and c.replace("\x00", "").strip().lower() in normalized
                for c in candidates
            ):
                return True
            if address and normalize_addr(address) in {
                normalize_addr(n) for n in names if n and n.strip()
            }:
                return True
        return self.device_cache.get_is_phone(address) is True

    def _classic_name_matches(self, address: str, names, extra_name: str = None) -> bool:
        configured_name = self._configured_name(address)
        candidates = [configured_name, extra_name, self.device_name]
        normalized_names = {
            name.replace("\x00", "").strip().lower()
            for name in names
            if name and name.strip()
        }
        return any(
            candidate
            and candidate.replace("\x00", "").strip().lower() in normalized_names
            for candidate in candidates
        )

    def _park_classic_session_until_input(self):
        event = self._protocol_disconnection_events.get(Protocol.CLASSIC)
        session = DeviceSession(
            protocol=Protocol.CLASSIC,
            address=self.current_device_address,
            connection=self.connection,
            hid_host=self.hid_host,
            device_name=self.device_name,
            report_map=self.report_map,
            is_phone=self.device_cache.get_is_phone(self.current_device_address),
            uhid_device=None,
            disconnection_event=event,
        )
        session.established_at = time.monotonic()
        self._classic_pending_session = session
        if self._connection_future and not self._connection_future.done():
            self._connection_future.set_result(session)
        log.info(
            f"[Classic] Parked {self._format_device(session.address)}; "
            "waiting for first input report before creating UHID"
        )

    def _promote_classic_pending_session(self) -> bool:
        pending = self._classic_pending_session
        if not pending or not self._is_session_alive(pending):
            return False

        self.connection = pending.connection
        self.hid_host = pending.hid_host
        self.current_device_address = pending.address
        self.device_name = pending.device_name
        self.report_map = pending.report_map
        self.connected_protocol = Protocol.CLASSIC
        self.uhid_device = None
        self._uhid_created_at = None

        self._finalize_classic_hid()
        if not self.uhid_device:
            log.warning("[Classic] Could not promote parked link; UHID unavailable")
            return False

        self._classic_pending_session = None
        self._record_current_session(Protocol.CLASSIC)
        log.success(
            f"[Classic] Promoted parked link after input: "
            f"{self._format_device(self.current_device_address)}"
        )
        return True

    def _schedule_protocol_restore(self, protocol: Protocol):
        existing = self._protocol_restore_tasks.get(protocol)
        if existing and not existing.done():
            return
        if protocol == Protocol.CLASSIC:
            has_classic_listener = bool(
                getattr(self, "_classic_connection_listener", None)
            )
            active_addresses = [
                dev.address for dev in self.classic_devices
                if dev.address != '*' and not self._classic_is_passive(dev.address, dev.name)
            ]
            if has_classic_listener and active_addresses:
                coro = self._classic_active_connect_loop(active_addresses)
            else:
                coro = self._run_classic_handler()
        elif protocol == Protocol.BLE:
            coro = self._run_ble_handler()
        else:
            return
        task = asyncio.create_task(coro, name=f"{protocol.value}_restore")
        self._protocol_restore_tasks[protocol] = task
        self._connection_tasks.add(task)

        def finish(done):
            self._connection_tasks.discard(done)
            if self._protocol_restore_tasks.get(protocol) is done:
                self._protocol_restore_tasks.pop(protocol, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.warning(f"[{protocol.value.upper()}] Restore failed: {e}")

        task.add_done_callback(finish)

    def _is_protocol_connecting(self, protocol: Protocol) -> bool:
        return (
            self.connected_protocol == protocol
            and protocol not in self.sessions
            and self._is_raw_connection_alive(self.connection)
        )

    def _protocol_retry_delay(self, protocol: Protocol) -> float:
        if protocol == Protocol.CLASSIC:
            return max(0.0, self._classic_retry_not_before - time.monotonic())
        return 0.0

    def _update_classic_flap_backoff(self, session: DeviceSession, reason):
        addr = normalize_addr(session.address)
        duration = (
            time.monotonic() - session.established_at
            if session.established_at else 0.0
        )
        if session.last_report is not None or duration >= self.CLASSIC_FLAP_WINDOW:
            had_flap_count = self._classic_flap_counts.pop(addr, None) is not None
            had_retry_deadline = self._classic_flap_until.pop(addr, None) is not None
            if had_flap_count or had_retry_deadline:
                log.info(
                    f"[Classic] {self._format_device(addr)} session healthy; "
                    "clearing flap backoff"
                )
            return

        if reason not in self.CLASSIC_REMOTE_DISCONNECT_REASONS:
            return

        if session.source_report_count == 0 and self._classic_short_idle_retry(session):
            delay = self.CLASSIC_PARKED_RETRY_DELAY
            if self.ble_devices and not self._is_protocol_connected(Protocol.BLE):
                delay = max(delay, self.CLASSIC_AUTH_RETRY_DELAY_WITH_PENDING_BLE)
            self._classic_flap_until[addr] = time.monotonic() + delay
            log.warning(
                f"[Classic] {self._format_device(addr)} idle link dropped "
                f"before input after {duration:.0f}s; retrying in "
                f"{delay:.0f}s"
            )
            return

        count = self._classic_flap_counts.get(addr, 0) + 1
        self._classic_flap_counts[addr] = count
        delay = min(
            self.CLASSIC_FLAP_BACKOFF_BASE * (2 ** (count - 1)),
            self.CLASSIC_FLAP_BACKOFF_MAX,
        )
        self._classic_flap_until[addr] = time.monotonic() + delay
        log.warning(
            f"[Classic] {self._format_device(addr)} dropped by remote after "
            f"{duration:.0f}s without input ({count} in a row); "
            f"deferring dial for {delay:.0f}s"
        )

    def _classic_dial_delay(self, addr: str) -> float:
        until = self._classic_flap_until.get(normalize_addr(addr), 0.0)
        return max(0.0, until - time.monotonic())

    def _is_raw_connection_alive(self, connection) -> bool:
        if connection is None:
            return False
        if not hasattr(connection, 'handle') or connection.handle is None:
            return False
        if hasattr(connection, 'is_disconnected') and connection.is_disconnected:
            return False
        return True

    async def cleanup(self):
        """Clean up resources."""
        had_sessions = bool(self.sessions)
        session_connection_ids = {id(s.connection) for s in self.sessions.values()}
        session_uhid_ids = {
            id(s.uhid_device) for s in self.sessions.values() if s.uhid_device
        }

        if self._connection_tasks:
            pending = list(self._connection_tasks)
            for task in pending:
                if not task.done():
                    task.cancel()
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass
            self._connection_tasks.clear()

        for session in list(self.sessions.values()):
            self._stop_report_sender(session)
            if session.uhid_device:
                try:
                    session.uhid_device.destroy()
                except Exception:
                    pass
            if session.hid_host and self._is_session_alive(session):
                if session.hid_host.l2cap_intr_channel:
                    try:
                        await asyncio.wait_for(
                            session.hid_host.disconnect_interrupt_channel(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        pass
                if session.hid_host.l2cap_ctrl_channel:
                    try:
                        await asyncio.wait_for(
                            session.hid_host.disconnect_control_channel(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        pass
            if self._is_session_alive(session):
                try:
                    await asyncio.wait_for(session.connection.disconnect(), timeout=2.0)
                except asyncio.TimeoutError:
                    log.warning("Connection disconnect timed out")
                except Exception as e:
                    log.debug(f"Disconnect cleanup: {e}")
        self.sessions.clear()

        if self._classic_pending_session:
            pending = self._classic_pending_session
            self._stop_report_sender(pending)
            if pending.uhid_device:
                try:
                    pending.uhid_device.destroy()
                except Exception:
                    pass
            if self._is_session_alive(pending):
                try:
                    await asyncio.wait_for(pending.connection.disconnect(), timeout=2.0)
                except asyncio.TimeoutError:
                    log.warning("Connection disconnect timed out")
                except Exception as e:
                    log.debug(f"Disconnect cleanup: {e}")
            self._classic_pending_session = None

        peer_already_disconnected = (
            self._disconnection_event is not None
            and self._disconnection_event.is_set()
        )

        if (
            self.uhid_device
            and (not had_sessions or id(self.uhid_device) not in session_uhid_ids)
        ):
            try:
                self.uhid_device.destroy()
            except Exception:
                pass
        self.uhid_device = None

        if not had_sessions and self.hid_host:
            if self._is_connection_alive():
                if self.hid_host.l2cap_intr_channel:
                    try:
                        await asyncio.wait_for(
                            self.hid_host.disconnect_interrupt_channel(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        pass
                if self.hid_host.l2cap_ctrl_channel:
                    try:
                        await asyncio.wait_for(
                            self.hid_host.disconnect_control_channel(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        pass
            self.hid_host = None

        unrecorded_connection = (
            self._is_connection_alive()
            and id(self.connection) not in session_connection_ids
        )
        if unrecorded_connection and not peer_already_disconnected:
            try:
                await asyncio.wait_for(self.connection.disconnect(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("Connection disconnect timed out")
            except Exception as e:
                log.debug(f"Disconnect cleanup: {e}")
        self.connection = None
        self.peer = None
        if had_sessions:
            self.hid_host = None

        if hasattr(self, '_classic_connection_listener') and self._classic_connection_listener:
            try:
                self.device.remove_listener('connection', self._classic_connection_listener)
            except Exception:
                pass
            self._classic_connection_listener = None

        if (
            hasattr(self, '_classic_connection_request_listener')
            and self._classic_connection_request_listener
        ):
            try:
                self.device.host.remove_listener(
                    'connection_request', self._classic_connection_request_listener
                )
            except Exception:
                pass
            self._classic_connection_request_listener = None

        if self.transport:
            try:
                await asyncio.wait_for(self.transport.close(), timeout=3.0)
            except asyncio.TimeoutError:
                log.warning("Transport close timed out, fd may leak")
            except Exception:
                pass
            self.transport = None

    async def disconnect_all(self):
        disconnected = False
        for session in list(self.sessions.values()):
            if self._is_session_alive(session):
                try:
                    await session.connection.disconnect()
                    disconnected = True
                except Exception as e:
                    log.debug(f"Disconnect request failed for {session.address}: {e}")
        if not disconnected and self._is_connection_alive():
            await self.connection.disconnect()
            disconnected = True
        return disconnected

    def get_auth_failure_address(self) -> str:
        """Get address that had auth failure, if any."""
        addr = self._auth_failure_address
        self._auth_failure_address = None
        return addr

    def get_virtual_cable_unplug_address(self) -> str:
        """Get address that sent a virtual cable unplug, if any."""
        addr = self._virtual_cable_unplug_address
        self._virtual_cable_unplug_address = None
        return addr
