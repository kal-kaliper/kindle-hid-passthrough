#!/usr/bin/env python3
"""
BLE/Classic HID Host - UHID Passthrough

HID device support for Linux using Google Bumble.
Supports both Bluetooth Low Energy (BLE) and Classic Bluetooth (BR/EDR).
Forwards HID reports to Linux via UHID.

Usage:
    # Interactive pairing
    python main.py --pair --protocol classic

    # Run (connect to configured device)
    python main.py

    # Run as daemon
    python main.py --daemon

    # Programmatic use
    from host import BLEHIDHost
    from classic_host import ClassicHIDHost
    from config import create_host, Protocol

    host = create_host(Protocol.CLASSIC)
    await host.run(device_address)
"""

from kindle_hid_passthrough.host import BLEHIDHost, __version__
from kindle_hid_passthrough.config import config, Protocol, create_host
from kindle_hid_passthrough.logging_utils import log
from kindle_hid_passthrough.device_cache import DeviceCache

try:
    from kindle_hid_passthrough.classic_host import ClassicHIDHost
except ImportError:
    ClassicHIDHost = None

__all__ = [
    'BLEHIDHost',
    'ClassicHIDHost',
    'Protocol',
    'create_host',
    'config',
    'log',
    'DeviceCache',
    '__version__',
]
