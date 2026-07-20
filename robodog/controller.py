"""
Rapidpower Robot Dog BLE Controller

Original Python BLE controller reconstructed from reverse-engineering
the com.zhongrun.robotdog Android app for local control of operator's own device.

Connection flow reconstructed from MainActivity.java:733-740 and RxBleConnectionModule.
"""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any
from bleak import BleakScanner, BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic

from .protocol import encode, encode_feed, encode_legs, COMMANDS, FEED_COMMANDS

logger = logging.getLogger(__name__)


class RobodogBLE:
    """
    Asynchronous BLE controller for Rapidpower robot dog.

    Protocol reconstructed from com.zhongrun.robotdog app analysis:
    - Device name filter: "Rapidpower-dog" (FunctionActivity.java:106)
    - GATT discovery: dynamic property-based (MainActivity.java:733-740)
    - WRITE characteristic: property & 0x0C != 0
    - NOTIFY characteristic: property & 0x10 != 0
    - No authentication or pairing required
    """

    DEVICE_NAME_FILTER = "Rapidpower-dog"
    SCAN_TIMEOUT = 20.0  # seconds (from MainActivity.java:106)
    # The dog uses BLE privacy (randomized address) and often advertises no name, so we also
    # positively identify it by the custom GATT UUIDs observed on a confirmed connect.
    KNOWN_DOG_UUIDS = {
        "b02eaeaa-f6bc-4a7e-bc94-f7b7fc8ded0b",  # WRITE characteristic
        "10e2fde2-d7fe-4845-b3f3-a32010ebb095",  # NOTIFY characteristic
    }

    def __init__(self, simulate: bool = False):
        """
        Initialize BLE controller.

        Args:
            simulate: If True, runs in simulation mode without real BLE device
        """
        self.simulate = simulate
        self.client: Optional[BleakClient] = None
        self.write_char: Optional[BleakGATTCharacteristic] = None
        self.notify_char: Optional[BleakGATTCharacteristic] = None
        self.device_address: Optional[str] = None
        self._notify_callback: Optional[Callable] = None
        self._telemetry_data: bytearray = bytearray()

    async def scan(self, timeout: float = None, include_all: bool = False) -> list[Dict[str, Any]]:
        """
        Scan for Rapidpower-dog devices.

        Args:
            timeout: Scan duration in seconds (default: SCAN_TIMEOUT)
            include_all: If True, return EVERY advertising BLE device (unfiltered),
                         so the operator can identify the dog by whatever name it
                         actually advertises. Filtered results are sorted first / flagged.

        Returns:
            List of discovered devices with 'address', 'name', 'rssi', 'likely_dog' keys
        """
        if self.simulate:
            logger.info("[SIMULATE] Scanning for devices...")
            await asyncio.sleep(0.5)
            return [{"address": "SIMULATED:00:11:22:33:44:55", "name": self.DEVICE_NAME_FILTER, "likely_dog": True}]

        timeout = timeout or self.SCAN_TIMEOUT
        logger.info(f"Scanning (include_all={include_all}, timeout: {timeout}s)...")

        # active scanning requests scan-response packets, where the local name usually lives;
        # return_adv=True surfaces AdvertisementData (local_name + rssi + service uuids).
        found = await BleakScanner.discover(timeout=timeout, return_adv=True, scanning_mode="active")
        results = []

        for address, (device, adv) in found.items():
            # prefer the advertised local name; fall back to the device name
            name = (adv.local_name if adv and adv.local_name else None) or device.name or ""
            svc_uuids = [str(u).lower() for u in (adv.service_uuids or [])] if adv else []
            # positive ID: matches the dog name, OR advertises one of the dog's known GATT UUIDs
            by_name = bool(name) and self.DEVICE_NAME_FILTER.lower() in name.lower()
            by_uuid = any(u in svc_uuids for u in self.KNOWN_DOG_UUIDS)
            likely = by_name or by_uuid
            if not include_all and not likely:
                continue
            rssi = adv.rssi if adv and adv.rssi is not None else getattr(device, "rssi", None)
            results.append({
                "address": device.address,
                "name": name or "(no name)",
                "rssi": rssi,
                "likely_dog": likely,
                "by_uuid": by_uuid,
            })
            if svc_uuids:
                logger.info(f"Found: {name or '(no name)'} ({device.address}) rssi={rssi} svc={svc_uuids}")
            else:
                logger.info(f"Found: {name or '(no name)'} ({device.address}) rssi={rssi}")

        # dogs first, then strongest signal first (closest device is usually the target)
        results.sort(key=lambda d: (not d["likely_dog"], -(d["rssi"] or -999)))
        logger.info(f"Scan complete: {len(results)} device(s) returned "
                    f"({sum(d['likely_dog'] for d in results)} likely dog)")
        return results

    async def connect(self, address: str = None, timeout: float = 20.0):
        """
        Connect to robot dog BLE device.

        Connection flow from MainActivity.java:733-740:
        1. Connect to device
        2. Discover GATT services
        3. Find WRITE characteristic (property & 0x0C != 0)
        4. Find NOTIFY characteristic (property & 0x10 != 0)
        5. Subscribe to notifications

        Args:
            address: BLE MAC address (if None, auto-scan for first device)
            timeout: Connection timeout in seconds

        Raises:
            ValueError: If no device found or characteristics missing
            TimeoutError: If connection times out
        """
        if self.simulate:
            logger.info(f"[SIMULATE] Connecting to {address or 'auto-discovered device'}...")
            await asyncio.sleep(0.5)
            self.device_address = address or "SIMULATED:00:11:22:33:44:55"
            logger.info(f"[SIMULATE] Connected to {self.device_address}")
            return

        # Auto-scan if no address provided
        if not address:
            logger.info("No address provided, auto-scanning...")
            devices = await self.scan(timeout=self.SCAN_TIMEOUT)
            if not devices:
                raise ValueError(f"No {self.DEVICE_NAME_FILTER} devices found")
            address = devices[0]['address']
            logger.info(f"Using first found device: {address}")

        self.device_address = address
        logger.info(f"Connecting to {address}...")

        self.client = BleakClient(address, timeout=timeout)
        await self.client.connect()
        logger.info(f"Connected to {address}")

        # Discover characteristics — mirror the app's selection exactly
        # (MainActivity.swapScanResult:733-741): scan services in order; within a
        # service, take the LAST writable (props & 0x0C) and LAST notifiable (props & 0x10)
        # characteristic; accept the FIRST service that yields BOTH. This avoids latching
        # onto a stray writable characteristic in an unrelated service (e.g. 0x1801).
        services = self.client.services
        logger.info("Discovering GATT characteristics (app-matched per-service pairing)...")
        self.write_char = None
        self.notify_char = None

        for service in services:
            w = n = None
            for char in service.characteristics:
                if 'write' in char.properties or 'write-without-response' in char.properties:
                    w = char  # last writable wins, as in the app
                if 'notify' in char.properties or 'indicate' in char.properties:
                    n = char  # last notifiable wins
            if w is not None and n is not None:
                self.write_char, self.notify_char = w, n
                logger.info(f"Service {service.uuid}: WRITE={w.uuid} {w.properties} | "
                            f"NOTIFY={n.uuid} {n.properties}")
                break

        if not self.write_char or not self.notify_char:
            # fall back to a global best-effort pick so we can still log what's there
            for service in services:
                for char in service.characteristics:
                    logger.info(f"  gatt {service.uuid} / {char.uuid} props={char.properties}")
                    if not self.write_char and ('write' in char.properties or 'write-without-response' in char.properties):
                        self.write_char = char
                    if not self.notify_char and ('notify' in char.properties or 'indicate' in char.properties):
                        self.notify_char = char
            if not self.write_char:
                await self.disconnect()
                raise ValueError("No WRITE characteristic found")
            if not self.notify_char:
                await self.disconnect()
                raise ValueError("No NOTIFY characteristic found")

        # remember whether the write char supports write-WITH-response (app default)
        self._write_response = 'write' in self.write_char.properties

        # Subscribe to notifications
        await self.client.start_notify(self.notify_char, self._handle_notify)
        logger.info("Subscribed to notifications")
        logger.info("✓ Connection ready")

    def _handle_notify(self, characteristic: BleakGATTCharacteristic, data: bytearray):
        """
        Handle incoming BLE notification packets.

        Note: App does not decode telemetry payload (MainActivity.java:754-760).
        Only logs raw hex and uses arrival as liveness signal.
        """
        self._telemetry_data = data
        hex_str = data.hex().upper()
        logger.debug(f"NOTIFY: {hex_str}")

        if self._notify_callback:
            self._notify_callback(hex_str)

    async def disconnect(self):
        """Disconnect from device."""
        if self.simulate:
            logger.info("[SIMULATE] Disconnected")
            self.device_address = None
            return

        if self.client and self.client.is_connected:
            try:
                if self.notify_char:
                    await self.client.stop_notify(self.notify_char)
            except Exception as e:
                logger.warning(f"Error stopping notifications: {e}")

            await self.client.disconnect()
            logger.info("Disconnected")

        self.client = None
        self.write_char = None
        self.notify_char = None
        self.device_address = None

    async def send_command(self, command: str):
        """
        Send a movement/action command to the robot.

        Args:
            command: Command name from COMMANDS dict (e.g., 'forward', 'sit')

        Raises:
            RuntimeError: If not connected
            KeyError: If command not recognized
        """
        if not self.device_address:
            raise RuntimeError("Not connected to device")

        packet = encode(command)
        hex_str = packet.hex().upper()

        if self.simulate:
            logger.info(f"[SIMULATE] WRITE: {command} -> {hex_str}")
            await asyncio.sleep(0.1)
            return

        if not self.client or not self.write_char:
            raise RuntimeError("Not connected")

        logger.info(f"WRITE: {command} -> {hex_str}")
        await self.client.write_gatt_char(self.write_char, packet, response=getattr(self, '_write_response', True))

    async def send_feed(self, feed_type: str):
        """
        Send a feed command to the robot.

        Args:
            feed_type: Feed type from FEED_COMMANDS (e.g., 'feed_water')
        """
        if not self.device_address:
            raise RuntimeError("Not connected to device")

        packet = encode_feed(feed_type)
        hex_str = packet.hex().upper()

        if self.simulate:
            logger.info(f"[SIMULATE] WRITE: {feed_type} -> {hex_str}")
            await asyncio.sleep(0.1)
            return

        if not self.client or not self.write_char:
            raise RuntimeError("Not connected")

        logger.info(f"WRITE: {feed_type} -> {hex_str}")
        await self.client.write_gatt_char(self.write_char, packet, response=getattr(self, '_write_response', True))

    async def send_legs(self, fl: int, fr: int, bl: int, br: int):
        """
        Send individual leg position control command.

        Args:
            fl: Front left leg angle (degrees)
            fr: Front right leg angle (degrees)
            bl: Back left leg angle (degrees)
            br: Back right leg angle (degrees)
        """
        if not self.device_address:
            raise RuntimeError("Not connected to device")

        packet = encode_legs(fl, fr, bl, br)
        hex_str = packet.hex().upper()

        if self.simulate:
            logger.info(f"[SIMULATE] WRITE: legs({fl},{fr},{bl},{br}) -> {hex_str}")
            await asyncio.sleep(0.1)
            return

        if not self.client or not self.write_char:
            raise RuntimeError("Not connected")

        logger.info(f"WRITE: legs({fl},{fr},{bl},{br}) -> {hex_str}")
        await self.client.write_gatt_char(self.write_char, packet, response=getattr(self, '_write_response', True))

    def set_notify_callback(self, callback: Callable[[str], None]):
        """
        Set callback for telemetry notifications.

        Args:
            callback: Function that receives hex string of telemetry data
        """
        self._notify_callback = callback

    def get_status(self) -> Dict[str, Any]:
        """
        Get current connection status.

        Returns:
            Status dict with 'connected', 'address', and 'last_telemetry' keys
        """
        return {
            "connected": self.device_address is not None,
            "address": self.device_address,
            "simulate": self.simulate,
            "last_telemetry": self._telemetry_data.hex().upper() if self._telemetry_data else None,
        }

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        if self.simulate:
            return self.device_address is not None
        return self.client is not None and self.client.is_connected
