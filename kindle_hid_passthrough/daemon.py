#!/usr/bin/env python3
"""Persistent connection manager with auto-reconnect + embedded API server."""

import asyncio
import logging
import signal
import subprocess
import sys
import threading

sys.path.insert(0, '/mnt/us/kindle_hid_passthrough')

from api_server import PORT, APIServer, RequestHandler
from bt_setup import chip, prepare_bt
from config import config, get_version
from controller import DaemonController
from host import HIDHost
from logging_utils import errstr, log, setup_daemon_logging
from power_monitor import PowerMonitor
from scanner import Scanner
from wifi_ready import wifi_readiness

logger = logging.getLogger(__name__)


def powerd_state():
    """Return powerd's normalized state, or None when LIPC is unavailable."""
    try:
        result = subprocess.run(
            ['lipc-get-prop', 'com.lab126.powerd', 'state'],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().lower() or None


class HIDDaemon:
    """Daemon that maintains persistent connection to an HID device."""

    def __init__(self):
        self.running = False
        self.host = None
        self._host_task = None
        self._suspended = False
        self._suspend_reason = None
        self._resume_event = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._power_resume_task = None
        self._power_blocked = False
        self._resume_after_power = False
        self._resume_after_power_reason = None
        self._paired_host = None
        self._classic_flap_counts = {}
        self._classic_flap_until = {}
        self._classic_retry_not_before = 0.0
        self._ble_bond_3e_fail_counts = {}

    @property
    def connection_state(self) -> dict:
        """Current connection state for API."""
        if self.host and not self._suspended:
            return self.host.connection_state
        return {"connected": False}

    async def suspend(self, reason="manual"):
        """Disconnect and release transport for scan/pair."""
        async with self._lifecycle_lock:
            if reason == "power":
                self._power_blocked = True
            elif reason == "manual":
                self._resume_after_power = False
                self._resume_after_power_reason = None

            if self._suspended:
                if reason != "power":
                    self._suspend_reason = reason
                detail = f" ({self._suspend_reason})" if self._suspend_reason else ""
                logger.info(f"Daemon already suspended{detail}")
                return

            logger.info(f"Daemon suspending ({reason})...")
            self._suspended = True
            self._suspend_reason = reason
            self._resume_event.clear()

            if self._host_task and not self._host_task.done():
                self._host_task.cancel()
                try:
                    await self._host_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._host_task = None

            if self.host:
                try:
                    await self.host.cleanup()
                except Exception:
                    pass
                self.host = None

            logger.info(f"Daemon suspended ({reason})")

    async def scan(self, duration=10.0, on_device_found=None, stop_event=None):
        """Scan for BT devices. Must be called while suspended."""
        scanner = Scanner()
        if on_device_found:
            scanner.on_device_found = on_device_found
        try:
            await scanner.start()
            await scanner.scan(duration=duration, stop_event=stop_event)
        finally:
            await scanner.cleanup()

    async def pair(self, address, protocol, name=None) -> bool:
        """Pair with a device. Must be called while suspended."""
        host = HIDHost()
        try:
            success = await host.pair_device(address, protocol, name)
            if success:
                self._paired_host = host
                return True
            await host.cleanup()
            return False
        except Exception:
            await host.cleanup()
            raise

    async def disconnect(self):
        """Drop the active connection; daemon keeps running and will reconnect."""
        if self.host:
            disconnected = await self.host.disconnect_all()
        else:
            disconnected = False
        if not disconnected:
            logger.info("No active connection to disconnect")
        if self._host_task and not self._host_task.done():
            self._host_task.cancel()

    async def resume(self, reason=None):
        """Resume connections after scan/pair."""
        async with self._lifecycle_lock:
            if self._power_blocked and reason == "user":
                logger.info("User resume requested during WMT/Wi-Fi recovery; resuming now")
                self._power_blocked = False
                if self._power_resume_task and not self._power_resume_task.done():
                    self._power_resume_task.cancel()
            elif self._power_blocked and reason not in ("power", "power-watchdog"):
                if reason == "user" or self._suspend_reason != "manual":
                    self._resume_after_power = True
                    self._resume_after_power_reason = reason
                detail = f" ({reason})" if reason else ""
                logger.info(f"Resume deferred during WMT/Wi-Fi recovery{detail}")
                return

            if reason in ("power", "power-watchdog"):
                self._power_blocked = False
                if not self._suspended:
                    self._resume_after_power = False
                    return
                if self._suspend_reason != "power":
                    should_resume = (
                        self._resume_after_power
                        and (
                            self._suspend_reason != "manual"
                            or self._resume_after_power_reason == "user"
                        )
                    )
                    if not should_resume:
                        logger.info(
                            f"Power resume ignored; daemon suspended by "
                            f"{self._suspend_reason}"
                        )
                        self._resume_after_power = False
                        self._resume_after_power_reason = None
                        return
                    logger.info("Power resume delay elapsed; applying deferred resume")
            elif not self._suspended:
                return

            detail = f" ({reason})" if reason else ""
            logger.info(f"Daemon resuming{detail}...")
            self._suspended = False
            self._suspend_reason = None
            self._resume_after_power = False
            self._resume_after_power_reason = None
            self._resume_event.set()

    async def handle_power_event(self, event: str):
        if event in ("goingToScreenSaver", "readyToSuspend", "suspending"):
            if self._power_resume_task and not self._power_resume_task.done():
                self._power_resume_task.cancel()
            await self.suspend(reason="power")
            self._power_resume_task = asyncio.create_task(
                self._delayed_power_resume(config.power_resume_max_delay, "power-watchdog"),
                name="power_resume_watchdog",
            )
            return

        if event not in ("wakeupFromSuspend", "resuming", "outOfScreenSaver"):
            return

        if self._power_resume_task and not self._power_resume_task.done():
            self._power_resume_task.cancel()
        self._power_resume_task = asyncio.create_task(
            self._delayed_power_resume(config.power_resume_max_delay, "power"),
            name="power_resume_wifi_gate",
        )

    async def _delayed_power_resume(self, delay, reason):
        if reason == "power":
            if config.power_wifi_gate_enabled:
                try:
                    await self._wait_for_power_resume_ready(delay)
                except asyncio.CancelledError:
                    return
                await self.resume(reason=reason)
                return
            delay = config.power_resume_delay

        if delay > 0:
            logger.info(f"Power resume: waiting {delay:.0f}s for WMT/Wi-Fi")
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

        if reason == "power-watchdog":
            # This fallback exists for a missed wake event, but screensaver ->
            # readyToSuspend can itself take longer than the watchdog ceiling.
            # Do not re-arm the shared MTK BT/Wi-Fi stack while powerd still
            # says the device is asleep. Polling also preserves recovery: if
            # the wake event was genuinely missed, powerd eventually reports
            # active and the normal Wi-Fi readiness gate runs before resume.
            last_state = None
            while True:
                state = await asyncio.to_thread(powerd_state)
                if state is None or state == "active":
                    break
                if state != last_state:
                    logger.info(
                        f"Power watchdog: powerd state is {state}; keeping BT off"
                    )
                    last_state = state
                try:
                    await asyncio.sleep(max(0.1, config.power_resume_poll_interval))
                except asyncio.CancelledError:
                    return
            if state == "active" and config.power_wifi_gate_enabled:
                try:
                    await self._wait_for_power_resume_ready(
                        config.power_resume_max_delay
                    )
                except asyncio.CancelledError:
                    return
        await self.resume(reason=reason)

    async def _wait_for_power_resume_ready(self, max_delay):
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + max_delay

        min_delay = max(0.0, config.power_resume_min_delay)
        if min_delay > 0:
            logger.info(
                f"Power resume: waiting {min_delay:.0f}s minimum for WMT/Wi-Fi"
            )
            try:
                await asyncio.sleep(min_delay)
            except asyncio.CancelledError:
                raise

        stable_required = max(1, config.power_resume_stable_polls)
        poll_interval = max(0.1, config.power_resume_poll_interval)
        stable_count = 0
        last_reason = None

        logger.info(
            f"Power resume: waiting for Wi-Fi readiness "
            f"(max {max_delay:.0f}s, stable={stable_required})"
        )

        while True:
            readiness = await asyncio.to_thread(wifi_readiness)
            elapsed = loop.time() - started
            if readiness.ready:
                stable_count += 1
                if stable_count >= stable_required:
                    logger.info(
                        f"Power resume: Wi-Fi ready after {elapsed:.1f}s "
                        f"({readiness.reason})"
                    )
                    return
            else:
                stable_count = 0
                if readiness.reason != last_reason:
                    logger.info(
                        f"Power resume: Wi-Fi not ready after {elapsed:.1f}s "
                        f"({readiness.reason})"
                    )
                    last_reason = readiness.reason

            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    f"Power resume: Wi-Fi readiness timed out after "
                    f"{max_delay:.0f}s; resuming anyway "
                    f"(last={readiness.reason})"
                )
                return

            try:
                await asyncio.sleep(min(poll_interval, remaining))
            except asyncio.CancelledError:
                raise

    def _has_devices(self, log_details=False) -> bool:
        """Check if any devices are configured."""
        devices = config.get_all_devices()
        if not devices:
            return False

        if log_details:
            if len(devices) == 1 and devices[0][0] != '*':
                addr, proto, name = devices[0]
                display = f"{name} ({addr})" if name else addr
                logger.info(f"Device: {display} ({proto.value})")
            else:
                logger.info(f"Accepting {len(devices)} device(s):")
                for addr, proto, name in devices:
                    display = f"{name} ({addr})" if name else addr
                    logger.info(f"  - {display} ({proto.value})")

        return True

    def _seed_host_state(self, host: HIDHost):
        """Carry reconnect throttles across host restarts."""
        host._classic_flap_counts = self._classic_flap_counts
        host._classic_flap_until = self._classic_flap_until
        host._classic_retry_not_before = self._classic_retry_not_before
        host._ble_bond_3e_fail_counts = self._ble_bond_3e_fail_counts

    def _capture_host_state(self, host: HIDHost):
        self._classic_flap_counts = host._classic_flap_counts
        self._classic_flap_until = host._classic_flap_until
        self._classic_retry_not_before = host._classic_retry_not_before
        self._ble_bond_3e_fail_counts = host._ble_bond_3e_fail_counts

    async def run(self):
        """Main daemon loop."""
        self.running = True

        logger.info(f"HID Daemon v{get_version()}")

        while self.running:
            # Wait for devices if none configured or after suspend
            if self._suspended:
                logger.info("Daemon suspended, waiting for resume...")
                await self._resume_event.wait()
                self._resume_event.clear()
                if not self.running:
                    break

            if not self._has_devices(log_details=True):
                logger.info("No devices configured, waiting for pairing...")
                self._resume_event.clear()
                await self._resume_event.wait()
                self._resume_event.clear()  # else the next reconnect delay is skipped
                if not self.running:
                    break
                continue

            skip_delay = False
            await asyncio.to_thread(chip().ensure_powered)

            try:
                # Use handed-off host from controller pairing if available
                if self._paired_host:
                    logger.info("=== Continuing with paired device ===")
                    self.host = self._paired_host
                    self._paired_host = None
                    self._seed_host_state(self.host)
                    self._host_task = asyncio.create_task(
                        self.host.continue_after_pairing()
                    )
                else:
                    logger.info("=== Starting connection ===")
                    self.host = HIDHost()
                    self._seed_host_state(self.host)
                    self._host_task = asyncio.create_task(
                        self.host.run()
                    )
                await self._host_task

            except asyncio.CancelledError:
                if self._suspended:
                    logger.info("Connection cancelled (suspend)")
                elif not self.running:
                    logger.info("Cancelled (shutdown)")
                    break
                else:
                    logger.info("Connection cancelled, will reconnect")

            except Exception as e:
                logger.error(f"Error: {errstr(e)}")

            finally:
                self._host_task = None
                # When suspended, suspend() owns cleanup of self.host. Skipping
                # here avoids a race where both paths run host.cleanup() in
                # parallel and deadlock on transport/connection teardown.
                auth_fail_addr = None
                vc_unplug_addr = None
                if self.host and not self._suspended:
                    self._capture_host_state(self.host)
                    auth_fail_addr = self.host.get_auth_failure_address()
                    vc_unplug_addr = self.host.get_virtual_cable_unplug_address()
                    try:
                        await self.host.cleanup()
                    except Exception:
                        pass
                    self.host = None

                if vc_unplug_addr:
                    logger.info(f"Virtual cable unplugged by {vc_unplug_addr}, removing device")
                    config.remove_device(vc_unplug_addr)
                    skip_delay = True

                if auth_fail_addr:
                    logger.info(f"Auth failure for {auth_fail_addr}, clearing stale key")
                    config.remove_pairing_key(auth_fail_addr)
                    skip_delay = True

            if not self.running:
                break

            # Don't delay if we got suspended during connection
            if self._suspended:
                continue

            if not skip_delay:
                logger.info(f"Reconnecting in {config.reconnect_delay}s...")
                try:
                    await asyncio.wait_for(
                        self._resume_event.wait(),
                        timeout=config.reconnect_delay
                    )
                    # Resume event fired during delay — go back to top
                    self._resume_event.clear()
                except asyncio.TimeoutError:
                    pass  # Normal delay elapsed

        logger.info("Daemon stopped")

    async def stop(self):
        """Stop the daemon."""
        logger.info("Stopping...")
        self.running = False
        if self._power_resume_task and not self._power_resume_task.done():
            self._power_resume_task.cancel()
        self._resume_event.set()
        if self.host:
            try:
                await self.host.cleanup()
            except Exception:
                pass


