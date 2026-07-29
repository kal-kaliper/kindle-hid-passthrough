#!/usr/bin/env python3
"""
Daemon Controller - Coordination layer between HTTP thread and async daemon.

Provides thread-safe access to daemon operations (scan, pair, connect,
disconnect) from the HTTP server thread via asyncio.run_coroutine_threadsafe().
"""

import asyncio
import logging
import os
import threading

from bt_setup import chip
from config import Protocol, config, normalize_addr
from device_cache import DeviceCache
from logging_utils import errstr

logger = logging.getLogger(__name__)

__all__ = ['DaemonController']

STATUS_CONNECTION_KEYS = (
    "address",
    "protocol",
    "name",
    "hid_ready",
    "uhid_name",
    "input_paths",
    "descriptor_size",
    "source_report_count",
    "uhid_report_count",
)

STATUS_DIAGNOSTIC_KEYS = (
    "last_source_report",
    "last_uhid_report",
    "recent_source_reports",
    "recent_uhid_reports",
)


def _status_connection_keys():
    if config.diagnostics_include_reports:
        return STATUS_CONNECTION_KEYS + STATUS_DIAGNOSTIC_KEYS
    return STATUS_CONNECTION_KEYS


class DaemonController:
    """Coordinates between the HTTP server thread and the async daemon.

    All request_* methods are called from the HTTP thread and schedule
    coroutines on the daemon's event loop via run_coroutine_threadsafe().
    """

    def __init__(self, daemon):
        self.daemon = daemon
        self.loop = None  # Set when event loop starts

        self._op_lock = asyncio.Lock()
        self._suspended_by_system = False

        # Scan state
        self.scan_result = None
        self.is_scanning = False
        self._scan_live_devices = []
        self._scan_stop_event = None

        # Pair state
        self.pair_result = None
        self.is_pairing = False

        # Device list cache (mtime-based)
        self._devices_cache = None
        self._devices_mtime = 0
        self._devices_lock = threading.Lock()

    def get_status(self) -> dict:
        """Thread-safe read of daemon state. Called from HTTP thread."""
        devices = self._get_devices_cached()

        status = {
            "daemon_running": self.daemon.running and not self.daemon._suspended,
            "devices": devices,
            "device_count": len(devices),
            "scanning": self.is_scanning,
            "pairing": self.is_pairing,
        }

        conn = self.daemon.connection_state
        if conn.get("connected"):
            if conn.get("connections"):
                status["connections"] = [
                    self._status_connection_state(connection)
                    for connection in conn["connections"]
            ]
            status["connected_device"] = conn.get("address")
            status["connected_protocol"] = conn.get("protocol")
            status["connected_name"] = conn.get("name")
            for key in _status_connection_keys():
                if key in conn:
                    status[key] = conn[key]

        return status

    @staticmethod
    def _status_connection_state(connection: dict) -> dict:
        return {
            key: connection[key]
            for key in _status_connection_keys()
            if key in connection
        }

    def _get_devices_cached(self) -> list:
        """Device list from devices.conf, cached by file mtime."""
        try:
            mtime = os.path.getmtime(config.devices_config_file)
        except OSError:
            mtime = 0

        with self._devices_lock:
            if self._devices_cache is not None and mtime == self._devices_mtime:
                return self._devices_cache

            devices = config.get_all_devices()
            self._devices_cache = [
                {
                    "address": addr,
                    "protocol": proto.value,
                    **({"name": name} if name else {}),
                }
                for addr, proto, name in devices
            ]
            self._devices_mtime = mtime
            return self._devices_cache

    # ---- Scan ----

    def request_scan(self):
        """From HTTP thread: schedule scan on event loop."""
        if self.is_scanning:
            return
        self.scan_result = None
        self.is_scanning = True
        self._scan_stop_event = asyncio.Event()
        asyncio.run_coroutine_threadsafe(self._do_scan(), self.loop)

    def request_scan_stop(self):
        if self._scan_stop_event:
            self._scan_stop_event.set()

    def _on_device_found(self, device):
        """Callback from scanner when a device is discovered."""
        self._scan_live_devices.append({
            "address": device.address,
            "name": device.name,
            "protocol": device.protocol.value,
            "rssi": device.rssi,
            "is_phone": device.is_phone,
        })
        host = getattr(self.daemon, "host", None)
        if host is not None:
            host.device_cache.set_class(device.address, device.is_phone)

    async def _do_scan(self):
        async with self._op_lock:
            self._scan_live_devices = []
            try:
                await self.daemon.suspend(reason="operation")
                config.validate_keystore()

                # Re-warm the chip if a prior /stop powered it off; opening the
                # transport against a cold chip makes HCI Reset time out.
                await asyncio.to_thread(chip().ensure_powered)

                await self.daemon.scan(
                    duration=10.0,
                    on_device_found=self._on_device_found,
                    stop_event=self._scan_stop_event,
                )
                self.scan_result = {
                    "ok": True,
                    "devices": self._scan_live_devices,
                }
            except Exception as e:
                logger.error(f"Scan failed: {errstr(e)}")
                self.scan_result = {"ok": False, "error": str(e)}
            finally:
                self.is_scanning = False
                self._scan_stop_event = None
                await self.daemon.resume(reason="operation")

    # ---- Pair ----

    def request_pair(self, address, protocol, name=None):
        """From HTTP thread: schedule pair on event loop."""
        if self.is_pairing:
            return
        self.pair_result = None
        self.is_pairing = True  # Set immediately so status polls see it
        asyncio.run_coroutine_threadsafe(
            self._do_pair(address, protocol, name), self.loop
        )

    async def _do_pair(self, address, protocol, name):
        async with self._op_lock:
            try:
                await self.daemon.suspend(reason="operation")
                config.validate_keystore()

                # Re-warm the chip if a prior /stop powered it off; opening the
                # transport against a cold chip makes HCI Reset time out.
                await asyncio.to_thread(chip().ensure_powered)

                success = await self.daemon.pair(address, protocol, name)
                if success:
                    config.add_device(address, protocol, name)
                self.pair_result = {
                    "ok": success,
                    "address": address,
                    **({"message": "Paired successfully"} if success
                       else {"error": "Pairing failed"}),
                }
            except Exception as e:
                logger.error(f"Pair failed: {errstr(e)}")
                self.pair_result = {"ok": False, "address": address, "error": str(e)}
            finally:
                self.is_pairing = False
                await self.daemon.resume(reason="operation")

    # ---- Connect / Resume ----

    def request_connect(self, address=None, protocol_str=None):
        """From HTTP thread: connect to a device or resume the daemon.

        With address: suspend → save device to config → resume.
        Without address: just resume if suspended (used by /start).
        """
        if not address:
            asyncio.run_coroutine_threadsafe(self._do_resume(), self.loop)
            return

        protocol = Protocol.CLASSIC if protocol_str == 'classic' else Protocol.BLE
        asyncio.run_coroutine_threadsafe(
            self._do_connect(address, protocol), self.loop
        )

    async def _do_resume(self):
        async with self._op_lock:
            self._suspended_by_system = False
            if self.daemon._suspended:
                await self.daemon.resume(reason="user")

    async def _do_connect(self, address, protocol):
        async with self._op_lock:
            try:
                await self.daemon.suspend(reason="operation")
                config.add_device(address, protocol)
                await self.daemon.resume(reason="operation")
            except Exception as e:
                logger.error(f"Connect failed: {errstr(e)}")
                await self.daemon.resume(reason="operation")

    # ---- System suspend (powerd) ----

    def on_system_suspend(self, event):
        """From power monitor thread: BT off before the system sleeps."""
        asyncio.run_coroutine_threadsafe(self._do_system_suspend(event), self.loop)

    async def _do_system_suspend(self, event):
        async with self._op_lock:
            if self.daemon._suspended:
                return
            logger.info(f"System suspend ({event}): quiescing HID transport")
            self._suspended_by_system = True
            if self.daemon._power_resume_task and not self.daemon._power_resume_task.done():
                self.daemon._power_resume_task.cancel()
            await self.daemon.suspend(reason="power")
            # Keep the shared MTK WMT module loaded.  Repeated rmmod/insmod
            # cycles around sleep were the source of the wake interrupt storm.
            await asyncio.to_thread(chip().quiesce)
            self.daemon._power_resume_task = asyncio.create_task(
                self.daemon._delayed_power_resume(
                    config.power_resume_max_delay,
                    "power-watchdog",
                )
            )

    def on_system_resume(self, event):
        """From power monitor thread: re-warm BT after wake."""
        asyncio.run_coroutine_threadsafe(self._do_system_resume(event), self.loop)

    async def _do_system_resume(self, event):
        async with self._op_lock:
            if not self._suspended_by_system:
                return
            self._suspended_by_system = False
            if not self.daemon._suspended:
                return
            logger.info(f"System resume ({event}): restarting BT")
            if self.daemon._power_resume_task and not self.daemon._power_resume_task.done():
                self.daemon._power_resume_task.cancel()
            self.daemon._power_resume_task = asyncio.create_task(
                self.daemon._delayed_power_resume(
                    config.power_resume_max_delay,
                    "power",
                )
            )

    # ---- Remove ----

    def request_remove(self, address: str) -> dict:
        """Remove a device from config, clear its cache, and disconnect."""
        result = config.remove_device(address)
        if result["removed"]:
            DeviceCache(config.cache_dir).clear(normalize_addr(address))
            self.request_disconnect()
        return result

    # ---- Clear Cache ----

    def request_clear_cache(self) -> int:
        """Clear all descriptor cache files. Returns count of files removed."""
        return DeviceCache(config.cache_dir).clear()

    # ---- Disconnect / Stop ----

    def request_disconnect(self, suspend=False):
        """From HTTP thread: drop connection.

        suspend=False: drop connection, daemon keeps running (reconnect loop).
        suspend=True:  suspend daemon entirely (/stop).
        """
        asyncio.run_coroutine_threadsafe(
            self._do_disconnect(suspend), self.loop
        )

    async def _do_disconnect(self, suspend):
        async with self._op_lock:
            try:
                if suspend:
                    await self.daemon.suspend(reason="manual")
                    await asyncio.to_thread(chip().power_off)
                else:
                    await self.daemon.disconnect()
            except Exception as e:
                logger.error(f"Disconnect failed: {errstr(e)}")
