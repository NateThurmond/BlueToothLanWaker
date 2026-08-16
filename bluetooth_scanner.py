"""
Bluetooth scanner — detects both BLE (PS5 DualSense, Xbox Series X/S, etc.)
and classic Bluetooth (Xbox One, PS4, etc.) devices.

On macOS the BT addresses are CoreBluetooth UUIDs (not real MACs) — this is
fine for local UI testing.  Real MAC addresses are returned on Linux / Raspberry Pi.
"""

import asyncio
import logging
import platform
import subprocess

logger = logging.getLogger(__name__)

try:
    from bleak import BleakScanner
    _BLEAK_OK = True
except ImportError:
    _BLEAK_OK = False
    logger.warning("bleak not installed — BLE scanning disabled")


# ---------------------------------------------------------------------------
# Known Bluetooth manufacturer company IDs
# ---------------------------------------------------------------------------
_MANUFACTURER_MAP: dict[int, str] = {
    0x054C: "Sony",        # Sony Interactive Entertainment (PS4, PS5)
    0x0006: "Microsoft",   # Microsoft (Xbox)
    0x004C: "Apple",
    0x0171: "Nintendo",    # Nintendo (Switch Pro, Joy-Con)
    0x0075: "Samsung",
    0x00E0: "Google",
}

# BLE HID service UUID (Human Interface Device — gamepads, keyboards, mice)
_HID_SERVICE_UUID = "00001812-0000-1000-8000-00805f9b34fb"

# Name substring → device_type label  (most specific patterns FIRST)
_NAME_PATTERNS: list[tuple[str, str]] = [
    ("DualSense",                "PS5 DualSense"),
    ("DualShock 4",              "PS4 DualShock 4"),
    ("DualShock",                "PlayStation Controller"),
    ("Xbox Elite",               "Xbox Elite Controller"),
    ("Xbox Wireless Controller", "Xbox Controller"),
    ("Xbox",                     "Xbox Controller"),
    ("Pro Controller",           "Nintendo Switch Pro"),
    ("Joy-Con",                  "Nintendo Joy-Con"),
    ("Wireless Controller",      "PlayStation Controller"),   # PS3/PS4 generic
    ("Stadia",                   "Google Stadia Controller"),
    ("8BitDo",                   "8BitDo Controller"),
    ("Steam Controller",         "Steam Controller"),
    ("Luna Controller",          "Amazon Luna Controller"),
]


def classify_device(
    name: str | None,
    manufacturer_data: dict | None,
    service_uuids: list | None = None,
) -> str | None:
    """
    Return a human-friendly device type string, or None if truly unknown.

    Priority order:
      1. Advertisement name (most specific — only present in pairing mode for controllers)
      2. Manufacturer company ID + HID service UUID combo
      3. Manufacturer company ID alone (broad fallback label)
      4. HID service UUID alone (unknown manufacturer)
    """
    # 1. Name match
    if name:
        name_lower = name.lower()
        for pattern, label in _NAME_PATTERNS:
            if pattern.lower() in name_lower:
                return label

    is_hid = bool(
        service_uuids
        and any(_HID_SERVICE_UUID in str(u).lower() or "1812" in str(u) for u in service_uuids)
    )

    # 2 + 3. Manufacturer data
    if manufacturer_data:
        for company_id in manufacturer_data:
            vendor = _MANUFACTURER_MAP.get(company_id)
            if vendor == "Sony":
                # HID + Sony = controller (most likely DualSense/DualShock)
                return "PlayStation Controller" if is_hid else "Sony Device"
            if vendor == "Microsoft":
                return "Xbox Controller" if is_hid else "Microsoft Device"
            if vendor == "Nintendo":
                return "Nintendo Controller"
            if vendor == "Apple":
                return "Apple Device"

    # 4. HID service alone — definitely an input device even if manufacturer unknown
    if is_hid:
        return "Game Controller"

    return None


# ---------------------------------------------------------------------------
# BLE scanning (bleak)
# ---------------------------------------------------------------------------

async def _ble_scan(duration: float = 5.0) -> list[dict]:
    devices = []
    try:
        # return_adv=True gives us (BLEDevice, AdvertisementData) pairs
        # so we can read manufacturer_data and service_uuids for fingerprinting
        discovered = await BleakScanner.discover(timeout=duration, return_adv=True)
        for address, (ble_device, adv_data) in discovered.items():
            name = ble_device.name or adv_data.local_name or ""
            mfr  = getattr(adv_data, "manufacturer_data", {}) or {}
            uuids = list(getattr(adv_data, "service_uuids", []) or [])
            device_type = classify_device(name, mfr, uuids)
            devices.append(
                {
                    "mac": address,
                    "name": name,
                    "type": "BLE",
                    "device_type": device_type,
                    "rssi": adv_data.rssi if hasattr(adv_data, "rssi") else None,
                }
            )
    except Exception as exc:
        logger.debug("BLE scan error: %s", exc)
    return devices


def _scan_ble_sync(duration: float = 5.0) -> list[dict]:
    if not _BLEAK_OK:
        return []
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_ble_scan(duration))
    except Exception as exc:
        logger.debug("BLE sync scan failed: %s", exc)
        return []
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Classic Bluetooth scanning (Linux/RPi only — hcitool)
# ---------------------------------------------------------------------------

def _scan_classic_bt() -> list[dict]:
    """
    Active inquiry scan for classic BT devices (e.g. Xbox One controllers,
    PS4 controllers).  Requires bluez installed on the host.
    Takes ~10 s to complete.
    """
    devices = []
    try:
        result = subprocess.run(
            ["hcitool", "scan", "--flush"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Scanning"):
                continue
            parts = line.split("\t", 1)
            if len(parts) >= 1 and ":" in parts[0]:
                mac = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else ""
                device_type = classify_device(name, None, None)
                devices.append({"mac": mac, "name": name, "type": "Classic", "device_type": device_type})
    except FileNotFoundError:
        logger.debug("hcitool not found — classic BT scan skipped")
    except subprocess.TimeoutExpired:
        logger.debug("Classic BT scan timed out")
    except Exception as exc:
        logger.debug("Classic BT scan error: %s", exc)
    return devices


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_bluetooth() -> list[dict]:
    """
    Scan for nearby Bluetooth devices (BLE + classic).
    Returns a deduplicated list of { mac, name, type } dicts.
    """
    seen: dict[str, dict] = {}

    # BLE scan (works on macOS and Linux)
    for d in _scan_ble_sync(duration=5.0):
        seen[d["mac"]] = d

    # Classic BT scan (Linux only)
    if platform.system() == "Linux":
        for d in _scan_classic_bt():
            if d["mac"] not in seen:
                seen[d["mac"]] = d

    return list(seen.values())
