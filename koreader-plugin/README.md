# KOReader Plugin: HID Passthrough

KOReader plugin that lets users start/stop the kindle-hid-passthrough Bluetooth HID daemon from within KOReader, and auto-detects connected keyboards via polling-based device enumeration.

Originally created by [@alllexx88](https://github.com/alllexx88) (see [issue #40](https://github.com/zampierilucas/kindle-hid-passthrough/issues/40)).

## Features

- Adds a "HID Passthrough" entry under Settings > Network to toggle the daemon on/off
- Registers Dispatcher actions (start/stop/toggle) bindable to gestures, taps, or swipes
- Auto-attaches BLE keyboards to KOReader at runtime without restarting
- Shows daemon status (version, connected device, scanning/pairing state) via the HTTP API

## Installation

Copy the `hidpassthrough.koplugin` directory to your KOReader plugins folder:

```
cp -r hidpassthrough.koplugin /mnt/us/koreader/plugins/
```

Then restart KOReader. The entry will appear under the cog menu > Network.

The kindle-hid-passthrough daemon must already be installed on the device at `/mnt/us/kindle_hid_passthrough/kindle-hid-passthrough`. See the main project README for installation instructions.

## Upstream KOReader PRs

The uevent-based auto-detection / polling logic in this plugin works around limitations in KOReader's current input handling on Kindle. There are upstream PRs that would make this unnecessary once merged:

- [koreader/koreader-base#2327](https://github.com/koreader/koreader-base/pull/2327)
- [koreader/koreader#15248](https://github.com/koreader/koreader/pull/15248)

Once those land, the keyboard wiring / polling sections of the plugin can be removed, and KOReader will natively handle uevent-based keyboard hot-plug on Kindle.
