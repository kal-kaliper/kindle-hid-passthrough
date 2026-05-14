#!/usr/bin/env python3
"""Bumble transport and device initialization."""

import asyncio

from bumble.device import Device
from bumble.hci import HCI_Reset_Command
from bumble.transport import open_transport

from config import config
from logging_utils import log


async def create_bumble_device(transport_spec=None):
    """Open HCI transport, create a Bumble Device, reset, and power on.

    Returns:
        (transport, device) tuple.
    """
    spec = transport_spec or config.transport

    try:
        transport = await asyncio.wait_for(
            open_transport(spec),
            timeout=config.transport_timeout
        )
    except asyncio.TimeoutError:
        log.error(f"Transport open timed out after {config.transport_timeout}s")
        raise

    device = Device.with_hci(
        config.device_name,
        config.device_address,
        transport.source,
        transport.sink
    )

    try:
        await asyncio.wait_for(
            device.host.send_command(HCI_Reset_Command()),
            timeout=config.hci_reset_timeout
        )
        log.success("HCI Reset successful")
        await asyncio.sleep(0.2)
    except asyncio.TimeoutError:
        log.error("HCI Reset timed out")
        raise

    await device.power_on()
    log.success(f"Device powered on: {device.public_address}")

    return transport, device
