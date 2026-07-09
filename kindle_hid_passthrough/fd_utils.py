#!/usr/bin/env python3
"""Small helpers for working with this process's file descriptors."""

import os


def close_own_fds_for_path(device_path):
    """Close this process's own fds pointing at device_path. Returns count."""
    closed = 0
    try:
        entries = os.listdir('/proc/self/fd')
    except OSError:
        return 0
    for entry in entries:
        try:
            target = os.readlink(f'/proc/self/fd/{entry}')
        except OSError:
            continue
        if target != device_path:
            continue
        try:
            os.close(int(entry))
            closed += 1
        except OSError:
            pass
    return closed
