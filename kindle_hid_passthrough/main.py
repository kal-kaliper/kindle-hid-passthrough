#!/usr/bin/env python3
"""
Kindle HID Passthrough

Userspace Bluetooth HID host with UHID passthrough.
Supports both BLE and Classic Bluetooth HID devices.
Forwards all HID reports to Linux via UHID.

Usage:
    main.py                    # Run normally (connect to configured device)
    main.py --pair             # Interactive pairing mode (scans BLE + Classic)
    main.py --daemon           # Run as daemon with auto-reconnect
    main.py --address XX:XX:XX:XX:XX:XX  # Connect to specific address

Author: Lucas Zampieri <lzampier@redhat.com>
"""

import argparse
import asyncio
import sys
import os

# Add current directory to path for imports
sys.path.insert(0, '/mnt/us/kindle_hid_passthrough')

from config import config, Protocol, create_host, create_scanner
from logging_utils import log


async def pair_mode(protocol_override: Protocol = None, sequential: bool = False):
    """Interactive pairing mode - scan BLE and Classic, then pair.

    Args:
        protocol_override: If set, only scan for this protocol (legacy mode)
        sequential: If True, scan BLE then Classic sequentially (not concurrent)
    """
    if protocol_override:
        log.info(f"Pairing mode ({protocol_override.value} only)")
        await _pair_mode_legacy(protocol_override)
    else:
        mode = "sequentially" if sequential else "concurrently"
        log.info(f"Pairing mode (scanning BLE + Classic {mode})")
        await _pair_mode_unified(sequential=sequential)


