#!/usr/bin/env python3
"""
Classic Bluetooth HID Host - Pure UHID Passthrough

Bluetooth Classic (BR/EDR) HID host implementation using Google Bumble.
Uses passive mode: registers L2CAP servers and waits for device to connect.
Forwards all HID reports directly to Linux via UHID.

Author: Lucas Zampieri <lzampier@redhat.com>
"""

__version__ = "2.1.0"

import asyncio
import logging
from typing import Optional, List, Dict

from bumble.device import Device
from bumble.hci import (
    Address,
    HCI_Reset_Command,
    HCI_Write_Scan_Enable_Command,
    HCI_Write_Class_Of_Device_Command,
    HCI_Write_Local_Name_Command,
)
from bumble.transport import open_transport
from bumble.hid import (
    Host as HIDHost,
    Message,
    HID_CONTROL_PSM,
    HID_INTERRUPT_PSM,
)
from bumble.core import (
    DeviceClass,
    BT_BR_EDR_TRANSPORT,
    BT_HUMAN_INTERFACE_DEVICE_SERVICE,
    InvalidStateError,
    ProtocolError,
)
from bumble.sdp import (
    Client as SDPClient,
    SDP_SERVICE_RECORD_HANDLE_ATTRIBUTE_ID,
    SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID,
    SDP_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID,
)

from config import config
from logging_utils import log
from pairing import create_pairing_config, create_keystore
from device_cache import DeviceCache
from state_machine import StateMachine, HostState

__all__ = ['ClassicHIDHost', '__version__']


class ConnectionPolicy:
    """Unified connection policy - eliminates active/passive race condition.

    Uses asyncio.Future as single source of truth:
    - Passive path (page scan) sets the future via connection event
    - Active path checks future.done() before each attempt
    - No window for race condition
    """

    def __init__(self, device: Device, state_machine: StateMachine):
        self.device = device
        self.state_machine = state_machine
        self._connection_future: Optional[asyncio.Future] = None

    async def wait_for_connection(
        self,
        allowed_addresses: List[str],
        connection_handler,
        timeout: float = 60.0,
        retry_interval: float = 5.0
    ):
        """Wait for connection using coordinated active+passive approach.

        Args:
            allowed_addresses: List of allowed device addresses (* = wildcard)
            connection_handler: Async callback for handling connections
            timeout: Total timeout for connection
            retry_interval: Seconds between active connection retry rounds

        Returns:
            Connection object

        Raises:
            InvalidStateError: If no connection within timeout
        """
        # Create future that will hold the connection
        self._connection_future = asyncio.get_event_loop().create_future()

        # Register connection handler (passive path)
        async def on_passive_connection(connection):
            addr_str = str(connection.peer_address)

            # Check if device is allowed
            allowed, _ = config.is_device_allowed(addr_str)
            if not allowed:
                log.warning(f"Ignoring {addr_str} (not in devices.conf)")
                return

            # Handle the connection
            await connection_handler(connection)

            # Set future result (should already be set by sync handler, but ensure)
            if not self._connection_future.done():
                self._connection_future.set_result(connection)

        def on_connection_sync(connection):
            addr_str = str(connection.peer_address)
            log.info(f"Passive connection from: {addr_str}")

            # Set future IMMEDIATELY to stop active connection attempts
            # (before async handling runs)
            allowed, _ = config.is_device_allowed(addr_str)
            if allowed and not self._connection_future.done():
                self._connection_future.set_result(connection)

            asyncio.create_task(on_passive_connection(connection))

        self.device.on('connection', on_connection_sync)

        # Start active connection task if we have specific addresses
        active_task = None
        specific_addresses = [a for a in allowed_addresses if a != '*']
        if specific_addresses:
            active_task = asyncio.create_task(
                self._try_active_connects(
                    specific_addresses,
                    connection_handler,
                    retry_interval
                )
            )

        try:
            # Wait for either passive or active to succeed
            connection = await asyncio.wait_for(
                self._connection_future,
                timeout=timeout
            )
            return connection

        except asyncio.TimeoutError:
            log.warning("Connection timeout - no device connected")
            raise InvalidStateError("No device connected within timeout")

        finally:
            # Clean up
            if active_task:
                active_task.cancel()
                try:
                    await active_task
                except asyncio.CancelledError:
                    pass
            self.device.remove_listener('connection', on_connection_sync)

    async def _try_active_connects(
        self,
        addresses: List[str],
        connection_handler,
        retry_interval: float
    ):
        """Try active connects, checking future before each attempt."""
        while not self._connection_future.done():
            for addr in addresses:
                # Check if someone else (passive) got a connection
                if self._connection_future.done():
                    return

                try:
                    log.info(f"Trying active connection to {addr}...")
                    target = Address(addr, Address.PUBLIC_DEVICE_ADDRESS)
                    connection = await asyncio.wait_for(
                        self.device.connect(target, transport=BT_BR_EDR_TRANSPORT),
                        timeout=5.0
                    )

                    # Connection successful
                    log.success(f"Active connection to {addr} successful")

                    # Handle the connection
                    await connection_handler(connection)

                    # Set future if not already done
                    if not self._connection_future.done():
                        self._connection_future.set_result(connection)
                    return

                except asyncio.TimeoutError:
                    log.info(f"  {addr} not responding")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.info(f"  {addr} connection failed: {e}")

            # All devices tried, wait before retrying
            if not self._connection_future.done():
                log.info(f"Retrying active connection in {retry_interval}s...")
                await asyncio.sleep(retry_interval)


