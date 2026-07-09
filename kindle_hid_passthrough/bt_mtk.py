#!/usr/bin/env python3
"""MediaTek (Kindle 11th gen+) BT via the wmt_cdev_bt kernel module + /dev/stpbt."""

import glob
import os
import signal
import subprocess
import threading
import time

from bt_chip import BtChip, free_device, run
from config import config
from fd_utils import close_own_fds_for_path
from logging_utils import log

DEFAULT_MODULE_PATTERNS = [
    'wmt_cdev_bt.ko',   # MediaTek (PW4/5, Kindle 10/11, Scribe)
    'bt_drv.ko',         # Older Freescale/NXP Kindles
]


def _find_bt_module(patterns=None):
    """Find the BT kernel module path for the running kernel, or None."""
    patterns = patterns or DEFAULT_MODULE_PATTERNS
    try:
        uname = os.uname().release
    except Exception:
        return None
    base = f'/lib/modules/{uname}/extra'
    for pattern in patterns:
        matches = glob.glob(f'{base}/{pattern}')
        if matches:
            return matches[0]
        matches = glob.glob(f'{base}/**/{pattern}', recursive=True)
        if matches:
            return matches[0]
    return None


def _is_module_loaded(module_path):
    """Check if a kernel module is already loaded."""
    mod_name = _module_name(module_path)
    return _is_module_name_loaded(mod_name)


def _module_name(module_path):
    return os.path.basename(module_path).replace('.ko', '')


def _is_module_name_loaded(mod_name):
    try:
        with open('/proc/modules', 'r') as f:
            for line in f:
                if line.split()[0] == mod_name:
                    return True
    except Exception:
        pass
    return False


def _kill_holders_via_proc(device_path):
    """SIGKILL processes holding device_path by scanning /proc (fuser fallback)."""
    my_pid = os.getpid()
    killed = 0
    try:
        pids = [e for e in os.listdir('/proc') if e.isdigit()]
    except OSError:
        return 0
    for pid in pids:
        if int(pid) == my_pid:
            continue
        fd_dir = f'/proc/{pid}/fd'
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f'{fd_dir}/{fd}')
            except OSError:
                continue
            if target == device_path:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                    killed += 1
                    log.info(f"Killed PID {pid} holding {device_path}")
                except OSError as e:
                    log.warning(f"Could not kill PID {pid}: {e}")
                break
    return killed


def _is_device_free(device_path):
    """Check if the BT device can be opened."""
    try:
        fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
        os.close(fd)
        return True
    except OSError:
        return False


def _release_own_fds(device_path):
    return close_own_fds_for_path(device_path)


def _run_logged(cmd, timeout=10):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(f"{cmd[0]} failed: {e}")
        return False
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout or '').strip()
    if detail:
        log.warning(f"{' '.join(cmd)} failed ({result.returncode}): {detail}")
    else:
        log.warning(f"{' '.join(cmd)} failed ({result.returncode})")
    return False


class MtkChip(BtChip):
    survives_suspend = False

    def __init__(self, kindle):
        super().__init__(kindle)
        self._power_lock = threading.RLock()
        self._powered = False

    def _device_path(self):
        return self.kindle.device_path if self.kindle else '/dev/stpbt'

    def _module_patterns(self):
        patterns = config.bt_module_patterns
        if patterns is None and self.kindle and self.kindle.kernel_module:
            patterns = [self.kindle.kernel_module]
        return patterns

    def _loaded_module_names(self, module_path=None):
        candidates = []
        if module_path:
            candidates.append(_module_name(module_path))
        for pattern in self._module_patterns() or DEFAULT_MODULE_PATTERNS:
            candidates.append(_module_name(pattern))

        loaded = []
        for name in candidates:
            if name and name not in loaded and _is_module_name_loaded(name):
                loaded.append(name)
        return loaded

    def prepare(self):
        with self._power_lock:
            device_path = self._device_path()
            settle = config.bt_settle_time
            module_path = _find_bt_module(self._module_patterns())
            if module_path:
                if _is_module_loaded(module_path):
                    log.info(f"BT module already loaded: {os.path.basename(module_path)}")
                else:
                    log.info(f"Loading BT module: {module_path}")
                    if run(['/sbin/insmod', module_path]):
                        log.info("BT module loaded")
                        time.sleep(0.5)
                    else:
                        log.warning(f"Failed to load {module_path} (may need root)")
            else:
                log.info("No BT kernel module found (may already be built-in)")

            if not os.path.exists(device_path):
                log.warning(f"{device_path} does not exist")
                self._powered = False
                return False
            if _is_device_free(device_path):
                log.info(f"{device_path} is available")
                self._powered = True
                return True

            log.info(f"{device_path} is busy, evicting holder...")
            if free_device(device_path):
                time.sleep(settle)
            if _is_device_free(device_path):
                log.info(f"{device_path} is now available")
                self._powered = True
                return True

            log.warning(f"{device_path} still busy, scanning /proc for holders...")
            if _kill_holders_via_proc(device_path):
                time.sleep(settle)
            if _is_device_free(device_path):
                log.info(f"{device_path} is now available")
                self._powered = True
                return True

            log.warning(f"{device_path} still busy, waiting 2s...")
            time.sleep(2.0)
            if _is_device_free(device_path):
                log.info(f"{device_path} is now available")
                self._powered = True
                return True

            log.warning(f"{device_path} still busy after cleanup")
            self._powered = False
            return False

    def power_off(self):
        with self._power_lock:
            device_path = self._device_path()
            settle = config.bt_settle_time

            if os.path.exists(device_path):
                if not _is_device_free(device_path):
                    log.info(f"Releasing {device_path} before MTK suspend")
                    if _release_own_fds(device_path):
                        time.sleep(settle)
                if not _is_device_free(device_path):
                    if _kill_holders_via_proc(device_path):
                        time.sleep(settle)
                if not _is_device_free(device_path):
                    log.warning(f"{device_path} still busy before MTK module unload")

            module_path = _find_bt_module(self._module_patterns())
            loaded = self._loaded_module_names(module_path)
            if not loaded:
                log.info("No MTK BT module loaded")
                self._powered = False
                return

            for module_name in loaded:
                log.info(f"Unloading BT module: {module_name}")
                if _run_logged(['/sbin/rmmod', module_name]):
                    log.info(f"BT module unloaded: {module_name}")
                    time.sleep(settle)

            self._powered = False

    def on_hci_reset_timeout(self):
        log.warning("HCI Reset timed out on MTK; unloading BT module for a clean restart")
        self.power_off()

    def ensure_powered(self):
        with self._power_lock:
            if self._powered and os.path.exists(self._device_path()):
                return
            log.info("Rearming MTK Bluetooth hardware")
            self.prepare()
