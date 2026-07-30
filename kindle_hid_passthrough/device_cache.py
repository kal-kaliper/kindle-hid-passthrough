#!/usr/bin/env python3
"""Per-device cache (HID report descriptors, names) for fast reconnection."""

import json
import logging
import os
import re
from typing import Dict, Optional

from config import normalize_addr

logger = logging.getLogger(__name__)

# Pre-normalization filenames came straight from the address, so they may carry a
# transport suffix (AA_BB_CC_DD_EE_FF_P.json for "AA:BB:CC:DD:EE:FF/P") and may be
# in either case, because the old key was built without uppercasing. Both forms
# have to be recognized, not just the suffixed one. The extension stays literal
# lowercase: the old code only ever wrote ".json", so a ".JSON" file is somebody
# else's, not a legacy cache entry.
_LEGACY_KEY_RE = re.compile(
    r'^([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})(?:_[PRpr])?\.json$')


class DeviceCache:
    """Manages caching of device data for fast reconnection"""

    def __init__(self, cache_dir: str):
        """Initialize cache manager

        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._migrate_legacy_keys()

    def _migrate_legacy_keys(self):
        """Fold pre-normalization cache files into their canonical name.

        Before the address was normalized, the transport suffix became part of
        the filename, so one device could hold both <addr>.json and
        <addr>_P.json. Without this, a suffixed file is simply orphaned: never
        read again, and not removed by a per-device clear() either, because that
        resolves to the canonical name.
        """
        try:
            names = os.listdir(self.cache_dir)
        except OSError:
            return
        for name in names:
            match = _LEGACY_KEY_RE.match(name)
            if not match:
                continue
            canonical_name = f"{match.group(1).upper()}.json"
            if name == canonical_name:
                continue          # already canonical, nothing to do
            legacy = os.path.join(self.cache_dir, name)
            canonical = os.path.join(self.cache_dir, canonical_name)
            try:
                if not os.path.exists(canonical):
                    os.rename(legacy, canonical)
                    logger.info(
                        f"Migrated cache file {name} -> {canonical_name}")
                elif os.path.samefile(legacy, canonical):
                    # Case-insensitive filesystem: the two names are one file, so
                    # there is nothing to migrate and nothing to delete. Without
                    # this the mtime comparison below would compare the file with
                    # itself and then remove it, destroying the entry.
                    continue
                elif os.path.getmtime(legacy) > os.path.getmtime(canonical):
                    # Both forms exist as separate files. Keep whichever was
                    # written last: it came from the most recent SDP query, so it
                    # is the descriptor the device most recently advertised.
                    os.replace(legacy, canonical)
                    logger.info(
                        f"Migrated newer cache file {name} over {canonical_name}")
                else:
                    os.remove(legacy)
                    logger.info(f"Removed superseded cache file {name}")
            except OSError as e:
                logger.warning(f"Could not migrate cache file {name}: {e}")

    def _get_cache_path(self, address: str) -> str:
        """Get cache file path for device address

        The address is normalized first, so the two forms of one address resolve
        to a single file. `str(connection.peer_address)` carries a transport
        suffix ("AA:BB:CC:DD:EE:FF/P"), and the Classic connection listener puts
        that in current_device_address, so the connection path reads and writes
        <addr>_P.json. The pairing flow and the startup descriptor check pass the
        plain devices.conf address instead, so they use <addr>.json. Keying on the
        raw string therefore gives one device two independent files: the copy
        written at pairing is never read by the connection path, and removing the
        device deletes only that copy, leaving the suffixed one to be served if
        the device is paired again.

        Args:
            address: Device address, with or without a transport suffix
                (e.g. "AA:BB:CC:DD:EE:FF" or "AA:BB:CC:DD:EE:FF/P")

        Returns:
            Path to cache file
        """
        safe_addr = normalize_addr(address).replace(':', '_')
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
            cache_path = self._get_cache_path(address)
            with open(cache_path, 'w') as f:
                json.dump(cache_data, f, indent=2)

            logger.info(f"Saved device cache for {address}")
            return True

        except Exception as e:
            logger.warning(f"Failed to save cache for {address}: {e}")
            return False

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

