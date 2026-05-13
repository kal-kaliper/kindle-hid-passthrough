# KOReader Plugin: HID Passthrough

KOReader plugin that lets users start/stop the kindle-hid-passthrough Bluetooth HID daemon from within KOReader, and auto-detects connected keyboards via polling-based device enumeration.

Originally created by [@alllexx88](https://github.com/alllexx88) (see [issue #40](https://github.com/zampierilucas/kindle-hid-passthrough/issues/40)).

![Plugin menu in KOReader](screenshots/menu.png)

## Features

Full feature parity with the BTManager WAF app — you can manage everything from inside KOReader, no need to exit.

- Adds a "HID Passthrough" entry under Settings > Network
- **Daemon control**: start / stop / toggle the HID daemon (also bindable to gestures via Dispatcher actions)
- **Scan for devices**: discovers nearby BLE and Classic HID devices, with live-updating results menu
- **Paired devices**: list paired devices with connect / disconnect / remove (forget) actions
- **Recent logs**: in-app log viewer with refresh, useful for debugging pairing issues
- **Clear descriptor cache**: drop cached HID descriptors
- **Daemon status**: version, configured devices, connected device, scanning / pairing flags
- **Keyboard auto-attach**: BLE keyboards are wired into KOReader's input layer at runtime without restarting KOReader

## Installation

Copy the `hidpassthrough.koplugin` directory to your KOReader plugins folder:

```
cp -r hidpassthrough.koplugin /mnt/us/koreader/plugins/
```

Then restart KOReader.

The kindle-hid-passthrough daemon must already be installed on the device at `/mnt/us/kindle_hid_passthrough/kindle-hid-passthrough`. See the main project README for installation instructions.

## Opening the menu

In KOReader, tap the top of the screen to bring up the menu bar, then:

**cog icon (Settings) → Network → HID Passthrough**

The sub-menu shows the daemon toggle, scan, paired devices, logs, and cache controls (see screenshot above). Long-pressing the "HID Passthrough" parent entry toggles the daemon without descending into the sub-menu.

## Upstream KOReader PRs

The uevent-based auto-detection / polling logic in this plugin works around limitations in KOReader's current input handling on Kindle. There are upstream PRs that would make this unnecessary once merged:

- [koreader/koreader-base#2327](https://github.com/koreader/koreader-base/pull/2327)
- [koreader/koreader#15248](https://github.com/koreader/koreader/pull/15248)

Once those land, the keyboard wiring / polling sections of the plugin can be removed, and KOReader will natively handle uevent-based keyboard hot-plug on Kindle.
