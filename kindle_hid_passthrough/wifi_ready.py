#!/usr/bin/env python3
"""Kindle Wi-Fi readiness checks used before re-arming MTK Bluetooth."""

import os
import re
import subprocess
from dataclasses import dataclass

WIFI_INTERFACE = 'wlan0'


@dataclass
class WifiReadiness:
    ready: bool
    reason: str
    details: dict


def _run_text(cmd, timeout=2):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _lipc_get(service, prop):
    return _run_text(['lipc-get-prop', service, prop])


def _read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _ifconfig_ipv4(interface=WIFI_INTERFACE):
    output = _run_text(['ifconfig', interface])
    if not output:
        return None
    match = re.search(r'inet addr:([0-9.]+)', output)
    if match:
        return match.group(1)
    match = re.search(r'\binet ([0-9.]+)\b', output)
    return match.group(1) if match else None


def _has_default_route(interface=WIFI_INTERFACE):
    try:
        with open('/proc/net/route') as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                iface, destination, _gateway, flags = parts[:4]
                if iface != interface or destination != '00000000':
                    continue
                try:
                    return bool(int(flags, 16) & 0x2)  # RTF_GATEWAY
                except ValueError:
                    return True
    except OSError:
        return False
    return False


def _is_disabled(value):
    return value is not None and value.strip() == '0'


def _is_unavailable(value):
    return value is None or value == ''


def wifi_readiness(interface=WIFI_INTERFACE):
    """Return whether Kindle Wi-Fi looks ready enough to let BT reattach.

    The gate is intentionally multi-signal. On MTK Kindles the Bluetooth cdev
    is a leaf of the shared WMT stack; after system wake we wait for Wi-Fi's
    userspace and kernel surfaces to agree before loading the BT cdev again.
    """

    details = {
        'cmd_wireless_enable': _lipc_get('com.lab126.cmd', 'wirelessEnable'),
        'wifid_enable': _lipc_get('com.lab126.wifid', 'enable'),
        'cmd_active_interface': _lipc_get('com.lab126.cmd', 'activeInterface'),
        'wifid_cm_state': _lipc_get('com.lab126.wifid', 'cmState'),
        'operstate': _read_text(f'/sys/class/net/{interface}/operstate'),
        'carrier': _read_text(f'/sys/class/net/{interface}/carrier'),
        'ipv4': _ifconfig_ipv4(interface),
        'default_route': _has_default_route(interface),
    }

    if _is_disabled(details['cmd_wireless_enable']) or _is_disabled(details['wifid_enable']):
        return WifiReadiness(True, 'wifi-disabled', details)

    interface_path = f'/sys/class/net/{interface}'
    if not os.path.exists(interface_path):
        return WifiReadiness(True, 'wifi-interface-missing', details)

    missing = []
    if details['cmd_active_interface'] not in (None, '', 'wifi'):
        missing.append(f"activeInterface={details['cmd_active_interface']}")
    if details['wifid_cm_state'] != 'CONNECTED':
        missing.append(f"cmState={details['wifid_cm_state']}")
    if details['operstate'] != 'up':
        missing.append(f"operstate={details['operstate']}")
    if details['carrier'] not in (None, '1'):
        missing.append(f"carrier={details['carrier']}")
    if not details['ipv4']:
        missing.append('ipv4=missing')
    if details['default_route'] is not True:
        missing.append('default_route=missing')

    if missing:
        return WifiReadiness(False, ', '.join(missing), details)

    unavailable = [
        key for key in (
            'cmd_wireless_enable',
            'wifid_enable',
            'cmd_active_interface',
            'wifid_cm_state',
        )
        if _is_unavailable(details[key])
    ]
    reason = 'ready'
    if unavailable:
        reason = f"ready-without-lipc:{','.join(unavailable)}"
    return WifiReadiness(True, reason, details)
