#!/usr/bin/env python3
"""Classic Bluetooth HID handler mixin for HIDHost."""

import asyncio
import time
from typing import List

from bumble.core import BT_BR_EDR_TRANSPORT, BT_HUMAN_INTERFACE_DEVICE_SERVICE, InvalidStateError
from bumble.core import TimeoutError as BumbleTimeoutError
from bumble.hci import (
    Address,
    HCI_Exit_Sniff_Mode_Command,
    HCI_Write_Link_Policy_Settings_Command,
    HCI_Write_Scan_Enable_Command,
)
from bumble.hid import HID_CONTROL_PSM, HID_INTERRUPT_PSM, Message
from bumble.hid import Host as BumbleHIDHost
from bumble.sdp import Client as SDPClient

from config import Protocol, config, normalize_addr
from logging_utils import log
from scanner import classic_cod_is_phone

FALLBACK_HID_DESCRIPTOR = bytes([
    0x05, 0x01, 0x09, 0x05, 0xa1, 0x01, 0x85, 0x01,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
    0x16, 0x00, 0x00, 0x26, 0xff, 0xff, 0x75, 0x10, 0x95, 0x04, 0x81, 0x02,
    0x05, 0x02, 0x09, 0xc5, 0x09, 0xc4,
    0x16, 0x00, 0x00, 0x26, 0xff, 0x03, 0x75, 0x10, 0x95, 0x02, 0x81, 0x02,
    0x05, 0x01, 0x09, 0x39, 0x15, 0x01, 0x25, 0x08,
    0x35, 0x00, 0x46, 0x3b, 0x01, 0x65, 0x14, 0x75, 0x08, 0x95, 0x01, 0x81, 0x42,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x10,
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02,
    0xc0,
])


