#!/usr/bin/env python3
"""
Base HID Host - Shared functionality for HID hosts

Provides common functionality for UHID device management,
transport initialization, and connection state.

Author: Lucas Zampieri <lzampier@redhat.com>
"""

from abc import ABC, abstractmethod
from typing import Optional

from config import config
from logging_utils import log

__all__ = ['BaseHIDHost']


class BaseHIDHost(ABC):
    """Base class for HID hosts with shared UHID functionality.

    Provides:
    - Transport specification handling
    - UHID device creation and cleanup
    - Common connection state management
    """

    def __init__(self, transport_spec: str = None):
        """Initialize base HID host.

        Args:
            transport_spec: HCI transport (default: from config)
        """
        # Transport
        self.transport_spec = transport_spec or config.transport
        self.transport = None

        # Bumble device and connection
        self.device = None
        self.connection = None

        # Device state
        self.current_device_address = None
        self.device_name = None
        self.report_map: Optional[bytes] = None

        # UHID
        self.uhid_device = None
        self._setup_uhid()

    def _setup_uhid(self):
        """Initialize UHID availability."""
        self._uhid_available = False
        try:
            from uhid_handler import UHIDDevice, Bus, UHIDError
            self._UHIDDevice = UHIDDevice
            self._Bus = Bus
            self._UHIDError = UHIDError
            self._uhid_available = True
        except ImportError:
            log.warning("UHID support not available")

    def _create_uhid_device(self, default_name: str = "HID Device"):
        """Create UHID virtual device.

        Args:
            default_name: Name to use if device_name is not set
        """
        if not self._uhid_available:
            log.warning("UHID not available")
            return

        if not self.report_map:
            log.warning("No report descriptor for UHID")
            return

        try:
            name = self.device_name or default_name
            self.uhid_device = self._UHIDDevice(
                name=name,
                report_descriptor=self.report_map,
                bus=self._Bus.BLUETOOTH,
                vendor=0,
                product=0,
            )
            log.success(f"UHID device created: {name}")
        except Exception as e:
            log.error(f"Failed to create UHID device: {e}")

    def _cleanup_uhid(self):
        """Clean up UHID device."""
        if self.uhid_device:
            try:
                self.uhid_device.destroy()
            except Exception:
                pass
            self.uhid_device = None

    def _is_connection_alive(self) -> bool:
        """Check if the connection is still alive and usable.

        Returns:
            True if connection exists and has a valid handle
        """
        if self.connection is None:
            return False
        if not hasattr(self.connection, 'handle') or self.connection.handle is None:
            return False
        if hasattr(self.connection, 'is_disconnected') and self.connection.is_disconnected:
            return False
        return True

    @abstractmethod
    async def run(self, address: str):
        """Connect to device and forward HID reports.

        Args:
            address: Device address to connect to (or first from config if None)
        """
        pass

    @abstractmethod
    async def cleanup(self):
        """Clean up all resources."""
        pass
