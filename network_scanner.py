"""
Network scanner — discovers hosts on the local subnet.
Tries arp-scan first (best results), falls back to nmap, then to the ARP cache.
"""

import logging
import re
import socket
import subprocess

logger = logging.getLogger(__name__)


def _local_subnet() -> str:
    """Best-effort local subnet prefix (e.g. '192.168.1')."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        return ".".join(ip.split(".")[:3])
    except Exception:
        return "192.168.1"


def _parse_arp_output(text: str) -> list[dict]:
    hosts = []
    for line in text.splitlines():
        # BSD/macOS: host (ip) at mac [ether] on iface
        # Linux:    Address HWtype HWaddress Flags Mask Iface
        ip_m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line)
        mac_m = re.search(r"((?:[0-9a-fA-F]{1,2}[:\-]){5}[0-9a-fA-F]{1,2})", line)
        if ip_m and mac_m:
            ip = ip_m.group(1)
            mac = mac_m.group(1).upper().replace("-", ":")
            # Normalise short octets: 0:1:2:... → 00:01:02:...
            mac = ":".join(p.zfill(2) for p in mac.split(":"))
            name = line.split("(")[0].strip() or ip
            hosts.append({"ip": ip, "mac": mac, "name": name})
    return hosts


def _try_arp_scan() -> list[dict]:
    try:
        result = subprocess.run(
            ["arp-scan", "--localnet"],
            capture_output=True, text=True, timeout=30,
        )
        hosts = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and re.match(r"\d+\.\d+\.\d+\.\d+", parts[0].strip()):
                hosts.append({
                    "ip": parts[0].strip(),
                    "mac": parts[1].strip().upper(),
                    "name": parts[2].strip() if len(parts) > 2 else "",
                })
        if hosts:
            return hosts
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as exc:
        logger.debug("arp-scan failed: %s", exc)
    return []


def _try_nmap() -> list[dict]:
    subnet = _local_subnet()
    try:
        result = subprocess.run(
            ["nmap", "-sn", "--max-retries", "1", f"{subnet}.0/24"],
            capture_output=True, text=True, timeout=45,
        )
        hosts = []
        current_ip, current_name = None, None
        for line in result.stdout.splitlines():
            ip_m = re.search(r"Nmap scan report for (?:(\S+) )?\(?([\d.]+)\)?", line)
            if ip_m:
                current_name = ip_m.group(1) or ""
                current_ip = ip_m.group(2)
            mac_m = re.search(r"MAC Address: ((?:[0-9A-F]{2}[:\-]){5}[0-9A-F]{2})", line, re.I)
            if mac_m and current_ip:
                hosts.append({"ip": current_ip, "mac": mac_m.group(1).upper(), "name": current_name or current_ip})
                current_ip = current_name = None
        if hosts:
            return hosts
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as exc:
        logger.debug("nmap scan failed: %s", exc)
    return []


def _try_arp_cache() -> list[dict]:
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
        return _parse_arp_output(result.stdout)
    except Exception as exc:
        logger.debug("arp -a failed: %s", exc)
    return []


def scan_network() -> list[dict]:
    """
    Return a list of { ip, mac, name } dicts for reachable hosts.
    Tries: arp-scan → nmap → arp cache.
    """
    hosts = _try_arp_scan()
    if not hosts:
        hosts = _try_nmap()
    if not hosts:
        hosts = _try_arp_cache()

    # Deduplicate by IP
    seen_ips: set[str] = set()
    unique: list[dict] = []
    for h in hosts:
        if h["ip"] not in seen_ips:
            seen_ips.add(h["ip"])
            unique.append(h)

    return sorted(unique, key=lambda h: [int(x) for x in h["ip"].split(".")])
