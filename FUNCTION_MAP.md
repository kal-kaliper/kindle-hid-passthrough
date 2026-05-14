# Function Map — kindle_hid_passthrough

Every function in the project, one-line description each.

## api_server.py — HTTP API layer

- `APIServer.server_bind` — Bind socket without FQDN lookup (Kindle lacks idna codec)
- `RequestHandler.log_message` — Suppress default stderr logging
- `RequestHandler._send_json` — Serialize dict as JSON response with CORS headers
- `RequestHandler.do_GET` — Route GET requests to handler methods by path
- `RequestHandler.param` — Extract single query parameter value by name
- `RequestHandler._controller` — Property: get DaemonController from server instance
- `RequestHandler._handle_status` — Return full daemon status including devices, connection, version
- `RequestHandler._handle_start` — Resume daemon if suspended
- `RequestHandler._handle_stop` — Suspend daemon entirely
- `RequestHandler._handle_devices` — Return configured device list
- `RequestHandler._handle_remove` — Remove a device via controller, return result
- `RequestHandler._handle_clear_cache` — Delete descriptor cache files (not pairing keys)
- `RequestHandler._handle_scan` — Start BT scan via controller
- `RequestHandler._handle_scan_status` — Return live scan results or final scan result
- `RequestHandler._handle_pair` — Start pairing with a device via controller
- `RequestHandler._handle_pair_status` — Return pairing progress or final result
- `RequestHandler._handle_connect` — Connect to a specific device via controller
- `RequestHandler._handle_disconnect` — Drop active BT connection
- `RequestHandler._handle_logs` — Tail log file with formatting for small screens

## bt_setup.py — Bluetooth hardware preparation

- `_run` — Run a shell command, return stdout or None on failure
- `_find_bt_module` — Search for a kernel module .ko file by name patterns
- `_is_module_loaded` — Check if a kernel module is currently loaded
- `_free_device` — Kill any process holding a device path using fuser
- `_is_device_free` — Check if a device path has no holders
- `prepare_bt` — Full BT hardware init: load module, free device, wait for settle

## config.py — Configuration and device management

- `_get_git_sha` — Get short git SHA from repo (dev only)
- `_get_build_sha` — Read BUILD_SHA file (dev deploy)
- `get_version` — Return version string with SHA suffix if available
- `normalize_addr` — Strip /P suffix and uppercase a BT address
- `Config.__new__` — Singleton constructor
- `Config.__init__` — Load config on first instantiation
- `Config._determine_base_path` — Set base path from env or fallback to /mnt/us
- `Config._load` — Parse config.ini, set all config attributes
- `Config._detect_transport` — Auto-detect HCI transport from Kindle hardware
- `Config._parse_protocol` — Convert protocol string to Protocol enum
- `Config._get` — Read string from config.ini with fallback
- `Config._get_list` — Read comma-separated list from config.ini with fallback
- `Config._getint` — Read int from config.ini with fallback
- `Config.validate_keystore` — Check pairing_keys.json integrity, backup if corrupt
- `Config.remove_pairing_key` — Delete a pairing key by normalized address
- `Config.remove_device` — Remove device from devices.conf and its pairing key
- `Config.add_device` — Append device to devices.conf (skip duplicates)
- `Config.get_all_devices` — Parse devices.conf into list of (address, protocol, name)
- `get_fallback_hid_descriptor` — Return generic gamepad HID report descriptor bytes

## controller.py — Thread-safe bridge between HTTP and async daemon

- `DaemonController.__init__` — Init scan/pair state, device cache, op lock
- `DaemonController.get_status` — Build full status dict: daemon state, devices, connection
- `DaemonController._get_devices_cached` — Return device list from devices.conf, mtime-cached
- `DaemonController.request_scan` — Schedule BT scan on event loop from HTTP thread
- `DaemonController._on_device_found` — Callback: append discovered device to live list
- `DaemonController._do_scan` — Async: suspend daemon, run scan, collect results, resume
- `DaemonController.request_pair` — Schedule pairing on event loop from HTTP thread
- `DaemonController._do_pair` — Async: suspend, pair via daemon, save to config, resume
- `DaemonController.request_connect` — Resume daemon (no args) or add device and restart (with args)
- `DaemonController._do_connect` — Async: suspend, add device to config, resume
- `DaemonController.request_remove` — Remove device from config, clear cache, disconnect
- `DaemonController.request_disconnect` — Drop connection (default) or suspend daemon (suspend=True)
- `DaemonController._do_disconnect` — Async: call daemon.suspend() or daemon.disconnect()