class ClassicMixin:
    """Classic Bluetooth methods for HIDHost."""

    async def _run_classic_handler(self):
        """Handle Classic Bluetooth connections."""
        if hasattr(self, '_classic_connection_listener') and self._classic_connection_listener:
            try:
                self.device.remove_listener('connection', self._classic_connection_listener)
            except Exception:
                pass
            self._classic_connection_listener = None

        if hasattr(self, '_classic_connection_request_listener') and self._classic_connection_request_listener:
            try:
                self.device.host.remove_listener(
                    'connection_request', self._classic_connection_request_listener
                )
            except Exception:
                pass
            self._classic_connection_request_listener = None

        classic_hid_host = BumbleHIDHost(self.device)
        self.hid_host = classic_hid_host
        classic_hid_host.on(BumbleHIDHost.EVENT_INTERRUPT_DATA, self._on_classic_interrupt_data)
        classic_hid_host.on(BumbleHIDHost.EVENT_VIRTUAL_CABLE_UNPLUG, self._on_virtual_cable_unplug)
        log.info(f"[Classic] HID Host ready (PSM 0x{HID_CONTROL_PSM:04X}, 0x{HID_INTERRUPT_PSM:04X})")

        await self._set_classic_page_scan(True)

        async def on_classic_connection(connection):
            if self._is_protocol_connected(Protocol.CLASSIC):
                log.info("[Classic] Connection received but Classic is already connected")
                try:
                    await connection.disconnect()
                except Exception:
                    pass
                return

            if not classic_hid_host:
                log.warning("[Classic] Connection received but hid_host not ready, ignoring")
                try:
                    await connection.disconnect()
                except Exception:
                    pass
                return

            addr_str = str(connection.peer_address)
            log.info(f"[Classic] Device connected: {self._format_device(addr_str)}")

            if not self._is_classic_allowed(addr_str):
                log.warning(f"[Classic] Rejecting {addr_str} (not allowed)")
                try:
                    await connection.disconnect()
                except Exception:
                    pass
                return

            async with self._session_setup_lock:
                if self._is_protocol_connected(Protocol.CLASSIC):
                    try:
                        await connection.disconnect()
                    except Exception:
                        pass
                    return

                self._clear_protocol_event(Protocol.CLASSIC)
                self.connection = connection
                self.peer = None
                self.hid_host = classic_hid_host
                self.current_device_address = addr_str
                self.device_name = self._configured_name(addr_str)
                self.report_map = None
                self.hid_reports = []
                self.uhid_device = None
                self._uhid_created_at = None
                self._last_report = None
                self._classic_setup_started_at = time.monotonic()
                self._classic_channels_opened_at = None
                self._classic_hid_ready_at = None
                self._classic_channel_origin = None
                self._classic_set_protocol_ok = None
                self._classic_set_protocol_error = None
                self.connected_protocol = Protocol.CLASSIC

                connection.on(
                    'disconnection',
                    lambda reason, p=Protocol.CLASSIC, a=addr_str:
                    self._on_protocol_disconnection(p, a, reason)
                )

                await self._setup_classic_connection(connection, classic_hid_host)

        def on_connection_event(connection):
            is_classic = (hasattr(connection, 'transport')
                          and connection.transport == BT_BR_EDR_TRANSPORT) \
                          or not hasattr(connection, 'transport')
            if not is_classic:
                return
            task = asyncio.create_task(on_classic_connection(connection))
            self._connection_tasks.add(task)
            task.add_done_callback(self._connection_tasks.discard)

        self._classic_connection_listener = on_connection_event
        self.device.on('connection', on_connection_event)

        def on_connection_request(bd_addr, class_of_device, link_type):
            # Fires for INBOUND Classic connections only (the HCI
            # Connection Request event), before ACL setup completes. This
            # is the only place the remote's Class of Device is available —
            # `Connection` objects never carry it. Used to auto-detect
            # phones so `_classic_is_passive` can treat them as
            # connect-on-demand without a hardcoded name in
            # `classic_passive_names`. An outbound-only device we always
            # dial first (never yet seen inbound) won't be classified via
            # this path; it converges to passive after its first inbound
            # connect, which matches observed phone behavior (phones
            # reconnect to the last-used host on their own).
            try:
                addr_str = str(bd_addr)
                if not self._is_classic_allowed(addr_str):
                    return
                is_phone = classic_cod_is_phone(class_of_device)
                # Log the raw CoD even when it changes nothing. This handler is
                # the only place it is observable, and without the value there
                # is no way to explain after the fact why a device was or was
                # not classified. Same format as the scanner's inquiry line so
                # one pattern finds both.
                log.info(
                    f"[Classic] Inbound from {self._format_device(addr_str)} "
                    f"CoD=0x{class_of_device:06X} phone={is_phone}"
                )
                if not is_phone:
                    return
                if self.device_cache.get_is_phone(addr_str) is True:
                    return
                self.device_cache.set_class(addr_str, True)
                log.info(
                    f"[Classic] Auto-detected phone from CoD: "
                    f"{self._format_device(addr_str)} (now passive/connect-on-demand)"
                )
            except Exception as e:
                log.debug(f"[Classic] connection_request phone-detection failed: {e}")

        self._classic_connection_request_listener = on_connection_request
        self.device.host.on('connection_request', on_connection_request)

        configured_addresses = [d.address for d in self.classic_devices if d.address != '*']
        passive_addresses = [
            d.address for d in self.classic_devices
            if d.address != '*' and self._classic_is_passive(d.address, d.name)
        ]
        active_addresses = [a for a in configured_addresses if a not in passive_addresses]
        if passive_addresses:
            log.info(
                "[Classic] Passive (connect-on-demand) devices, not actively dialed: "
                f"{[self._format_device(a) for a in passive_addresses]}"
            )
        if active_addresses:
            log.info(
                "[Classic] Actively dialing: "
                f"{[self._format_device(a) for a in active_addresses]}"
            )
            await self._classic_active_connect_loop(active_addresses)
        elif configured_addresses:
            log.info(
                "[Classic] All configured devices are passive; active-connect "
                "loop not started, page-scan remains on for inbound connections"
            )

    async def _setup_classic_connection(self, connection, hid_host):
        addr_str = str(connection.peer_address)

        hid_host.on_device_connection(connection)
        hid_host.l2cap_ctrl_channel = None
        hid_host.l2cap_intr_channel = None

        if not getattr(connection, 'is_encrypted', False):
            log.info("[Classic] Authenticating...")
            try:
                await asyncio.wait_for(connection.authenticate(), timeout=8.0)
                log.success("[Classic] Authentication complete")
            except Exception as e:
                log.warning(f"[Classic] Authentication: {e!r}")

        if not getattr(connection, 'is_encrypted', False):
            log.info("[Classic] Requesting encryption...")
            try:
                await asyncio.wait_for(connection.encrypt(enable=True), timeout=10.0)
                log.success("[Classic] Link encrypted")
            except Exception as e:
                log.warning(f"[Classic] Encryption: {e!r}")

        if self._protocol_event_is_set(Protocol.CLASSIC):
            log.warning("[Classic] Connection lost during authentication")
            self.connection = None
            self.current_device_address = None
            self.connected_protocol = None
            return

        await self._classic_disable_low_power(connection)

        log.info("[Classic] Waiting for HID channels...")
        for _ in range(30):
            if self._protocol_event_is_set(Protocol.CLASSIC):
                log.warning("[Classic] Connection lost while waiting for HID channels")
                self.connection = None
                self.current_device_address = None
                self.connected_protocol = None
                return
            self._classic_clear_closed_l2cap_channels(hid_host)
            if self._classic_hid_channels_open(hid_host):
                self._classic_channels_opened_at = time.monotonic()
                self._classic_channel_origin = "remote"
                log.success("[Classic] HID channels opened")
                break
            await asyncio.sleep(0.1)

        if self._protocol_event_is_set(Protocol.CLASSIC):
            log.warning("[Classic] Connection lost during HID setup")
            self.connection = None
            self.current_device_address = None
            self.connected_protocol = None
            return

        if not self._classic_channel_open(hid_host.l2cap_ctrl_channel):
            try:
                await asyncio.wait_for(hid_host.connect_control_channel(), timeout=5.0)
                self._classic_channel_origin = "host"
            except Exception:
                pass

        if not self._classic_channel_open(hid_host.l2cap_intr_channel):
            try:
                await asyncio.wait_for(hid_host.connect_interrupt_channel(), timeout=5.0)
                self._classic_channel_origin = self._classic_channel_origin or "host"
            except Exception:
                pass

        if self._protocol_event_is_set(Protocol.CLASSIC):
            log.warning("[Classic] Connection lost during channel setup")
            self.connection = None
            self.current_device_address = None
            self.connected_protocol = None
            return

        self._classic_clear_closed_l2cap_channels(hid_host)
        if not self._classic_channel_open(hid_host.l2cap_intr_channel):
            log.warning("[Classic] HID interrupt channel failed to connect, dropping link")
            try:
                await connection.disconnect()
            except Exception:
                pass
            self.connection = None
            self.current_device_address = None
            self.connected_protocol = None
            return

        if (
            self._classic_hid_channels_open(hid_host)
            and self._classic_channels_opened_at is None
        ):
            self._classic_channels_opened_at = time.monotonic()
            self._classic_channel_origin = self._classic_channel_origin or "host"

        self._classic_set_report_protocol()
        await self._classic_disable_low_power(connection)
        if not await self._handle_classic_connection(create_uhid=False):
            return
        self._classic_hid_ready_at = time.monotonic()
        if self._classic_defer_uhid_until_input(addr_str):
            self._park_classic_session_until_input()
            return
        self._finalize_classic_hid()
        self._record_current_session(Protocol.CLASSIC)
        log.success(
            f"[Classic] Session ready: {self._format_device(addr_str)} "
            f"(waited {self._connection_wait_elapsed():.2f}s)"
        )

    def _is_classic_allowed(self, addr_str: str) -> bool:
        """Check if Classic address is allowed."""
        norm_addr = normalize_addr(addr_str)

        for dev in self.classic_devices:
            if dev.address == '*':
                return True
            if dev.address == norm_addr:
                return True

        if norm_addr in self._keystore_addresses:
            return True

        return False

    def _has_live_classic_connection(self, addr: str) -> bool:
        norm = normalize_addr(addr)
        connections = getattr(self.device, 'connections', None) or {}
        for conn in list(connections.values()):
            try:
                if normalize_addr(str(conn.peer_address)) != norm:
                    continue
                transport = getattr(conn, 'transport', None)
                if transport is not None and transport != BT_BR_EDR_TRANSPORT:
                    continue
                if self._is_raw_connection_alive(conn):
                    return True
            except Exception:
                continue
        return False

    async def _classic_active_connect_loop(self, addresses: List[str]):
        """Actively try to connect to Classic devices."""
        current_task = asyncio.current_task()
        existing = getattr(self, "_classic_active_connect_task", None)
        if existing and existing is not current_task and not existing.done():
            log.info("[Classic] Active connect loop already running")
            return
        self._classic_active_connect_task = current_task
        log.info(f"[Classic] Active: {len(addresses)} device(s)")

        try:
            await asyncio.sleep(self.ACTIVE_DELAY)

            attempt = 0
            waiting_for_ble = False
            while not self._is_protocol_connected(Protocol.CLASSIC):
                retry_delay = self._protocol_retry_delay(Protocol.CLASSIC)
                if retry_delay > 0:
                    await asyncio.sleep(min(retry_delay, 1.0))
                    continue

                if self._is_protocol_connecting(Protocol.CLASSIC):
                    await asyncio.sleep(0.5)
                    continue

                if getattr(self.device, 'le_connecting', False):
                    if not waiting_for_ble:
                        log.info(
                            "[Classic] Waiting for active BLE procedure to yield "
                            "the controller"
                        )
                        waiting_for_ble = True
                    await asyncio.sleep(0.5)
                    continue
                waiting_for_ble = False

                all_backoff_delay = self._classic_backoff_delay_for_all(addresses)
                if all_backoff_delay > 0:
                    await self._set_classic_page_scan(True)
                    wait_time = min(
                        all_backoff_delay,
                        self.CLASSIC_BACKOFF_POLL_INTERVAL,
                    )
                    log.debug(
                        "[Classic] All active devices are in flap backoff; "
                        f"passive Page Scan remains enabled; next dial in "
                        f"{all_backoff_delay:.0f}s"
                    )
                    await asyncio.sleep(wait_time)
                    continue

                await self._set_classic_page_scan(True)
                for addr in addresses:
                    if self._is_protocol_connected(Protocol.CLASSIC):
                        return
                    if self._is_protocol_connecting(Protocol.CLASSIC):
                        break

                    flap_delay = self._classic_dial_delay(addr)
                    if flap_delay > 0:
                        log.debug(
                            f"[Classic] {self._format_device(addr)} in flap "
                            f"backoff for {flap_delay:.0f}s; skipping dial"
                        )
                        continue

                    if self._has_live_classic_connection(addr):
                        log.info(
                            f"[Classic] {self._format_device(addr)} already linked; "
                            "skipping active connect"
                        )
                        await asyncio.sleep(1.0)
                        continue

                    attempt += 1
                    log.info(f"[Classic] Attempt {attempt}: {self._format_device(addr)}")

                    target = Address(addr, Address.PUBLIC_DEVICE_ADDRESS)
                    radio_started = await self._acquire_radio_lock(
                        f"Classic connect ({self._format_device(addr)})"
                    )
                    connect_task = asyncio.create_task(
                        self.device.connect(target, transport=BT_BR_EDR_TRANSPORT)
                    )
                    backoff = 0.0
                    try:
                        timed_out = True
                        for _ in range(self.ACTIVE_CONNECT_TIMEOUT):
                            if self._is_protocol_connected(Protocol.CLASSIC):
                                return
                            done, _ = await asyncio.wait([connect_task], timeout=0.5)
                            if done:
                                timed_out = False
                                break

                        if timed_out:
                            log.info(f"[Classic] {addr} timed out")
                            backoff = 3.0
                        else:
                            await connect_task

                    except Exception as e:
                        if "DISALLOWED" in str(e) or "PENDING" in str(e):
                            log.warning("[Classic] HCI busy, waiting...")
                            backoff = 5.0
                        else:
                            log.info(f"[Classic] Connect failed: {e}")
                            backoff = 2.0

                    finally:
                        if not connect_task.done():
                            connect_task.cancel()
                        try:
                            await connect_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        log.info(
                            f"[Radio] Classic connect ({self._format_device(addr)}): "
                            f"held lock for {time.monotonic() - radio_started:.2f}s"
                        )
                        self._radio_lock.release()

                    if backoff:
                        await asyncio.sleep(backoff)

                if not self._is_protocol_connected(Protocol.CLASSIC):
                    # Leave page scan on while idle so a keyboard that
                    # reconnects by itself is accepted without another dial.
                    await self._set_classic_page_scan(True)
                    await asyncio.sleep(
                        self._next_idle_probe_delay(Protocol.CLASSIC)
                    )
        finally:
            if getattr(self, "_classic_active_connect_task", None) is current_task:
                self._classic_active_connect_task = None

    def _classic_backoff_delay_for_all(self, addresses: List[str]) -> float:
        delays = []
        for addr in addresses:
            delay = self._classic_dial_delay(addr)
            if delay <= 0:
                return 0.0
            delays.append(delay)
        return min(delays) if delays else 0.0

    async def _set_classic_page_scan(self, enabled: bool):
        if self._classic_page_scan_enabled == enabled:
            return
        scan_enable = 0x02 if enabled else 0x00
        action = "Enabling" if enabled else "Disabling"
        log.info(f"[Classic] {action} Page Scan...")
        await self.device.host.send_command(
            HCI_Write_Scan_Enable_Command(scan_enable=scan_enable),
            check_result=True
        )
        self._classic_page_scan_enabled = enabled

    async def _classic_disable_low_power(self, connection):
        handle = getattr(connection, "handle", None)
        if handle is None:
            return
        try:
            await self.device.host.send_command(
                HCI_Write_Link_Policy_Settings_Command(
                    connection_handle=handle,
                    link_policy_settings=0,
                ),
                check_result=True,
            )
            log.info("[Classic] Disabled low-power link policy")
        except Exception as e:
            log.warning(f"[Classic] Link policy update failed: {e}")
        try:
            await self.device.host.send_command(
                HCI_Exit_Sniff_Mode_Command(connection_handle=handle),
                check_result=True,
            )
            log.info("[Classic] Requested exit from sniff mode")
        except Exception as e:
            log.debug(f"[Classic] Exit sniff ignored: {e}")

    def _classic_channel_open(self, channel):
        if not channel:
            return False
        state = getattr(channel, "state", None)
        state_enum = getattr(channel, "State", None)
        if state is None or state_enum is None:
            return True
        return state == state_enum.OPEN

    def _classic_channel_closed(self, channel):
        if not channel:
            return False
        state = getattr(channel, "state", None)
        state_enum = getattr(channel, "State", None)
        if state is None or state_enum is None:
            return False
        closed_states = {
            getattr(state_enum, "CLOSED", None),
            getattr(state_enum, "WAIT_DISCONNECT", None),
        }
        return state in closed_states

    def _classic_clear_closed_l2cap_channels(self, hid_host):
        if self._classic_channel_closed(hid_host.l2cap_ctrl_channel):
            hid_host.l2cap_ctrl_channel = None
        if self._classic_channel_closed(hid_host.l2cap_intr_channel):
            hid_host.l2cap_intr_channel = None

    def _classic_hid_channels_open(self, hid_host):
        return (
            self._classic_channel_open(hid_host.l2cap_intr_channel)
            and self._classic_channel_open(hid_host.l2cap_ctrl_channel)
        )

    def _classic_set_report_protocol(self):
        """Send HIDP SET_PROTOCOL(Report) on the control channel."""
        if not self.hid_host or not self._classic_channel_open(self.hid_host.l2cap_ctrl_channel):
            self._classic_set_protocol_ok = False
            self._classic_set_protocol_error = "control channel unavailable"
            return
        try:
            self.hid_host.set_protocol(Message.ProtocolMode.REPORT_PROTOCOL)
            self._classic_set_protocol_ok = True
            self._classic_set_protocol_error = None
            log.info("[Classic] Sent SET_PROTOCOL (Report)")
        except Exception as e:
            error = str(e)
            self._classic_set_protocol_ok = False
            self._classic_set_protocol_error = error
            log.warning(f"[Classic] SET_PROTOCOL failed: {error}")

    def _finalize_classic_hid(self):
        """Apply fallback descriptor if needed and create UHID."""
        if not self.report_map:
            self.report_map = FALLBACK_HID_DESCRIPTOR
            log.warning("[Classic] Using fallback descriptor")
        self._create_uhid_device()

    async def _handle_classic_connection(self, create_uhid=True):
        """Prepare Classic HID metadata and optionally create UHID."""
        if not self.hid_host.l2cap_intr_channel:
            raise InvalidStateError("HID interrupt channel not connected")

        if config.classic_require_live_descriptor:
            self.report_map = None
            live = await self._query_classic_sdp()
            if not live and self._load_cached_descriptor():
                log.warning("[Classic] Live SDP descriptor unavailable; using cached descriptor")
            elif not live:
                log.warning("[Classic] No live HID descriptor; dropping link")
                try:
                    await self.connection.disconnect()
                except Exception:
                    pass
                return False
        elif not self._load_cached_descriptor():
            await self._query_classic_sdp()

        if create_uhid:
            self._finalize_classic_hid()
        return True

    def _parse_hid_descriptor_list(self, data_element):
        """Parse HID Descriptor List from SDP."""
        try:
            if hasattr(data_element, 'value'):
                data_element = data_element.value

            if isinstance(data_element, (list, tuple)):
                for descriptor in data_element:
                    if hasattr(descriptor, 'value'):
                        descriptor = descriptor.value

                    if isinstance(descriptor, (list, tuple)) and len(descriptor) >= 2:
                        desc_type = descriptor[0]
                        desc_data = descriptor[1]

                        if hasattr(desc_type, 'value'):
                            desc_type = desc_type.value

                        if desc_type == 0x22:  # Report Descriptor
                            if hasattr(desc_data, 'value'):
                                desc_data = desc_data.value

                            if isinstance(desc_data, bytes):
                                self.report_map = desc_data
                            elif isinstance(desc_data, (list, tuple)):
                                self.report_map = bytes(desc_data)

                            log.success(f"[Classic] Got descriptor: {len(self.report_map)} bytes")
                            return
        except Exception as e:
            log.warning(f"[Classic] Failed to parse descriptor: {e}")

    def _on_classic_interrupt_data(self, pdu: bytes):
        """Handle Classic HID report."""
        if len(pdu) < 1:
            return
        if (pdu[0] >> 4) != Message.MessageType.DATA or \
                (pdu[0] & 0x0F) != Message.ReportType.INPUT_REPORT:
            log.debug(f"[Classic] Ignoring non-input interrupt PDU: 0x{pdu[0]:02X}")
            return
        if Protocol.CLASSIC not in self.sessions and self._classic_pending_session:
            if not self._promote_classic_pending_session():
                return
        self._forward_report_for_protocol(Protocol.CLASSIC, pdu[1:])

    def _on_virtual_cable_unplug(self):
        """Handle virtual cable unplug."""
        log.warning("[Classic] Virtual cable unplugged")
        session = self.sessions.get(Protocol.CLASSIC)
        self._virtual_cable_unplug_address = (
            session.address if session else self.current_device_address
        )
        address = self._virtual_cable_unplug_address
        if address:
            config.remove_device(address)
            addr_norm = normalize_addr(address)
            self.classic_devices = [
                dev for dev in self.classic_devices
                if normalize_addr(dev.address) != addr_norm
            ]
        self._on_protocol_disconnection(
            Protocol.CLASSIC, address, "virtual-cable-unplug")

    async def _pair_classic(self, address: str) -> bool:
        """Pair with a Classic Bluetooth device."""
        log.info(f"[Classic] Pairing with {address}...")

        try:
            target_address = Address(address, Address.PUBLIC_DEVICE_ADDRESS)
            self.connection = await self.device.connect(
                target_address,
                transport=BT_BR_EDR_TRANSPORT,
                timeout=config.connect_timeout,
            )
            log.success(f"[Classic] Connected to {address}")
        except (asyncio.TimeoutError, BumbleTimeoutError):
            log.error(f"[Classic] Connection timeout after {config.connect_timeout}s")
            return False
        except Exception as e:
            log.error(f"[Classic] Connection failed: {e}")
            return False

        self.current_device_address = address
        self.connected_protocol = Protocol.CLASSIC
        self.report_map = None
        self.device_name = None

        link_key_received = asyncio.Event()

        def on_device_link_key(_bd_addr, link_key, key_type):
            log.success(f"[Classic] Link key received: type={key_type}")
            link_key_received.set()

        self.device.host.on('link_key', on_device_link_key)

        try:
            log.info("[Classic] Authenticating...")
            try:
                await asyncio.wait_for(self.connection.authenticate(), timeout=30.0)
                log.success("[Classic] Authentication complete")
            except Exception as e:
                log.warning(f"[Classic] Authentication: {e!r}")

            log.info("[Classic] Waiting for link key...")
            try:
                await asyncio.wait_for(link_key_received.wait(), timeout=5.0)
                log.success("[Classic] Link key saved")
            except asyncio.TimeoutError:
                log.warning("[Classic] Link key event timeout (may already be saved)")

            if not self.connection.is_encrypted:
                log.info("[Classic] Requesting encryption...")
                try:
                    await asyncio.wait_for(
                        self.connection.encrypt(enable=True),
                        timeout=10.0
                    )
                except Exception as e:
                    log.warning(f"[Classic] Encryption: {e!r}")

            await self._query_classic_sdp(address)

            if not self.report_map:
                log.warning(
                    "[Classic] No HID descriptor in SDP; "
                    "using fallback keyboard descriptor"
                )

            if self.keystore:
                keys = await self.keystore.get(address)
                if keys and keys.link_key:
                    log.success("[Classic] Link key verified")
                else:
                    log.warning("[Classic] Link key not found in keystore!")

            self.device.host.remove_listener('link_key', on_device_link_key)
            return True

        except Exception as e:
            log.error(f"[Classic] Pairing failed: {e}")
            self.device.host.remove_listener('link_key', on_device_link_key)
            if self.connection:
                try:
                    await self.connection.disconnect()
                except Exception:
                    pass
                self.connection = None
            return False

    async def _query_classic_sdp(self, address: str = None):
        """Query SDP for HID descriptor and cache it."""
        if not self.connection:
            return None

        address = address or self.current_device_address

        log.info("[Classic] Querying SDP...")
        try:
            sdp_client = SDPClient(self.connection)
            await asyncio.wait_for(sdp_client.connect(), timeout=5.0)

            try:
                result = await asyncio.wait_for(
                    sdp_client.search_attributes(
                        [BT_HUMAN_INTERFACE_DEVICE_SERVICE],
                        [0x0100, 0x0206]
                    ),
                    timeout=10.0
                )
            finally:
                try:
                    await sdp_client.disconnect()
                except Exception:
                    pass

            if result:
                for record in result:
                    for attr in record:
                        if hasattr(attr, 'id') and attr.id == 0x0206:
                            self._parse_hid_descriptor_list(attr.value)
                        elif hasattr(attr, 'id') and attr.id == 0x0100:
                            try:
                                name = attr.value.value
                                if isinstance(name, bytes):
                                    name = name.decode('utf-8', errors='replace')
                                self.device_name = str(name)
                            except Exception:
                                pass

            if self.report_map:
                self.device_cache.save(address, {
                    'report_map': self.report_map.hex(),
                    'device_name': self.device_name or 'Unknown'
                })
                log.success(f"[Classic] Cached descriptor ({len(self.report_map)} bytes)")
                return True
            return False
        except Exception as e:
            log.warning(f"[Classic] SDP query failed: {e!r}")
            return None

    async def _continue_classic_after_pairing(self):
        """Continue Classic connection after pairing."""
        self.hid_host = BumbleHIDHost(self.device)
        self.hid_host.on(BumbleHIDHost.EVENT_INTERRUPT_DATA, self._on_classic_interrupt_data)
        self.hid_host.on(BumbleHIDHost.EVENT_VIRTUAL_CABLE_UNPLUG, self._on_virtual_cable_unplug)
        log.info("[Classic] HID Host created")

        self.hid_host.on_device_connection(self.connection)

        log.info("[Classic] Connecting to HID control channel...")
        try:
            await asyncio.wait_for(self.hid_host.connect_control_channel(), timeout=5.0)
            log.success("[Classic] HID control channel connected")
        except Exception as e:
            log.warning(f"[Classic] Control channel: {e!r}")

        log.info("[Classic] Connecting to HID interrupt channel...")
        try:
            await asyncio.wait_for(self.hid_host.connect_interrupt_channel(), timeout=5.0)
            log.success("[Classic] HID interrupt channel connected")
        except Exception as e:
            log.warning(f"[Classic] Interrupt channel: {e!r}")

        if not self.hid_host.l2cap_intr_channel:
            log.error("[Classic] Failed to connect HID interrupt channel")
            return

        self._classic_set_report_protocol()
        self._finalize_classic_hid()
