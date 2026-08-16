"""
Bluetooth pairing helpers — drives bluetoothctl via subprocess.
Linux/Pi only. On other platforms all functions return gracefully.
"""

import logging
import platform
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

_IS_LINUX = platform.system() == "Linux"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _btctl(*args, timeout=10) -> tuple[bool, str]:
    """Run a single bluetoothctl command, return (success, output)."""
    if not _IS_LINUX:
        return False, "Not supported on this platform"
    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode == 0, out
    except FileNotFoundError:
        return False, "bluetoothctl not found — install bluez"
    except subprocess.TimeoutExpired:
        return False, "bluetoothctl timed out"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_for_pairable(duration: int = 10, emit_fn=None) -> list[dict]:
    """
    Run a BT + BLE discovery scan for `duration` seconds.
    Calls emit_fn(device_dict) in real-time as devices appear.
    Returns the full list at the end.
    """
    if not _IS_LINUX:
        return []

    found: dict[str, dict] = {}

    def _stream():
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            proc.stdin.write("power on\nagent on\ndefault-agent\nscan on\n")
            proc.stdin.flush()

            deadline = time.time() + duration
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                # Match lines like:
                #   [NEW] Device AA:BB:CC:DD:EE:FF Some Name
                #   [CHG] Device AA:BB:CC:DD:EE:FF Name: Some Name
                m = re.search(
                    r"\[(?:NEW|CHG)\]\s+Device\s+((?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(.*)",
                    line, re.IGNORECASE,
                )
                if m:
                    mac  = m.group(1).upper()
                    name = m.group(2).strip()
                    if name.startswith("Name:"):
                        name = name[5:].strip()
                    if mac not in found or (name and name != mac):
                        found[mac] = {"mac": mac, "name": name}
                        if emit_fn:
                            emit_fn(found[mac])

            proc.stdin.write("scan off\nquit\n")
            proc.stdin.flush()
            proc.wait(timeout=3)
        except Exception as exc:
            logger.error("scan_for_pairable error: %s", exc)

    t = threading.Thread(target=_stream, daemon=True)
    t.start()
    t.join()
    return list(found.values())


# ---------------------------------------------------------------------------
# Pair / trust / unpair
# ---------------------------------------------------------------------------

def pair_device(mac: str) -> tuple[bool, str]:
    """Pair and trust a BT device by MAC. Returns (success, message)."""
    if not _IS_LINUX:
        return False, "Linux only"

    logger.info("Pairing %s …", mac)

    # Use a single bluetoothctl session for both pair + trust
    try:
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        proc.stdin.write(f"power on\nagent on\ndefault-agent\npair {mac}\n")
        proc.stdin.flush()

        output_lines = []
        deadline = time.time() + 30
        paired = False

        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            output_lines.append(line.strip())
            logger.debug("btctl: %s", line.strip())

            if "Pairing successful" in line or "already paired" in line.lower():
                paired = True
                break
            if "Failed to pair" in line or "not available" in line.lower():
                proc.stdin.write("quit\n")
                proc.stdin.flush()
                proc.wait(timeout=3)
                return False, f"Pairing failed: {line.strip()}"

        if paired:
            proc.stdin.write(f"trust {mac}\nquit\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
            logger.info("Paired and trusted %s", mac)
            return True, f"Paired and trusted {mac}"

        proc.stdin.write("quit\n")
        proc.stdin.flush()
        proc.wait(timeout=3)
        return False, "Pairing timed out — make sure controller is in pairing mode"

    except Exception as exc:
        return False, str(exc)


def unpair_device(mac: str) -> tuple[bool, str]:
    """Remove a paired BT device."""
    ok, out = _btctl("remove", mac, timeout=10)
    if ok or "not available" in out.lower():
        return True, f"Removed {mac}"
    return False, out


def get_paired_devices() -> list[dict]:
    """Return list of devices currently paired with this Pi."""
    if not _IS_LINUX:
        return []
    ok, out = _btctl("devices", "Paired")
    if not ok:
        return []
    devices = []
    for line in out.splitlines():
        m = re.search(r"Device\s+((?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(.*)", line, re.IGNORECASE)
        if m:
            devices.append({"mac": m.group(1).upper(), "name": m.group(2).strip()})
    return devices