## daemon.py — Persistent connection manager with reconnect loop

- `HIDDaemon.__init__` — Init running state, host reference, suspend event
- `HIDDaemon.connection_state` — Property: delegate to host.connection_state or return disconnected
- `HIDDaemon.suspend` — Cancel host task, cleanup host, set suspended flag
- `HIDDaemon.scan` — Create Scanner, run scan with callback, cleanup
- `HIDDaemon.pair` — Create HIDHost, pair device, hand off host on success
- `HIDDaemon.disconnect` — Drop active BT connection, cancel host task
- `HIDDaemon.resume` — Clear suspended flag, wake run loop
- `HIDDaemon._has_devices` — Check if devices.conf has entries, optionally log them
- `HIDDaemon.run` — Main loop: wait for devices, connect, handle auth failures, reconnect
- `HIDDaemon.stop` — Set running=False, wake any waiting events, cleanup host
- `main` — Entry point: init BT hardware, create daemon/controller/API server, handle signals

## device_cache.py — Per-device descriptor cache (JSON files)

- `DeviceCache.__init__` — Set cache directory path
- `DeviceCache._get_cache_path` — Map BT address to JSON file path
- `DeviceCache.load` — Read cached descriptor data for an address
- `DeviceCache.save` — Write descriptor data to cache file
- `DeviceCache.clear` — Delete cache file(s) by address or all

## host.py — BLE + Classic HID host on a single Bumble device

- `AutoAcceptPairingDelegate.__init__` — Set IO capability for pairing
- `AutoAcceptPairingDelegate.accept` — Auto-accept all pairing requests
- `AutoAcceptPairingDelegate.compare_numbers` — Auto-confirm numeric comparison
- `AutoAcceptPairingDelegate.get_number` — Return 0 for passkey entry
- `AutoAcceptPairingDelegate.display_number` — Log displayed PIN
- `create_pairing_config` — Build PairingConfig with secure defaults
- `create_keystore` — Create Bumble JsonKeyStore for pairing keys
- `HIDHost.__init__` — Init transport, device state, protocol lists, UHID, events
- `HIDHost.connection_state` — Property: build dict of address, protocol, UHID info
- `HIDHost._parse_devices` — Read devices.conf and split into classic/ble lists
- `HIDHost.start` — Create Bumble device via shared init, configure protocols and keystore
- `HIDHost._load_keystore_addresses` — Read known addresses from keystore for filtering
- `HIDHost._format_device` — Format address with device name if known
- `HIDHost.run` — Parse devices, start, race Classic/BLE handlers, wait for disconnect
- `HIDHost.pair_device` — Set up single-device config and delegate to _pair_ble or _pair_classic
- `HIDHost._pair_ble` — Connect BLE, initiate pairing, discover HID service
- `HIDHost._discover_ble_hid_service` — Walk GATT to find report map and report characteristics
- `HIDHost._pair_classic` — Connect Classic, authenticate, exchange link key, query SDP
- `HIDHost._query_classic_sdp` — SDP query for HID descriptor, parse and cache result
- `HIDHost.continue_after_pairing` — Enter run mode using connection from pair_device
- `HIDHost._continue_classic_after_pairing` — Create HID host, connect L2CAP channels, create UHID
- `HIDHost._continue_ble_after_pairing` — Reconnect if needed, restore bonding, setup BLE HID
- `HIDHost._run_classic_handler` — Page scan + active connect loop for Classic devices
- `HIDHost._is_classic_allowed` — Check if Classic address is in devices.conf or keystore
- `HIDHost._classic_active_connect_loop` — Actively try connecting to each Classic address
- `HIDHost._finalize_classic_hid` — Apply fallback descriptor if needed, create UHID
- `HIDHost._handle_classic_connection` — Load cached descriptor or SDP, finalize Classic HID
- `HIDHost._parse_hid_descriptor_list` — Parse HID Descriptor List from SDP attribute data
- `HIDHost._forward_report` — Deduplicate, log, and send HID report to UHID
- `HIDHost._on_classic_interrupt_data` — Strip header byte from Classic report, forward
- `HIDHost._on_virtual_cable_unplug` — Handle virtual cable unplug, signal disconnection
- `HIDHost._run_ble_handler` — Dispatch to accept-list or scan-based BLE handler
- `HIDHost._run_ble_accept_list_handler` — Use HCI filter accept list to wait for known BLE devices
- `HIDHost._run_ble_scan_handler` — Active BLE scanning fallback for wildcard devices
- `HIDHost._ble_restore_or_pair` — Restore BLE bonding from keystore or initiate new pairing
- `HIDHost._setup_ble_hid` — Discover reports, create UHID, subscribe, activate HID service
- `HIDHost._handle_ble_connection` — Load cached descriptor, setup BLE HID
- `HIDHost._read_ble_device_name` — Read device name from Generic Access GATT service
- `HIDHost._process_ble_report_char` — Read Report Reference descriptor, store input reports
- `HIDHost._subscribe_to_ble_reports` — Subscribe to GATT notifications for each input report
- `HIDHost._ble_activate_hid_service` — Write Exit Suspend to HID Control Point, read Protocol Mode
- `HIDHost._on_ble_hid_report` — Prepend report ID to BLE notification, forward
- `HIDHost._on_disconnection` — Log disconnect reason, flag auth failure if reason=5
- `HIDHost._load_cached_descriptor` — Load report_map and device_name from cache file
- `HIDHost._create_uhid_device` — Create UHID virtual device from report descriptor
- `HIDHost._is_connection_alive` — Check if Bumble connection handle is still valid
- `HIDHost.cleanup` — Cancel tasks, destroy UHID, close L2CAP/connection/transport
- `HIDHost.get_auth_failure_address` — Return and clear address that had auth failure

