#!/usr/bin/env python3
"""BLE HID handler mixin for HIDHost."""

import asyncio

from bumble.core import AdvertisingData, BT_LE_TRANSPORT, InvalidStateError
from bumble.device import Device, Peer
from bumble.gatt import (
    GATT_DEVICE_NAME_CHARACTERISTIC,
    GATT_GENERIC_ACCESS_SERVICE,
    GATT_HID_CONTROL_POINT_CHARACTERISTIC,
    GATT_HUMAN_INTERFACE_DEVICE_SERVICE,
    GATT_PROTOCOL_MODE_CHARACTERISTIC,
    GATT_REPORT_CHARACTERISTIC,
    GATT_REPORT_MAP_CHARACTERISTIC,
    GATT_REPORT_REFERENCE_DESCRIPTOR,
)
from bumble.hci import (
    Address,
    HCI_LE_Add_Device_To_Filter_Accept_List_Command,
    HCI_LE_Clear_Filter_Accept_List_Command,
    HCI_LE_Create_Connection_Cancel_Command,
    HCI_LE_Create_Connection_Command,
    OwnAddressType,
)

from config import Protocol, config, normalize_addr
from logging_utils import log

HID_REPORT_TYPE_INPUT = 1


class BLEMixin:
    """BLE methods for HIDHost."""

    BLE_INIT_WINDOW = 18.0
    BLE_SCAN_WINDOW = 8.0
    BLE_COEXIST_WINDOW = 1.0
    BLE_CLASSIC_IDLE_WINDOW = 12.0
    BLE_CLASSIC_IDLE_RETRY_DELAY = 2.0
    BLE_COEXIST_RETRY_DELAY = 60.0

    async def _run_ble_handler(self):
        """Handle BLE connections."""
        known_addresses = [dev.address for dev in self.ble_devices if dev.address != '*']
        has_wildcard = any(dev.address == '*' for dev in self.ble_devices)

        if known_addresses:
            await self._run_ble_accept_list_handler(known_addresses)
        elif has_wildcard:
            await self._run_ble_scan_handler(set())

    async def _run_ble_accept_list_handler(self, addresses: list):
        """Wait for BLE connections using the filter accept list."""
        await self.device.send_command(
            HCI_LE_Clear_Filter_Accept_List_Command(), check_result=True)

        known = {normalize_addr(a) for a in addresses}

        for addr_str in sorted(known):
            target = Address(addr_str)
            known_type = self._keystore_address_types.get(addr_str)
            if known_type is not None:
                entry_types = [known_type & 1]
            else:
                entry_types = [Address.PUBLIC_DEVICE_ADDRESS, Address.RANDOM_DEVICE_ADDRESS]
            for entry_type in entry_types:
                await self.device.send_command(
                    HCI_LE_Add_Device_To_Filter_Accept_List_Command(
                        address_type=entry_type,
                        address=target,
                    ), check_result=True)

        log.info(f"[BLE] Waiting for {len(addresses)} device(s) (accept list)")

        try:
            connection = None

            while connection is None and not self._is_protocol_connected(Protocol.BLE):
                matched_dev = None
                match_kind = None

                connection = await self._ble_initiate(self.BLE_INIT_WINDOW)
                if connection is not None:
                    if await self._reject_unconfigured_ble_connection(
                        connection, matched_dev
                    ):
                        connection = None
                        await self._ble_coexist_pause()
                        continue
                    break

                match = await self._ble_scan_for_rotated(known, self.BLE_SCAN_WINDOW)
                if match:
                    target_address, matched_dev, match_kind = match
                    connection = await self._ble_initiate(
                        config.connect_timeout, peer=target_address)
                    if (
                        connection is not None
                        and await self._reject_unconfigured_ble_connection(
                            connection, matched_dev
                        )
                    ):
                        connection = None
                        await self._ble_coexist_pause()
                        continue
                if connection is None:
                    await self._ble_coexist_pause()
                    # A BLE central must initiate, but an absent keyboard is
                    # not a fault.  Back off on the live host instead of
                    # churning the shared MTK controller and page scan.
                    await asyncio.sleep(
                        self._next_idle_probe_delay(Protocol.BLE)
                    )

            if connection is None:
                return

            if self._is_protocol_connected(Protocol.BLE):
                await connection.disconnect()
                return

            addr_str = str(connection.peer_address)
            log.info(f"[BLE] Device connected: {self._format_device(addr_str)}")

            async with self._session_setup_lock:
                if self._is_protocol_connected(Protocol.BLE):
                    await connection.disconnect()
                    return

                self._clear_protocol_event(Protocol.BLE)
                self.connection = connection
                self.peer = Peer(connection)
                self.hid_host = None
                self.current_device_address = addr_str
                self.device_name = self._configured_name(addr_str)
                self.report_map = None
                self.hid_reports = []
                self.uhid_device = None
                self._uhid_created_at = None
                self._last_report = None
                # Cleared per fresh connection so a bond-restore failure reads
                # only this session's disconnect reason.
                self._last_ble_disconnect_reason = None
                self.connected_protocol = Protocol.BLE
                self._reset_idle_probe_backoff(Protocol.BLE)
                connection.on(
                    'disconnection',
                    lambda reason, p=Protocol.BLE, a=addr_str:
                    self._on_protocol_disconnection(p, a, reason)
                )

                await self._ble_restore_or_pair()
                await self._handle_ble_connection()

                if match_kind == 'name' and matched_dev is not None:
                    self._save_rotated_address(matched_dev, normalize_addr(addr_str))

                self._record_current_session(Protocol.BLE)
                log.success(
                    f"[BLE] Session ready: {self._format_device(addr_str)} "
                    f"(waited {self._connection_wait_elapsed():.2f}s)"
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"[BLE] Connection failed: {e}")
            try:
                if connection is not None:
                    await connection.disconnect()
            except Exception:
                pass
            if (self.connection is connection
                    and self.connected_protocol == Protocol.BLE
                    and Protocol.BLE not in self.sessions):
                self.connection = None
                self.peer = None
                self.current_device_address = None
                self.connected_protocol = None

    async def _reject_unconfigured_ble_connection(self, connection, matched_dev) -> bool:
        if matched_dev is not None:
            return False
        addr_str = str(connection.peer_address)
        addr_norm = normalize_addr(addr_str)
        configured = {
            normalize_addr(dev.address)
            for dev in self.ble_devices
            if dev.address != '*'
        }
        if addr_norm in configured or any(dev.address == '*' for dev in self.ble_devices):
            return False
        log.warning(f"[BLE] Rejecting {addr_str} (not configured for BLE)")
        try:
            await connection.disconnect()
        except Exception:
            pass
        return True

    def _ble_should_yield_to_classic(self) -> bool:
        return bool(
            self.classic_devices
            and not self._is_protocol_connected(Protocol.CLASSIC)
        )

    def _ble_has_classic_setup_activity(self) -> bool:
        return bool(
            self._is_protocol_connecting(Protocol.CLASSIC)
            and not self._is_classic_parked()
        )

    def _ble_window_for_radio_state(self, window: float) -> float:
        if self._ble_has_classic_setup_activity():
            return min(window, self.BLE_COEXIST_WINDOW)
        if self._ble_should_yield_to_classic():
            return min(window, self.BLE_CLASSIC_IDLE_WINDOW)
        return window

    def _ble_coexist_pause_delay(self) -> float:
        if self._ble_has_classic_setup_activity():
            return self.BLE_COEXIST_RETRY_DELAY
        if self._ble_should_yield_to_classic():
            return self.BLE_CLASSIC_IDLE_RETRY_DELAY
        return 0.0

    def _ble_should_pause_classic_page_scan(self) -> bool:
        return bool(
            self.classic_devices
            and not self._is_protocol_connected(Protocol.CLASSIC)
            and not self._is_protocol_connecting(Protocol.CLASSIC)
            and not self._is_classic_parked()
        )

    async def _ble_coexist_pause(self):
        delay = self._ble_coexist_pause_delay()
        if delay <= 0:
            return
        log.info(f"[BLE] Yielding radio to Classic for {delay:.1f}s")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))
            updated_delay = self._ble_coexist_pause_delay()
            if updated_delay <= 0:
                return
            if updated_delay < remaining:
                deadline = loop.time() + updated_delay

    async def _ble_initiate(self, window: float, peer: Address = None):
        """Legacy create-connection to `peer`, or to the accept list when
        None. Returns the connection, or None on window timeout."""
        window = self._ble_window_for_radio_state(window)

        mode = f"peer {peer}" if peer is not None else "accept-list"
        loop = asyncio.get_running_loop()
        started = loop.time()
        log.info(
            f"[BLE] Initiate window start: mode={mode}, "
            f"window={window:.1f}s, live={self._live_protocols()}"
        )
        pending = loop.create_future()
        initiated = False

        def on_connection(connection):
            if connection.transport == BT_LE_TRANSPORT and not pending.done():
                pending.set_result(connection)

        def on_failure(error):
            if getattr(error, 'transport', BT_LE_TRANSPORT) != BT_LE_TRANSPORT:
                return
            if not pending.done():
                pending.set_exception(error)

        def consume_exception(future):
            if not future.cancelled():
                future.exception()
        pending.add_done_callback(consume_exception)

        radio_started = await self._acquire_radio_lock(f"BLE initiate ({mode})")
        self.device.on(Device.EVENT_CONNECTION, on_connection)
        self.device.on(Device.EVENT_CONNECTION_FAILURE, on_failure)
        classic_page_scan_paused = False

        try:
            if self._ble_has_classic_setup_activity():
                log.info("[BLE] Initiate window skipped: Classic setup active")
                return None

            if self._ble_should_pause_classic_page_scan():
                await self._set_classic_page_scan(False)
                classic_page_scan_paused = True

            self.device.connect_own_address_type = OwnAddressType.PUBLIC
            self.device.le_connecting = True

            try:
                await self.device.send_command(
                    HCI_LE_Create_Connection_Command(
                        le_scan_interval=96,
                        le_scan_window=96,
                        initiator_filter_policy=0 if peer is not None else 1,
                        peer_address_type=peer.address_type if peer is not None else 0,
                        peer_address=peer if peer is not None else Address.ANY,
                        own_address_type=OwnAddressType.PUBLIC,
                        connection_interval_min=12,
                        connection_interval_max=24,
                        max_latency=0,
                        supervision_timeout=72,
                        min_ce_length=0,
                        max_ce_length=0,
                    ), check_result=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info(f"[BLE] Initiate command failed: {e}")
                return None
            initiated = True

            deadline = started + window
            while True:
                if self._ble_has_classic_setup_activity():
                    elapsed = loop.time() - started
                    log.info(
                        "[BLE] Initiate window aborted for Classic setup: "
                        f"mode={mode}, elapsed={elapsed:.2f}s"
                    )
                    return None

                remaining = deadline - loop.time()
                if remaining <= 0:
                    elapsed = loop.time() - started
                    log.info(
                        f"[BLE] Initiate window timeout: mode={mode}, "
                        f"elapsed={elapsed:.2f}s"
                    )
                    return None

                try:
                    connection = await asyncio.wait_for(
                        asyncio.shield(pending),
                        timeout=min(0.1, remaining),
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    elapsed = loop.time() - started
                    log.info(
                        f"[BLE] Initiate window failed: mode={mode}, "
                        f"elapsed={elapsed:.2f}s, error={e}"
                    )
                    return None
                elapsed = loop.time() - started
                log.info(
                    f"[BLE] Initiate window connected: "
                    f"peer={connection.peer_address}, elapsed={elapsed:.2f}s"
                )
                return connection

        finally:
            if initiated and not pending.done():
                try:
                    await self.device.send_command(
                        HCI_LE_Create_Connection_Cancel_Command())
                    await asyncio.wait_for(asyncio.shield(pending), timeout=1.0)
                except Exception:
                    pass
            self.device.le_connecting = False
            self.device.remove_listener(Device.EVENT_CONNECTION, on_connection)
            self.device.remove_listener(Device.EVENT_CONNECTION_FAILURE, on_failure)
            if classic_page_scan_paused:
                try:
                    await self._set_classic_page_scan(True)
                except Exception as e:
                    log.warning(f"[BLE] Could not restore Classic Page Scan: {e}")
            log.info(
                f"[Radio] BLE initiate ({mode}): held lock for "
                f"{asyncio.get_running_loop().time() - radio_started:.2f}s"
            )
            self._radio_lock.release()

    async def _ble_scan_for_rotated(self, known: set, window: float):
        """Scan for bonded devices advertising, including from a rotated
        address. Returns (address, DeviceConfig or None, kind) or None."""
        window = self._ble_window_for_radio_state(window)

        loop = asyncio.get_running_loop()
        started = loop.time()
        rotated = loop.create_future()

        def on_advertisement(advertisement):
            if rotated.done():
                return
            match = self._match_rotated_ble_device(advertisement, known)
            if match:
                rotated.set_result((advertisement.address,) + match)

        radio_started = await self._acquire_radio_lock("BLE rotation scan")
        self.device.on('advertisement', on_advertisement)
        scanning = False
        try:
            await self.device.start_scanning(
                legacy=True,
                own_address_type=OwnAddressType.PUBLIC,
                filter_duplicates=True,
            )
            scanning = True
            deadline = started + window
            while True:
                if self._ble_has_classic_setup_activity():
                    log.info("[BLE] Rotation scan aborted for Classic setup")
                    return None
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(rotated),
                        timeout=min(0.1, remaining),
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"[BLE] Rotation scan failed: {e}")
            return None
        finally:
            self.device.remove_listener('advertisement', on_advertisement)
            if scanning:
                try:
                    await self.device.stop_scanning(legacy=True)
                except Exception:
                    pass
            log.info(
                "[Radio] BLE rotation scan: held lock for "
                f"{asyncio.get_running_loop().time() - radio_started:.2f}s"
            )
            self._radio_lock.release()

    def _match_rotated_ble_device(self, advertisement, known: set):
        """Match an advertisement by known address, IRK resolution, or
        device name. Returns (DeviceConfig or None, kind) or None."""
        address = advertisement.address
        addr_norm = normalize_addr(str(address))
        if addr_norm in known:
            dev = next((d for d in self.ble_devices if d.address == addr_norm), None)
            return (dev, 'known')

        if address.is_resolvable and self.device.address_resolver:
            resolved = self.device.address_resolver.resolve(address)
            if resolved:
                resolved_norm = normalize_addr(str(resolved))
                if resolved_norm in known:
                    dev = next((d for d in self.ble_devices if d.address == resolved_norm), None)
                    log.info(f"[BLE] Resolved {addr_norm} to bonded device "
                             f"{self._format_device(resolved_norm)}")
                    return (dev, 'irk')

        try:
            name = advertisement.data.get(AdvertisingData.COMPLETE_LOCAL_NAME) or \
                advertisement.data.get(AdvertisingData.SHORTENED_LOCAL_NAME)
        except UnicodeDecodeError as e:
            log.debug(f"[BLE] Ignoring malformed advertisement from {addr_norm}: {e}")
            return None
        if isinstance(name, bytes):
            name = name.decode('utf-8', errors='replace')
        if name:
            dev = next((d for d in self.ble_devices if d.name == name), None)
            if dev:
                log.info(f"[BLE] {name} advertising from new address {addr_norm}")
                return (dev, 'name')

        return None

    def _save_rotated_address(self, dev, new_addr: str):
        """Save the new address and drop the device's stale entries."""
        config.add_device(new_addr, Protocol.BLE, dev.name)
        for old in self.ble_devices:
            if old.name == dev.name and old.address != new_addr:
                config.remove_device(old.address)

    async def _run_ble_scan_handler(self, target_addresses: set):
        """Fallback BLE handler using active scanning for discovery."""
        log.info("[BLE] Scanning for devices...")

        while not self._is_protocol_connected(Protocol.BLE):
            found_device = None

            def on_advertisement(advertisement):
                nonlocal found_device
                if self._is_protocol_connected(Protocol.BLE):
                    return

                addr = normalize_addr(str(advertisement.address))
                if not target_addresses or addr in target_addresses:
                    found_device = advertisement
                    log.info(f"[BLE] Found target: {addr}")

            self.device.on('advertisement', on_advertisement)

            try:
                async with self._radio_lock:
                    await self.device.start_scanning()
                    for _ in range(20):
                        if found_device or self._is_protocol_connected(Protocol.BLE):
                            break
                        await asyncio.sleep(0.5)
                    await self.device.stop_scanning()
            except Exception as e:
                log.warning(f"[BLE] Scan error: {e}")
            finally:
                self.device.remove_listener('advertisement', on_advertisement)

            if self._is_protocol_connected(Protocol.BLE):
                return

            if found_device:
                max_attempts = 2
                for attempt in range(1, max_attempts + 1):
                    try:
                        log.info(f"[BLE] Connecting to {found_device.address} (Attempt {attempt}/{max_attempts})...")
                        async with self._radio_lock:
                            connection = await self.device.connect(
                                found_device.address,
                                own_address_type=OwnAddressType.PUBLIC,
                                timeout=config.connect_timeout,
                            )

                        if self._is_protocol_connected(Protocol.BLE):
                            await connection.disconnect()
                            return

                        async with self._session_setup_lock:
                            if self._is_protocol_connected(Protocol.BLE):
                                await connection.disconnect()
                                return

                            self._clear_protocol_event(Protocol.BLE)
                            self.connection = connection
                            self.peer = Peer(connection)
                            self.hid_host = None
                            self.current_device_address = str(found_device.address)
                            self.device_name = self._configured_name(self.current_device_address)
                            self.report_map = None
                            self.hid_reports = []
                            self.uhid_device = None
                            self._uhid_created_at = None
                            self._last_report = None
                            # Cleared per fresh connection so a bond-restore
                            # failure reads only this session's disconnect reason.
                            self._last_ble_disconnect_reason = None
                            self.connected_protocol = Protocol.BLE
                            connection.on(
                                'disconnection',
                                lambda reason, p=Protocol.BLE, a=self.current_device_address:
                                self._on_protocol_disconnection(p, a, reason)
                            )

                            await self._ble_restore_or_pair()
                            await self._handle_ble_connection()
                            self._record_current_session(Protocol.BLE)
                            log.success(f"[BLE] Session ready: {self._format_device(self.current_device_address)}")

                        return

                    except Exception as e:
                        log.warning(f"[BLE] Connect attempt {attempt} failed: {e}")
                        if attempt < max_attempts:
                            await asyncio.sleep(2.0)

            if not self._is_protocol_connected(Protocol.BLE):
                await asyncio.sleep(3.0)

    def _ble_bond_identity_address(self, connection) -> str:
        """Resolve a connection's peer address to its bonded identity address
        (e.g. an RPA resolved via IRK), matching the resolution
        `_match_rotated_ble_device` already does for advertisements. The
        keystore (and our per-address bond-forget state) is keyed by identity
        address, so forgetting/counting against the raw peer address can
        silently miss when the peer connected via a rotating RPA. Falls back
        to the raw peer address when resolution isn't available."""
        peer_address = connection.peer_address
        resolver = getattr(self.device, 'address_resolver', None)
        if getattr(peer_address, 'is_resolvable', False) and resolver:
            resolved = resolver.resolve(peer_address)
            if resolved:
                return str(resolved)
        return str(peer_address)

    async def _ble_restore_or_pair(self):
        """Restore BLE bonding or initiate new pairing."""
        identity_address = self._ble_bond_identity_address(self.connection)

        if self.connection.is_encrypted:
            log.info("[BLE] Connection already encrypted")
            self._ble_bond_3e_fail_counts.pop(identity_address, None)
            return

        if self.device.keystore:
            # Look up, count, and forget under one key: the resolved identity
            # address. If the peer connected via an RPA but the bond is stored
            # under its identity address, keying get() off the raw peer address
            # would miss the bond and force a needless fresh pair every
            # reconnect.
            try:
                keys = await self.device.keystore.get(identity_address)
                if keys:
                    log.info("[BLE] Restoring bonding...")
                    await self._wait_for_ble_operation(
                        self.connection.encrypt(), "bonding restore")
                    log.success("[BLE] Bonding restored")
                    self._ble_bond_3e_fail_counts.pop(identity_address, None)
                    return
            except Exception as e:
                log.warning(f"[BLE] Bonding restore failed: {e}")
                if (
                    not self._is_raw_connection_alive(self.connection)
                    or self._protocol_event_is_set(Protocol.BLE)
                ):
                    # If the peer dropped the link because it rejected our stored
                    # key (it was re-paired to another host and forgot us), the
                    # bond will fail identically on every future restore and
                    # deadlock reconnection. Forget it so the next window pairs
                    # fresh. Keep the bond on transient drops (e.g. supervision
                    # timeout) so a good bond survives a momentary glitch.
                    reason = self._last_ble_disconnect_reason
                    if reason in self.BLE_BOND_REJECT_REASONS:
                        await self._forget_ble_bond(identity_address)
                        self._ble_bond_3e_fail_counts.pop(identity_address, None)
                    elif reason == 0x3E:
                        count = self._ble_bond_3e_fail_counts.get(identity_address, 0) + 1
                        self._ble_bond_3e_fail_counts[identity_address] = count
                        if count >= self.BLE_BOND_3E_FORGET_THRESHOLD:
                            log.warning(
                                f"[BLE] {count} consecutive 0x3E restore failures "
                                f"for {identity_address}; treating stored key as "
                                "rejected"
                            )
                            await self._forget_ble_bond(identity_address)
                            self._ble_bond_3e_fail_counts.pop(identity_address, None)
                    raise InvalidStateError("[BLE] Disconnected during bonding restore")

        log.info("[BLE] Initiating pairing...")
        await self._wait_for_ble_operation(self.connection.pair(), "pairing")
        log.success("[BLE] Pairing complete")
        self._ble_bond_3e_fail_counts.pop(identity_address, None)

    async def _forget_ble_bond(self, address: str):
        """Delete a stale BLE bond so the next connect re-pairs from scratch."""
        if not self.device.keystore:
            return
        try:
            await self.device.keystore.delete(address)
            log.warning(
                f"[BLE] Removed stale bond for {address} "
                "(peer rejected our key); will re-pair on reconnect"
            )
        except KeyError:
            pass
        except Exception as e:
            log.warning(f"[BLE] Failed to remove stale bond for {address}: {e}")

    async def _wait_for_ble_operation(self, awaitable, operation: str,
                                      timeout: float = 20.0):
        op_task = asyncio.ensure_future(awaitable)
        disconnect_task = None
        timeout_task = None
        try:
            wait_tasks = {op_task}
            disconnect_event = self._protocol_disconnection_events.get(
                Protocol.BLE) or self._disconnection_event
            if disconnect_event:
                if disconnect_event.is_set():
                    op_task.cancel()
                    await asyncio.gather(op_task, return_exceptions=True)
                    raise InvalidStateError(f"[BLE] Disconnected during {operation}")
                disconnect_task = asyncio.create_task(disconnect_event.wait())
                wait_tasks.add(disconnect_task)

            timeout_task = asyncio.create_task(asyncio.sleep(timeout))
            wait_tasks.add(timeout_task)

            done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)

            if disconnect_task and disconnect_task in done:
                if not op_task.done():
                    op_task.cancel()
                    await asyncio.gather(op_task, return_exceptions=True)
                raise InvalidStateError(f"[BLE] Disconnected during {operation}")

            if timeout_task in done and not op_task.done():
                op_task.cancel()
                await asyncio.gather(op_task, return_exceptions=True)
                raise InvalidStateError(f"[BLE] {operation} timed out after {timeout:.0f}s")

            return await op_task
        finally:
            if disconnect_task and not disconnect_task.done():
                disconnect_task.cancel()
            if timeout_task and not timeout_task.done():
                timeout_task.cancel()

    async def _setup_ble_hid(self):
        """Discover reports, create UHID, subscribe. Common to connect and post-pair."""
        if not self.hid_reports:
            await self._discover_ble_hid_service(process_reports=True)
        if not self.report_map:
            raise InvalidStateError("[BLE] No report descriptor available")
        self._create_uhid_device()
        await self._subscribe_to_ble_reports()
        await self._ble_activate_hid_service()

    async def _handle_ble_connection(self):
        """Finalize BLE connection setup."""
        self._load_cached_descriptor()
        await self._setup_ble_hid()

    async def _read_ble_device_name(self):
        """Read BLE device name from Generic Access Service."""
        try:
            for service in self.peer.services:
                if service.uuid == GATT_GENERIC_ACCESS_SERVICE:
                    await self.peer.discover_characteristics(service=service)
                    for char in service.characteristics:
                        if char.uuid == GATT_DEVICE_NAME_CHARACTERISTIC:
                            value = await self.peer.read_value(char)
                            self.device_name = bytes(value).decode('utf-8', errors='replace')
                            log.info(f"[BLE] Device name: {self.device_name}")
                            return
        except Exception as e:
            log.warning(f"[BLE] Could not read device name: {e}")

    async def _process_ble_report_char(self, char):
        """Process a BLE Report characteristic."""
        await self.peer.discover_descriptors(characteristic=char)

        report_id = 0
        report_type = HID_REPORT_TYPE_INPUT

        for desc in char.descriptors:
            if desc.type == GATT_REPORT_REFERENCE_DESCRIPTOR:
                try:
                    ref = await self.peer.read_value(desc)
                    if len(ref) >= 2:
                        report_id = ref[0]
                        report_type = ref[1]
                except Exception:
                    pass

        if report_type == HID_REPORT_TYPE_INPUT:
            self.hid_reports.append((report_id, char))
            log.info(f"[BLE] Found input report {report_id}")

    async def _subscribe_to_ble_reports(self):
        """Subscribe to BLE HID input report notifications."""
        for report_id, char in self.hid_reports:
            try:
                def make_callback(rid):
                    return lambda value: self._on_ble_hid_report(value, rid)

                await self.peer.subscribe(char, make_callback(report_id))
                log.success(f"[BLE] Subscribed to report {report_id}")
            except Exception as e:
                log.warning(f"[BLE] Failed to subscribe to report {report_id}: {e}")

    async def _ble_activate_hid_service(self):
        """Write Exit Suspend to HID Control Point and force Report Protocol Mode."""
        if not self.peer:
            log.warning("[BLE] No peer for HID activation")
            return

        hid_services = [s for s in self.peer.services if s.uuid == GATT_HUMAN_INTERFACE_DEVICE_SERVICE]
        if not hid_services:
            log.warning("[BLE] No HID service found for activation")
            return

        hid_service = hid_services[0]
        if not hid_service.characteristics:
            log.info("[BLE] Discovering characteristics for HID activation...")
            await self.peer.discover_characteristics(service=hid_service)

        found_cp = False
        for char in hid_service.characteristics:
            if char.uuid == GATT_HID_CONTROL_POINT_CHARACTERISTIC:
                found_cp = True
                try:
                    await self.peer.write_value(char, bytes([0x01]), with_response=False)
                    log.info("[BLE] Wrote Exit Suspend to HID Control Point")
                except Exception as e:
                    log.warning(f"[BLE] Failed to write HID Control Point: {e}")

            elif char.uuid == GATT_PROTOCOL_MODE_CHARACTERISTIC:
                try:
                    value = await self.peer.read_value(char)
                    mode = "Report" if bytes(value) == b'\x01' else "Boot"
                    log.info(f"[BLE] Protocol Mode: {mode}")
                    if bytes(value) != b'\x01':
                        await self.peer.write_value(char, bytes([0x01]), with_response=False)
                        log.info("[BLE] Forced Report Protocol Mode")
                except Exception as e:
                    log.warning(f"[BLE] Protocol Mode read/write failed: {e}")

        if not found_cp:
            log.info(f"[BLE] No HID Control Point characteristic (found {len(hid_service.characteristics)} chars)")

    def _on_ble_hid_report(self, value, report_id):
        """Handle BLE HID report."""
        payload = bytes(value)
        data = bytes([report_id]) + payload if report_id else payload
        self._forward_report_for_protocol(Protocol.BLE, data)

    async def _pair_ble(self, address: str) -> bool:
        """Pair with a BLE device."""
        log.info(f"[BLE] Pairing with {address}...")

        target = Address(address)
        try:
            self.connection = await self.device.connect(
                target,
                own_address_type=OwnAddressType.PUBLIC,
                timeout=config.connect_timeout,
            )
        except Exception as e:
            log.error(f"[BLE] Connection failed: {e}")
            return False

        self.peer = Peer(self.connection)
        self.current_device_address = address
        self.connected_protocol = Protocol.BLE
        log.success(f"[BLE] Connected to {address}")

        try:
            log.info("[BLE] Initiating pairing...")
            await self.connection.pair()
            log.success("[BLE] Pairing complete!")

            if not self.connection.is_encrypted:
                raise InvalidStateError("[BLE] Link not encrypted after pairing")

            await self._discover_ble_hid_service()

            return True
        except Exception as e:
            log.error(f"[BLE] Pairing failed: {e}")
            if self.connection:
                try:
                    await self.connection.disconnect()
                except Exception:
                    pass
                self.connection = None
                self.peer = None
            return False

    async def _discover_ble_hid_service(self, process_reports: bool = False):
        """Discover BLE GATT HID service and cache descriptor."""
        await self.peer.discover_services()

        if not self.device_name:
            await self._read_ble_device_name()

        hid_services = [s for s in self.peer.services if s.uuid == GATT_HUMAN_INTERFACE_DEVICE_SERVICE]
        if not hid_services:
            if process_reports:
                raise InvalidStateError("[BLE] HID service not found")
            log.warning("[BLE] HID service not found")
            return

        hid_service = hid_services[0]
        log.success("[BLE] Found HID service")

        await self.peer.discover_characteristics(service=hid_service)

        for char in hid_service.characteristics:
            if char.uuid == GATT_REPORT_MAP_CHARACTERISTIC and not self.report_map:
                try:
                    value = await self.peer.read_value(char)
                    self.report_map = bytes(value)
                    log.success(f"[BLE] Got descriptor: {len(self.report_map)} bytes")

                    address = self.current_device_address
                    self.device_cache.save(address, {
                        'report_map': self.report_map.hex(),
                        'device_name': self.device_name
                    })
                except Exception as e:
                    log.warning(f"[BLE] Failed to read report map: {e}")

            elif process_reports and char.uuid == GATT_REPORT_CHARACTERISTIC:
                await self._process_ble_report_char(char)

    async def _continue_ble_after_pairing(self):
        """Continue BLE connection after pairing."""
        if not self.connection:
            log.info(f"[BLE] Reconnecting to {self.current_device_address}...")
            target = Address(self.current_device_address)
            self.connection = await self.device.connect(
                target,
                own_address_type=OwnAddressType.PUBLIC,
                timeout=config.connect_timeout,
            )
            self.peer = Peer(self.connection)
            self.connection.on('disconnection', self._on_disconnection)
            await self._ble_restore_or_pair()
        else:
            log.info("[BLE] Using existing connection from pairing")
            if not self.peer:
                self.peer = Peer(self.connection)
            await self._ble_restore_or_pair()

        await self._setup_ble_hid()