class ClassicHIDHost:
    """Classic Bluetooth HID Host with pure UHID passthrough.

    This host operates in passive mode:
    1. Registers L2CAP servers on HID PSMs (Control: 0x11, Interrupt: 0x13)
    2. Enables Page Scan to accept incoming connections
    3. Waits for HID device to connect
    4. Queries SDP for report descriptor (or uses cache)
    5. Creates UHID device and forwards all reports

    The device initiates all connections - this is how Classic BT HID works.

    Uses Bumble's HID Host class which handles:
    - L2CAP channel management (control + interrupt)
    - HID protocol messages (GET_REPORT, SET_REPORT, etc.)
    - Protocol mode switching (boot vs report)
    - Suspend/resume
    """

    PROTOCOL_NAME = "Classic"

    def __init__(self, transport_spec: str = None):
        """Initialize Classic Bluetooth HID Host.

        Args:
            transport_spec: HCI transport (default: from config)
        """
        self.transport_spec = transport_spec or config.transport
        self.transport = None
        self.device = None
        self.connection = None
        self.hid_host = None

        # State machine
        self.state_machine = StateMachine()

        # State
        self.current_device_address = None
        self.device_name = None
        self.report_map: Optional[bytes] = None

        # Components
        self.keystore = create_keystore(config.pairing_keys_file)
        self.device_cache = DeviceCache(config.cache_dir)

        # UHID
        self.uhid_device = None
        self._uhid_available = False
        try:
            from uhid_handler import UHIDDevice, Bus, UHIDError
            self._UHIDDevice = UHIDDevice
            self._Bus = Bus
            self._UHIDError = UHIDError
            self._uhid_available = True
        except ImportError:
            log.warning("UHID support not available")

        # Events
        self._disconnection_event = None
        self._handshake_complete_event = None
        self._last_report = None
        self._auth_failure_address = None  # Track address for auth failure retry

    @property
    def state(self) -> HostState:
        """Current host state."""
        return self.state_machine.state

    async def start(self):
        """Initialize the Bumble device and Classic Bluetooth stack."""
        self.state_machine.transition(HostState.STARTING)

        log.info(f"Classic HID Host v{__version__}")
        log.info("Opening transport...")

        try:
            self.transport = await asyncio.wait_for(
                open_transport(self.transport_spec),
                timeout=config.transport_timeout
            )
        except asyncio.TimeoutError:
            log.error(f"Transport open timed out after {config.transport_timeout}s")
            self.state_machine.transition(HostState.ERROR, "Transport timeout")
            raise

        self.device = Device.with_hci(
            config.device_name,
            config.device_address,
            self.transport.source,
            self.transport.sink
        )

        self.device.keystore = self.keystore
        self.device.pairing_config_factory = lambda conn: create_pairing_config()

        log.info("Sending HCI Reset...")
        try:
            await asyncio.wait_for(
                self.device.host.send_command(HCI_Reset_Command()),
                timeout=config.hci_reset_timeout
            )
            log.success("HCI Reset successful")
            await asyncio.sleep(0.2)
        except asyncio.TimeoutError:
            log.error(f"HCI Reset timed out")
            self.state_machine.transition(HostState.ERROR, "HCI reset timeout")
            raise

        self.device.classic_enabled = True
        self.device.le_enabled = False
        self.device.classic_ssp_enabled = True  # Enable Secure Simple Pairing
        self.device.classic_sc_enabled = True   # Enable Secure Connections

        await self.device.power_on()
        log.success(f"Device powered on: {self.device.public_address}")

        # Set Class of Device to Computer (Desktop)
        class_of_device = 0x000104  # Computer/Desktop
        await self.device.host.send_command(
            HCI_Write_Class_Of_Device_Command(class_of_device=class_of_device),
            check_result=True
        )
        log.info(f"Set Class of Device: 0x{class_of_device:06X} (Computer/Desktop)")

        # Set local name
        local_name_bytes = config.device_name.encode('utf-8') + b'\x00'
        await self.device.host.send_command(
            HCI_Write_Local_Name_Command(local_name=local_name_bytes),
            check_result=True
        )
        log.info(f"Set local name: {config.device_name}")

        # Debug: verify keystore
        if self.keystore:
            try:
                all_keys = await self.keystore.get_all()
                log.info(f"Keystore has {len(all_keys) if all_keys else 0} entries")
                if all_keys:
                    for addr, keys in all_keys:
                        log.info(f"  Key for: {addr}")
                        if keys.link_key:
                            log.info(f"    link_key: {keys.link_key.value.hex()}")
            except Exception as e:
                log.warning(f"Keystore check failed: {e}")

        # Override get_link_key to add debug logging
        original_get_link_key = self.device.get_link_key
        async def debug_get_link_key(address):
            log.info(f"Link key requested for: {address} (type: {type(address)})")
            result = await original_get_link_key(address)
            if result:
                log.info(f"Link key result: found, {len(result)} bytes: {result.hex()}")
            else:
                log.info(f"Link key result: NOT FOUND")
            return result
        self.device.host.link_key_provider = debug_get_link_key

    async def scan(self, duration: float = 10.0) -> List[Dict]:
        """Scan for Classic Bluetooth HID devices.

        Args:
            duration: Scan duration in seconds

        Returns:
            List of HID device dicts with address, name, rssi
        """
        self.state_machine.transition(HostState.SCANNING)

        log.info(f"Scanning for Classic BT devices ({duration}s)...")

        devices_found = []
        seen_addresses = set()

        def on_inquiry_result(address, class_of_device, eir_data, rssi):
            addr_str = str(address)
            if addr_str in seen_addresses:
                return
            seen_addresses.add(addr_str)

            # Check if HID device (Peripheral class)
            is_hid = False
            try:
                _, major_class, _ = DeviceClass.split_class_of_device(class_of_device)
                is_hid = DeviceClass.major_device_class_name(major_class) == "Peripheral"
            except Exception:
                major_class = (class_of_device >> 8) & 0x1F
                is_hid = (major_class == 0x05)

            if is_hid:
                name = 'Unknown'
                if eir_data:
                    try:
                        name_data = eir_data.get(0x09) or eir_data.get(0x08)
                        if name_data:
                            name = name_data.decode('utf-8', errors='replace') if isinstance(name_data, bytes) else str(name_data)
                    except Exception:
                        pass

                devices_found.append({
                    'address': addr_str,
                    'name': name,
                    'rssi': rssi or -100,
                })
                log.info(f"  Found: {name} ({addr_str})")

        self.device.on('inquiry_result', on_inquiry_result)
        try:
            await self.device.start_discovery()
            await asyncio.sleep(duration)
            await self.device.stop_discovery()
        finally:
            self.device.remove_listener('inquiry_result', on_inquiry_result)

        # Get names for unknown devices
        for dev in devices_found:
            if dev['name'] == 'Unknown':
                try:
                    name = await asyncio.wait_for(
                        self.device.request_remote_name(Address(dev['address'], Address.PUBLIC_DEVICE_ADDRESS)),
                        timeout=3.0
                    )
                    if name:
                        dev['name'] = name
                except Exception:
                    pass

        log.success(f"Found {len(devices_found)} HID devices")
        self.state_machine.transition(HostState.IDLE)
        return devices_found

    async def pair_device(self, address: str) -> bool:
        """Pair with a device - scan, connect, and authenticate.

        Args:
            address: Device address to pair with (if None, scan first)

        Returns:
            True if pairing successful
        """
        # If no address provided, scan for devices first
        if not address:
            log.info("Scanning for HID devices...")
            devices = await self.scan(duration=10.0)
            if not devices:
                log.error("No HID devices found")
                return False

            address = devices[0]['address']
            log.info(f"Found device: {devices[0]['name']} ({address})")

        self.state_machine.transition(HostState.CONNECTING)
        log.info(f"Connecting to {address} for pairing...")

        # Connect to the device
        try:
            target_address = Address(address, Address.PUBLIC_DEVICE_ADDRESS)
            self.connection = await asyncio.wait_for(
                self.device.connect(target_address, transport=BT_BR_EDR_TRANSPORT),
                timeout=config.connect_timeout
            )
            log.success(f"Connected to {address}")
        except asyncio.TimeoutError:
            log.error(f"Connection timeout after {config.connect_timeout}s")
            self.state_machine.transition(HostState.ERROR, "Connection timeout")
            return False
        except Exception as e:
            log.error(f"Connection failed: {e}")
            self.state_machine.transition(HostState.ERROR, str(e))
            return False

        self.current_device_address = address
        self.state_machine.transition(HostState.AUTHENTICATING)

        # Track link key generation
        link_key_received = asyncio.Event()
        received_link_key = None

        def on_connection_link_key():
            nonlocal received_link_key
            log.success("Link key event received (connection)!")
            link_key_received.set()

        def on_device_link_key(bd_addr, link_key, key_type):
            nonlocal received_link_key
            log.success(f"Link key event received (device): {bd_addr}, type={key_type}")
            log.info(f"Link key value: {link_key.hex()}")
            received_link_key = link_key
            link_key_received.set()

        self.connection.on('link_key', on_connection_link_key)
        self.device.host.on('link_key', on_device_link_key)

        try:
            # Authenticate (triggers SSP pairing if no link key)
            log.info("Authenticating...")
            try:
                await asyncio.wait_for(
                    self.connection.authenticate(),
                    timeout=30.0
                )
                log.success("Authentication complete")
            except Exception as e:
                log.warning(f"Authentication: {e}")

            # Wait for link key
            log.info("Waiting for link key...")
            try:
                await asyncio.wait_for(link_key_received.wait(), timeout=5.0)
                log.success("Link key received and saved")
            except asyncio.TimeoutError:
                log.warning("Link key event timeout (may already be saved)")

            # Request encryption
            log.info("Requesting encryption...")
            try:
                await asyncio.wait_for(
                    self.connection.encrypt(enable=True),
                    timeout=10.0
                )
                log.success("Encryption request sent")
            except Exception as e:
                log.warning(f"Encryption request: {e}")

            # Wait for encryption
            log.info("Waiting for encryption...")
            encryption_done = asyncio.Event()

            def on_encryption_change():
                if self.connection.is_encrypted:
                    log.success("Encryption enabled!")
                    encryption_done.set()

            def on_encryption_failure(error):
                log.error(f"Encryption failed: {error}")
                encryption_done.set()

            self.connection.on('connection_encryption_change', on_encryption_change)
            self.connection.on('connection_encryption_failure', on_encryption_failure)

            try:
                await asyncio.wait_for(encryption_done.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                log.warning("Encryption event timeout")

            if self.connection.is_encrypted:
                log.success("Connection encrypted!")
            else:
                log.warning("Connection not encrypted")

            self.state_machine.transition(HostState.DISCOVERING_SERVICES)

            # Query SDP for report descriptor
            await self._query_and_cache_descriptor(self.current_device_address)

            # Give time for key persistence
            log.info("Waiting for key persistence...")
            await asyncio.sleep(1.0)

            # Verify link key was saved
            if self.keystore:
                keys = await self.keystore.get(address)
                if keys and keys.link_key:
                    log.success(f"Link key verified: {keys.link_key.value.hex()}")
                else:
                    log.warning("Link key not found in keystore!")

            self.state_machine.transition(HostState.IDLE)
            return True

        except Exception as e:
            log.error(f"Pairing failed: {e}")
            self.state_machine.transition(HostState.ERROR, str(e))
            return False
        finally:
            try:
                self.connection.remove_listener('link_key', on_connection_link_key)
                self.device.host.remove_listener('link_key', on_device_link_key)
            except Exception:
                pass
            if self.connection:
                try:
                    await self.connection.disconnect()
                except Exception:
                    pass
                self.connection = None

    async def _query_and_cache_descriptor(self, address: str):
        """Query SDP for HID descriptor and cache it."""
        if not self.connection:
            log.warning("No connection for SDP query")
            return

        log.info("Querying SDP for HID descriptor...")

        try:
            sdp_client = SDPClient(self.connection)
            await asyncio.wait_for(sdp_client.connect(), timeout=5.0)
            log.info("SDP client connected")

            try:
                # Try focused query first
                hid_attrs = [0x0100, 0x0206]

                log.info("Searching for HID service (focused query)...")
                try:
                    result = await asyncio.wait_for(
                        sdp_client.search_attributes(
                            [BT_HUMAN_INTERFACE_DEVICE_SERVICE],
                            hid_attrs
                        ),
                        timeout=10.0
                    )
                except Exception as e:
                    log.warning(f"Focused query failed: {e}, trying broad query...")
                    result = await asyncio.wait_for(
                        sdp_client.search_attributes(
                            [BT_HUMAN_INTERFACE_DEVICE_SERVICE],
                            [0x0000, 0x0001, 0x0004, 0x0100, 0x0200, 0x0201, 0x0202,
                             0x0203, 0x0204, 0x0205, 0x0206, 0x0207, 0x020E]
                        ),
                        timeout=10.0
                    )

                if not result:
                    log.warning("No HID service found in SDP")
                else:
                    log.info(f"Found {len(result)} SDP record(s)")
                    for i, record in enumerate(result):
                        if not record:
                            continue
                        log.info(f"Record {i}: {len(record)} attributes")
                        for attr in record:
                            try:
                                attr_id = attr.id if hasattr(attr, 'id') else None
                                if attr_id is None:
                                    continue
                                log.info(f"  Attr 0x{attr_id:04X}: {type(attr.value).__name__}")
                                if attr_id == 0x0206:
                                    log.info(f"  Found HIDDescriptorList!")
                                    self._parse_hid_descriptor_list(attr.value)
                                elif attr_id == 0x0100:
                                    try:
                                        if hasattr(attr.value, 'value'):
                                            name = attr.value.value
                                            if isinstance(name, bytes):
                                                name = name.decode('utf-8', errors='replace')
                                            log.info(f"  Service name: {name}")
                                            self.device_name = str(name)
                                    except Exception:
                                        pass
                            except Exception as attr_err:
                                log.warning(f"  Error parsing attribute: {attr_err}")

                if self.report_map:
                    self.device_cache.save(address, {
                        'report_map': self.report_map.hex(),
                        'device_name': self.device_name or 'Unknown'
                    })
                    log.success(f"Cached report descriptor ({len(self.report_map)} bytes)")
                else:
                    log.warning("No report descriptor found in SDP")
            finally:
                await sdp_client.disconnect()
        except asyncio.TimeoutError:
            log.warning("SDP connection timeout")
        except Exception as e:
            log.warning(f"SDP query failed: {e}")
            import traceback
            log.debug(traceback.format_exc())

    def _parse_hid_descriptor_list(self, data_element):
        """Parse HID Descriptor List from SDP."""
        log.info(f"Parsing HIDDescriptorList: {type(data_element).__name__}")

        try:
            if hasattr(data_element, 'value'):
                data_element = data_element.value

            log.info(f"  Inner type: {type(data_element).__name__}, len={len(data_element) if hasattr(data_element, '__len__') else 'N/A'}")

            if isinstance(data_element, (list, tuple)):
                for i, descriptor in enumerate(data_element):
                    log.info(f"  Descriptor {i}: {type(descriptor).__name__}")

                    if hasattr(descriptor, 'value'):
                        descriptor = descriptor.value

                    if isinstance(descriptor, (list, tuple)) and len(descriptor) >= 2:
                        desc_type = descriptor[0]
                        desc_data = descriptor[1]

                        if hasattr(desc_type, 'value'):
                            desc_type = desc_type.value

                        log.info(f"    Type: 0x{desc_type:02X}" if isinstance(desc_type, int) else f"    Type: {desc_type}")

                        if desc_type == 0x22:  # Report Descriptor
                            if hasattr(desc_data, 'value'):
                                desc_data = desc_data.value

                            if isinstance(desc_data, bytes):
                                self.report_map = desc_data
                            elif isinstance(desc_data, (list, tuple)):
                                self.report_map = bytes(desc_data)
                            else:
                                log.warning(f"    Unexpected data type: {type(desc_data)}")
                                continue

                            log.success(f"Got report descriptor: {len(self.report_map)} bytes")
                            log.info(f"    First 32 bytes: {self.report_map[:32].hex()}")
                            return
                    else:
                        log.warning(f"    Unexpected descriptor format: {descriptor}")
            else:
                log.warning(f"  Unexpected format: {data_element}")

        except Exception as e:
            log.warning(f"Failed to parse HID descriptor: {e}")
            import traceback
            log.debug(traceback.format_exc())

    async def run(self, target_address: str):
        """Main run loop - wait for device and forward reports.

        Args:
            target_address: Expected device address (for filtering)
        """
        self._disconnection_event = asyncio.Event()
        self._handshake_complete_event = asyncio.Event()

        await self.start()

        # Load cached descriptor
        cache = self.device_cache.load(target_address)
        if cache and 'report_map' in cache:
            self.report_map = bytes.fromhex(cache['report_map'])
            log.success(f"Loaded cached descriptor ({len(self.report_map)} bytes)")

        # Create HID Host
        self.hid_host = HIDHost(self.device)
        self.hid_host.on(HIDHost.EVENT_INTERRUPT_DATA, self._on_interrupt_data)
        self.hid_host.on(HIDHost.EVENT_VIRTUAL_CABLE_UNPLUG, self._on_virtual_cable_unplug)
        self.hid_host.on(HIDHost.EVENT_CONTROL_DATA, self._on_control_data)
        self.hid_host.on(HIDHost.EVENT_HANDSHAKE, self._on_handshake)
        self.hid_host.on(HIDHost.EVENT_SUSPEND, lambda: log.info("[HID] Device suspended"))
        self.hid_host.on(HIDHost.EVENT_EXIT_SUSPEND, lambda: log.info("[HID] Device resumed"))
        log.info(f"HID Host created (Control PSM: 0x{HID_CONTROL_PSM:04X}, Interrupt PSM: 0x{HID_INTERRUPT_PSM:04X})")

        # Connection handler
        async def handle_connection(connection):
            addr_str = str(connection.peer_address)
            log.success(f"Connection established: {addr_str}")

            self.connection = connection
            self.current_device_address = addr_str

            # Set up disconnection handler
            connection.on('disconnection', self._on_disconnection)

            self.state_machine.transition(HostState.AUTHENTICATING)

            # Wait for device to authenticate us
            auth_event = asyncio.Event()
            def on_auth():
                log.success("Device authenticated us")
                auth_event.set()
            def on_auth_fail(error):
                log.warning(f"Auth failed: {error}")
                auth_event.set()

            connection.on('connection_authentication', on_auth)
            connection.on('connection_authentication_failure', on_auth_fail)

            log.info("Waiting for device authentication...")
            try:
                # Wait for either auth or disconnection
                auth_task = asyncio.create_task(auth_event.wait())
                disconnect_task = asyncio.create_task(self._disconnection_event.wait())
                done, pending = await asyncio.wait(
                    [auth_task, disconnect_task],
                    timeout=5.0,
                    return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()

                if self._disconnection_event.is_set():
                    log.warning("Device disconnected during authentication")
                    connection.remove_listener('connection_authentication', on_auth)
                    connection.remove_listener('connection_authentication_failure', on_auth_fail)
                    return  # Don't signal handshake complete
            except asyncio.TimeoutError:
                log.warning("No auth request from device, continuing...")

            # Check again if disconnected
            if self._disconnection_event.is_set():
                log.warning("Device disconnected, aborting handshake")
                return

            connection.remove_listener('connection_authentication', on_auth)
            connection.remove_listener('connection_authentication_failure', on_auth_fail)

            # Register with HID host
            self.hid_host.on_device_connection(connection)

            # Signal handshake complete
            self._handshake_complete_event.set()

        # Enable Page Scan
        log.info("Enabling Page Scan...")
        await self.device.host.send_command(
            HCI_Write_Scan_Enable_Command(scan_enable=0x02),
            check_result=True
        )

        # Get allowed addresses from devices.conf
        devices = config.get_all_devices()
        allowed_addresses = [addr for addr, _ in devices]

        # Also include addresses from keystore for active connection attempts
        # This helps when using wildcard (*) or when keystore has more devices
        if self.keystore:
            try:
                keystore_addresses = set()
                # Get all keys from keystore
                keys = await self.keystore.get_all()
                if keys:
                    for addr_str in keys.keys():
                        # Normalize address (remove /P suffix for comparison)
                        norm_addr = addr_str.split('/')[0].upper()
                        keystore_addresses.add(addr_str)
                    # Add keystore addresses not already in allowed list
                    for ks_addr in keystore_addresses:
                        norm_ks = ks_addr.split('/')[0].upper()
                        if not any(a.split('/')[0].upper() == norm_ks for a in allowed_addresses if a != '*'):
                            allowed_addresses.append(ks_addr)
                            log.info(f"Adding keystore address: {ks_addr}")
            except Exception as e:
                log.debug(f"Could not get keystore addresses: {e}")

        # Use ConnectionPolicy for race-free connection handling
        self.state_machine.transition(HostState.CONNECTING)
        policy = ConnectionPolicy(self.device, self.state_machine)

        log.info("Waiting for device (passive) + trying active connection...")
        await policy.wait_for_connection(
            allowed_addresses=allowed_addresses,
            connection_handler=handle_connection,
            timeout=60.0,
            retry_interval=5.0
        )

        # Wait for handshake to complete (or disconnection)
        log.info("Connection received, waiting for handshake...")
        try:
            handshake_task = asyncio.create_task(self._handshake_complete_event.wait())
            disconnect_task = asyncio.create_task(self._disconnection_event.wait())
            done, pending = await asyncio.wait(
                [handshake_task, disconnect_task],
                timeout=15.0,
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            if self._disconnection_event.is_set() and not self._handshake_complete_event.is_set():
                log.warning("Device disconnected during handshake (auth failure?)")
                self.state_machine.transition(HostState.IDLE)
                raise InvalidStateError("Device disconnected during handshake")
        except asyncio.TimeoutError:
            log.warning("Handshake timeout")
            self.state_machine.transition(HostState.ERROR, "Handshake timeout")
            raise InvalidStateError("Connection handshake failed")

        # Wait for L2CAP channels
        log.info("Waiting for HID channels...")
        for _ in range(50):
            if self.hid_host.l2cap_intr_channel:
                break
            await asyncio.sleep(0.1)

        if self.hid_host.l2cap_intr_channel:
            log.success("HID interrupt channel connected")
        else:
            self.state_machine.transition(HostState.ERROR, "HID channel not connected")
            raise InvalidStateError("HID channel not connected")

        if self.hid_host.l2cap_ctrl_channel:
            log.success("HID control channel connected")

        # If no cached descriptor, try SDP now
        if not self.report_map:
            self.state_machine.transition(HostState.DISCOVERING_SERVICES)
            await self._query_and_cache_descriptor(self.current_device_address)

        # Create UHID device
        if not self.report_map:
            log.warning("No report descriptor - using fallback")
            self.report_map = self._get_fallback_descriptor()

        self._create_uhid_device()

        self.state_machine.transition(HostState.CONNECTED)
        log.success(f"\n[Classic] Receiving HID reports. Press Ctrl+C to exit.")

        # Wait for disconnection
        await self._disconnection_event.wait()
        self.state_machine.transition(HostState.IDLE)

    def _on_disconnection(self, reason):
        """Handle device disconnection."""
        log.warning(f"Device disconnected (reason={reason})")

        # Reason 5 = HCI_AUTHENTICATION_FAILURE
        if reason == 5 and self.current_device_address:
            log.info("Authentication failure - clearing stale key immediately")
            self._auth_failure_address = self.current_device_address
            # Schedule async key cleanup
            asyncio.create_task(self._clear_stale_key_now())

        self._disconnection_event.set()

    async def _clear_stale_key_now(self):
        """Clear stale key immediately on auth failure."""
        if self._auth_failure_address:
            addr = self._auth_failure_address
            success = await self.clear_stale_key(addr)
            if success:
                log.success(f"Cleared stale key for {addr}")
            else:
                log.warning(f"Failed to clear key for {addr}")

    def _on_virtual_cable_unplug(self):
        """Handle virtual cable unplug."""
        log.warning("Virtual cable unplugged")
        self._disconnection_event.set()

    def _on_control_data(self, pdu: bytes):
        """Handle incoming HID control data."""
        log.info(f"[HID] Control data: {len(pdu)} bytes: {pdu.hex()}")

        if len(pdu) >= 1:
            msg_type = pdu[0] >> 4
            param = pdu[0] & 0x0F
            if msg_type == Message.MessageType.HANDSHAKE:
                log.info(f"[HID] Handshake: {Message.Handshake(param).name}")
            elif msg_type == Message.MessageType.DATA:
                report_type = param
                log.info(f"[HID] Data report type: {report_type}")

    def _on_handshake(self, result: Message.Handshake):
        """Handle HID handshake response."""
        log.info(f"[HID] Handshake result: {result.name}")

    # --- HID Host Protocol Methods ---

    def set_protocol_mode(self, boot_mode: bool = False):
        """Set HID protocol mode (boot or report)."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot set protocol: no control channel")
            return

        mode = Message.ProtocolMode.BOOT_PROTOCOL if boot_mode else Message.ProtocolMode.REPORT_PROTOCOL
        self.hid_host.set_protocol(mode)
        log.info(f"Set protocol mode: {'boot' if boot_mode else 'report'}")

    def get_protocol_mode(self):
        """Request current protocol mode from device."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot get protocol: no control channel")
            return

        self.hid_host.get_protocol()
        log.info("Requested protocol mode")

    def suspend_device(self):
        """Send suspend command to HID device."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot suspend: no control channel")
            return

        self.hid_host.suspend()
        log.info("Sent suspend command")

    def resume_device(self):
        """Send exit suspend command to HID device."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot resume: no control channel")
            return

        self.hid_host.exit_suspend()
        log.info("Sent exit suspend command")

    def send_output_report(self, data: bytes):
        """Send output report to HID device."""
        if not self.hid_host or not self.hid_host.l2cap_intr_channel:
            log.warning("Cannot send output: no interrupt channel")
            return

        self.hid_host.send_data(data)
        log.info(f"Sent output report: {data.hex()}")

    def get_report(self, report_type: int, report_id: int, buffer_size: int = 0):
        """Request a specific report from the device."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot get report: no control channel")
            return

        self.hid_host.get_report(report_type, report_id, buffer_size)
        log.info(f"Requested report type={report_type} id={report_id}")

    def set_report(self, report_type: int, data: bytes):
        """Send a SET_REPORT to the device."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot set report: no control channel")
            return

        self.hid_host.set_report(report_type, data)
        log.info(f"Set report type={report_type}: {data.hex()}")

    def virtual_cable_unplug(self):
        """Send virtual cable unplug to device."""
        if not self.hid_host or not self.hid_host.l2cap_ctrl_channel:
            log.warning("Cannot unplug: no control channel")
            return

        self.hid_host.virtual_cable_unplug()
        log.info("Sent virtual cable unplug")

    def _on_interrupt_data(self, pdu: bytes):
        """Handle incoming HID report."""
        if len(pdu) < 1:
            return

        # Skip header byte
        report_data = pdu[1:]

        # Log only when data changes
        if report_data != self._last_report:
            log.debug(f"[HID] Report: {report_data.hex()}")
            self._last_report = report_data

        # Forward to UHID
        if self.uhid_device:
            try:
                self.uhid_device.send_input(report_data)
            except Exception as e:
                log.warning(f"UHID send failed: {e}")

    def _create_uhid_device(self):
        """Create UHID virtual device."""
        if not self._uhid_available:
            log.warning("UHID not available")
            return

        if not self.report_map:
            log.warning("No report descriptor for UHID")
            return

        try:
            name = self.device_name or "Classic HID Device"
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

    def _get_fallback_descriptor(self) -> bytes:
        """Return a generic fallback HID report descriptor."""
        return bytes([
            0x05, 0x01, 0x09, 0x05, 0xa1, 0x01,
            0x85, 0x01,
            0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
            0x16, 0x00, 0x00, 0x26, 0xff, 0xff, 0x75, 0x10, 0x95, 0x04, 0x81, 0x02,
            0x05, 0x02, 0x09, 0xc5, 0x09, 0xc4,
            0x16, 0x00, 0x00, 0x26, 0xff, 0x03, 0x75, 0x10, 0x95, 0x02, 0x81, 0x02,
            0x05, 0x01, 0x09, 0x39,
            0x15, 0x01, 0x25, 0x08, 0x35, 0x00, 0x46, 0x3b, 0x01, 0x65, 0x14,
            0x75, 0x08, 0x95, 0x01, 0x81, 0x42,
            0x05, 0x09, 0x19, 0x01, 0x29, 0x10,
            0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02,
            0xc0,
            0x05, 0x06, 0x09, 0x20, 0xa1, 0x01,
            0x85, 0x04, 0x09, 0x20,
            0x15, 0x00, 0x26, 0xff, 0x00, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,
            0xc0,
        ])

    async def clear_stale_key(self, address: str) -> bool:
        """Clear a stale link key from the keystore."""
        if not self.keystore:
            return False

        try:
            keys = await self.keystore.get(address)
            if keys and keys.link_key:
                log.info(f"Clearing stale link key for {address}")
                await self.keystore.delete(address)
                log.success(f"Link key cleared for {address}")
                return True
            return False
        except Exception as e:
            log.warning(f"Failed to clear link key: {e}")
            return False

    def get_auth_failure_address(self) -> str:
        """Get address that had auth failure, if any."""
        addr = self._auth_failure_address
        self._auth_failure_address = None
        return addr

    async def cleanup(self):
        """Clean up resources."""
        # Only transition to DISCONNECTING from connected states
        if self.state_machine.state in (HostState.CONNECTED, HostState.AUTHENTICATING,
                                         HostState.DISCOVERING_SERVICES):
            self.state_machine.transition(HostState.DISCONNECTING)
        elif self.state_machine.state not in (HostState.IDLE, HostState.DISCONNECTING):
            self.state_machine.reset()

        if self.uhid_device:
            try:
                self.uhid_device.destroy()
            except Exception:
                pass
            self.uhid_device = None

        if self.hid_host:
            try:
                if self.hid_host.l2cap_intr_channel:
                    await self.hid_host.disconnect_interrupt_channel()
                if self.hid_host.l2cap_ctrl_channel:
                    await self.hid_host.disconnect_control_channel()
            except Exception:
                pass

        if self.connection:
            try:
                await self.connection.disconnect()
            except Exception:
                pass

        if self.transport:
            await self.transport.close()

        self.state_machine.reset()