async def _pair_mode_unified(sequential: bool = False):
    """Unified pairing - scans both BLE and Classic.

    Args:
        sequential: If True, scan sequentially instead of concurrently
    """
    scanner = create_scanner()

    try:
        await scanner.start()

        log.info("Put your device in pairing mode...")
        devices = []
        while not devices:
            devices = await scanner.scan(duration=10.0, concurrent=not sequential)
            if not devices:
                log.warning("No HID devices found. Scanning again...")
                await asyncio.sleep(2)

        # Show device list with protocol tags
        print("\nFound devices:")
        for i, dev in enumerate(devices):
            proto_tag = "[BLE]" if dev.protocol == Protocol.BLE else "[Classic]"
            print(f"  {i+1}. {proto_tag} {dev.name} ({dev.address})")

        # Get user choice
        selected = None
        while True:
            try:
                choice = input("\nSelect device (number): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(devices):
                    selected = devices[idx]
                    break
                print("Invalid selection")
            except ValueError:
                print("Enter a number")
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled")
                return

        log.info(f"Selected: {selected.name} ({selected.address}) [{selected.protocol.value}]")

    finally:
        await scanner.cleanup()

    # Now create the appropriate host for pairing
    host = create_host(selected.protocol)

    try:
        await host.start()

        # Pair with the device
        success = await host.pair_device(selected.address)

        if success:
            log.success(f"Paired with {selected.name}")

            # Auto-save to devices.conf
            save_device_config(selected.address, selected.protocol)
            log.success("Saved to devices.conf. Run without --pair to connect.")
        else:
            log.error("Pairing failed")

    finally:
        await host.cleanup()


async def _pair_mode_legacy(protocol: Protocol):
    """Legacy pairing mode - single protocol only."""
    host = create_host(protocol)

    try:
        await host.start()

        log.info("Put your device in pairing mode...")
        devices = []
        while not devices:
            devices = await host.scan(duration=10.0)
            if not devices:
                log.warning("No HID devices found. Scanning again...")
                await asyncio.sleep(2)

        # Show device list
        print("\nFound devices:")
        for i, dev in enumerate(devices):
            print(f"  {i+1}. {dev['name']} ({dev['address']})")

        # Get user choice
        while True:
            try:
                choice = input("\nSelect device (number): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(devices):
                    break
                print("Invalid selection")
            except ValueError:
                print("Enter a number")
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled")
                return

        selected = devices[idx]
        log.info(f"Selected: {selected['name']} ({selected['address']})")

        # Pair
        success = await host.pair_device(selected['address'])

        if success:
            log.success(f"Paired with {selected['name']}")

            # Auto-save to devices.conf
            save_device_config(selected['address'], protocol)
            log.success("Saved to devices.conf. Run without --pair to connect.")
        else:
            log.error("Pairing failed")

    finally:
        await host.cleanup()


def save_device_config(address: str, protocol: Protocol):
    """Save device address to devices.conf (appends, avoids duplicates)."""
    conf_file = config.devices_config_file
    log.info(f"Saving to: {conf_file}")

    # Ensure directory exists
    dir_path = os.path.dirname(conf_file)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

    # Normalize address for comparison
    addr_norm = address.split('/')[0].upper()

    # Check if already exists
    existing_devices = config.get_all_devices()
    for existing_addr, _ in existing_devices:
        if existing_addr.split('/')[0].upper() == addr_norm:
            log.info(f"Device {address} already in devices.conf")
            return

    try:
        # Create file with header if it doesn't exist
        if not os.path.exists(conf_file):
            with open(conf_file, 'w') as f:
                f.write("# Device addresses and protocols\n")
                f.write("# Format: ADDRESS [ble|classic]\n")

        # Append new device
        with open(conf_file, 'a') as f:
            f.write(f"{address} {protocol.value}\n")
        log.info(f"Added: {address} {protocol.value}")
    except Exception as e:
        log.error(f"Failed to save: {e}")


async def run_mode(address: str, protocol: Protocol):
    """Normal run mode - connect and forward reports."""
    log.info(f"Connecting to {address} ({protocol.value})")

    host = create_host(protocol)

    try:
        await host.run(address)
    except KeyboardInterrupt:
        log.warning("\nInterrupted")
    except Exception as e:
        log.error(f"Error: {e}")
        raise
    finally:
        await host.cleanup()


async def daemon_mode(address: str, protocol: Protocol):
    """Daemon mode - auto-reconnect on disconnection."""
    log.info(f"Daemon mode: {address} ({protocol.value})")

    while True:
        host = create_host(protocol)

        try:
            log.info("=== Starting connection ===")
            await host.run(address)
        except KeyboardInterrupt:
            log.warning("\nDaemon interrupted")
            await host.cleanup()
            break
        except Exception as e:
            log.error(f"Connection error: {e}")
        finally:
            try:
                await host.cleanup()
            except Exception:
                pass

        log.info(f"Reconnecting in {config.reconnect_delay}s...")
        await asyncio.sleep(config.reconnect_delay)


def main():
    parser = argparse.ArgumentParser(
        description='Kindle HID Passthrough - Userspace Bluetooth HID host'
    )
    parser.add_argument('--pair', action='store_true',
                        help='Interactive pairing mode (scans BLE + Classic)')
    parser.add_argument('--daemon', action='store_true',
                        help='Run as daemon with auto-reconnect')
    parser.add_argument('--address', type=str,
                        help='Device address (overrides devices.conf)')
    parser.add_argument('--protocol', type=str, choices=['ble', 'classic'],
                        help='Bluetooth protocol (for --pair: scan only this; for run: override config)')
    parser.add_argument('--sequential', action='store_true',
                        help='Scan BLE and Classic sequentially instead of concurrently')

    args = parser.parse_args()

    log.info(f"Config base path: {config.base_path}")

    # Determine protocol override
    protocol_override = None
    if args.protocol:
        protocol_override = Protocol.CLASSIC if args.protocol == 'classic' else Protocol.BLE

    # Pair mode
    if args.pair:
        asyncio.run(pair_mode(protocol_override, sequential=args.sequential))
        return

    # Get device address and protocol for run/daemon modes
    address = args.address
    protocol = protocol_override or config.protocol

    if not address:
        device_config = config.get_device_config()
        if device_config:
            address, protocol = device_config
            # Protocol override takes precedence
            if protocol_override:
                protocol = protocol_override
            log.info(f"Using device from {config.devices_config_file}: {address}")
        else:
            log.error("No device address specified. Use --address or create devices.conf")
            log.info("Run with --pair to set up a new device")
            sys.exit(1)

    log.info(f"Using {protocol.value.upper()} protocol")

    # Run mode
    if args.daemon:
        asyncio.run(daemon_mode(address, protocol))
    else:
        asyncio.run(run_mode(address, protocol))


if __name__ == '__main__':
    main()