async def main():
    setup_daemon_logging(config.log_file)

    await asyncio.to_thread(prepare_bt)
    if config.power_startup_delay > 0:
        log.info(f"Waiting {config.power_startup_delay:.0f}s for WMT/Wi-Fi startup")
        await asyncio.sleep(config.power_startup_delay)

    daemon = HIDDaemon()
    controller = DaemonController(daemon)
    controller.loop = asyncio.get_event_loop()

    # Start embedded API server
    server = APIServer(('127.0.0.1', PORT), RequestHandler)
    server.controller = controller
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    log.info(f"API server listening on port {PORT}")

    monitor = None
    if config.power_monitor_enabled and not chip().survives_suspend:
        monitor = PowerMonitor(controller)
        monitor.start()
        log.info("Watching powerd for system suspend")

    # Signal handling
    shutdown = asyncio.Event()

    def on_signal():
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal)

    log.info(f"Kindle HID Passthrough v{get_version()} (daemon)")
    daemon_task = asyncio.create_task(daemon.run())

    await asyncio.wait(
        [daemon_task, asyncio.create_task(shutdown.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    if shutdown.is_set():
        await daemon.stop()
        if not daemon_task.done():
            daemon_task.cancel()
            try:
                await daemon_task
            except asyncio.CancelledError:
                pass

    if monitor:
        monitor.stop()
    server.shutdown()
    logger.info("Daemon stopped")


if __name__ == '__main__':
    asyncio.run(main())
