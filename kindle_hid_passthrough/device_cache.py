#!/usr/bin/env python3
"""Per-device cache (HID report descriptors, names) for fast reconnection."""

import json
import logging
import os
from typing import Dict, Optional

from config import normalize_addr

logger = logging.getLogger(__name__)


class DeviceCache:
    """Manages caching of device data for fast reconnection"""

    # Facts learned from a different source than the report map — inquiry
    # device class, SDP service attributes — and on their own schedule. A
    # descriptor re-cache passes only report_map/device_name, so without
    # carrying these across they would be dropped on every reconnect.
    STICKY_KEYS = ('is_phone', 'reconnect_initiate')

    def __init__(self, cache_dir: str):
        """Initialize cache manager

        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _get_cache_path(self, address: str) -> str:
        """Get cache file path for device address

        The address is normalized first (`normalize_addr` strips any Bumble
        transport suffix like "/P" and upper-cases), so a Classic address
        passed as "AA:BB:CC:11:22:33/P" (as `str(bd_addr)` yields on a real
        connection request) and the same address from devices.conf
        ("AA:BB:CC:11:22:33") resolve to ONE canonical cache file. Without
        this, writes and reads land in different files and phone-class /
        descriptor data silently fails to round-trip.

        Args:
            address: Device address (e.g., "AA:BB:CC:DD:EE:FF" or
                "AA:BB:CC:DD:EE:FF/P")

        Returns:
            Path to cache file
        """
        safe_addr = normalize_addr(address).replace(':', '_').replace('/', '_')
        return os.path.join(self.cache_dir, f"{safe_addr}.json")

    def load(self, address: str) -> Optional[Dict]:
        """Load cached data for device

        Args:
            address: Device address

        Returns:
            Cache dictionary if found and valid, None otherwise
        """
        cache_path = self._get_cache_path(address)
        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, 'r') as f:
                cache = json.load(f)

            # Validate cache structure - must have report_map
            if 'report_map' not in cache:
                logger.warning(f"Invalid cache structure for {address}")
                return None

            logger.info(f"Loaded device cache for {address}")
            return cache

        except Exception as e:
            logger.warning(f"Failed to load cache for {address}: {e}")
            return None

    def save(self, address: str, cache_data: Dict) -> bool:
        """Save device data to cache

        Args:
            address: Device address
            cache_data: Dictionary containing cache data

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            existing = None
            for key in self.STICKY_KEYS:
                if key in cache_data:
                    continue
                if existing is None:
                    existing = self._read_raw(address)
                if key in existing:
                    cache_data = {**cache_data, key: existing[key]}

            cache_path = self._get_cache_path(address)
            with open(cache_path, 'w') as f:
                json.dump(cache_data, f, indent=2)

            logger.info(f"Saved device cache for {address}")
            return True

        except Exception as e:
            logger.warning(f"Failed to save cache for {address}: {e}")
            return False

    def _read_raw(self, address: str) -> Dict:
        """Read the raw cache dict for a device (no report_map validation)."""
        cache_path = self._get_cache_path(address)
        if not os.path.exists(cache_path):
            return {}
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read cache for {address}: {e}")
            return {}

    def set_class(self, address: str, is_phone: bool) -> bool:
        """Record whether a device is a phone, merging into its cache entry.

        Persisted at discovery time (from the inquiry/advertisement device
        class), so reconnects — which never re-run inquiry — can still tell a
        phone keyboard from a dedicated one.
        """
        data = self._read_raw(address)
        if data.get('is_phone') == is_phone:
            return True
        data['is_phone'] = is_phone
        return self.save(address, data)

    def get_is_phone(self, address: str) -> Optional[bool]:
        """Return cached is_phone for a device, or None if unknown."""
        return self._read_raw(address).get('is_phone')

    def set_reconnect_initiate(self, address: str, value: bool) -> bool:
        """Record whether a device reconnects to the host on its own.

        Read from the HID SDP record (HIDReconnectInitiate, 0x0205), which is
        the device's own declaration of who re-establishes the link after an
        idle disconnect. Persisted because SDP is only queried during setup,
        while the dial decision has to be made when nothing is connected.
        """
        data = self._read_raw(address)
        if data.get('reconnect_initiate') == value:
            return True
        data['reconnect_initiate'] = value
        return self.save(address, data)

    def get_reconnect_initiate(self, address: str) -> Optional[bool]:
        """Return cached reconnect_initiate, or None if never read."""
        return self._read_raw(address).get('reconnect_initiate')

    def clear(self, address: Optional[str] = None) -> int:
        """Clear cache for specific device or all devices.

        Args:
            address: Device address, or None to clear all

        Returns:
            Number of cache files removed.
        """
        count = 0
        if address:
            cache_path = self._get_cache_path(address)
            try:
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    count = 1
                    logger.info(f"Cleared cache for {address}")
            except Exception as e:
                logger.warning(f"Failed to clear cache for {address}: {e}")
        else:
            try:
                filenames = os.listdir(self.cache_dir)
            except OSError:
                filenames = []
            for filename in filenames:
                if filename.endswith('.json') and filename != 'pairing_keys.json':
                    try:
                        os.remove(os.path.join(self.cache_dir, filename))
                        count += 1
                    except OSError as e:
                        logger.warning(f"Failed to clear {filename}: {e}")
            logger.info("Cleared all device caches")
        return count