## kindle_detect.py — Kindle model detection from serial number

- `KindleDefaults` — Dataclass: device_path, kernel_module, model_name, transport_scheme, baud_rate, firmware_dir
- `_decode_device_code` — Extract integer device code from Kindle serial (B-hex or G-base32)
- `read_serial` — Read Kindle serial number from /proc/usid
- `detect_kindle` — Match serial to model, return hardware defaults or None

## logging_utils.py — Custom logging with color and file output

- `bumble_color` — No-op replacement for Bumble's color function
- `color` — Wrap text in ANSI color escape codes
- `HIDLogger.__init__` — Create logger with file handler
- `HIDLogger.set_console_output` — Toggle stderr output
- `HIDLogger._format_timestamp` — Format current time as HH:MM:SS
- `HIDLogger.info` — Log info message (optional highlight)
- `HIDLogger.success` — Log success message in green
- `HIDLogger.warning` — Log warning message in yellow
- `HIDLogger.error` — Log error message in red
- `HIDLogger.debug` — Log debug message in dim gray
- `HIDLogger.raw` — Log raw message without formatting
- `setup_logging` — Configure HIDLogger for interactive use
- `setup_daemon_logging` — Configure stdlib logging to file for daemon mode

## main.py — Interactive CLI entry point

- `main` — Parse args, init BT, connect to device, forward HID reports

## scanner.py — BLE + Classic device discovery

- `DiscoveredDevice.__str__` — Format as "[BLE/Classic] name (addr) RSSI: N"
- `Scanner.__init__` — Init transport spec and callback
- `Scanner.start` — Create Bumble device via shared transport init
- `Scanner.cleanup` — Close transport
- `Scanner.scan` — Run concurrent scan, fall back to sequential on failure
- `Scanner._scan_concurrent` — Run BLE and Classic scans as parallel tasks
- `Scanner._scan_sequential` — Run BLE then Classic scans, split duration
- `Scanner._interruptible_sleep` — Sleep with early exit on stop event
- `Scanner._scan_ble` — Listen for BLE advertisements, filter for HID service UUID
- `Scanner._scan_classic` — Run Classic inquiry, filter for Peripheral device class
- `Scanner._merge_results` — Combine BLE + Classic results, sort by RSSI

## transport.py — Bumble transport and device initialization

- `create_bumble_device` — Open HCI transport, create Device, HCI reset, power on

## uhid_handler.py — Linux UHID virtual device

- `strip_digitizer_collections` — Remove digitizer HID collections from descriptor bytes
- `Bus` — Enum: USB, BLUETOOTH bus types
- `UHIDDevice.__init__` — Open /dev/uhid, create virtual device with descriptor
- `UHIDDevice._open_uhid` — Open /dev/uhid file descriptor
- `UHIDDevice._create_device` — Write UHID_CREATE2 ioctl to register device
- `UHIDDevice.discover_input_paths` — Find /dev/input/eventN paths for this device
- `UHIDDevice.send_input` — Write HID input report to UHID fd
- `UHIDDevice.destroy` — Write UHID_DESTROY to remove virtual device
- `UHIDDevice.fd` — Property: return file descriptor
- `UHIDDevice.__enter__` — Context manager enter
- `UHIDDevice.__exit__` — Context manager exit (destroy)
- `UHIDDevice.__del__` — Destructor: destroy if still alive
