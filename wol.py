"""
Wake-on-LAN magic packet sender.
Broadcasts the 102-byte magic packet to the subnet broadcast address on UDP port 9.
"""

import re
import socket


def send_wol(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> None:
    """
    Send a Wake-on-LAN magic packet for the given MAC address.

    :param mac_address: Target MAC in any common notation (AA:BB:CC:DD:EE:FF,
                        AA-BB-CC-DD-EE-FF, or AABBCCDDEEFF).
    :param broadcast_ip: Broadcast address to use (defaults to 255.255.255.255).
    :param port:         UDP port (7 or 9 are standard; 9 is almost always open).
    """
    mac_clean = re.sub(r"[:\-\.\s]", "", mac_address).upper()
    if len(mac_clean) != 12 or not all(c in "0123456789ABCDEF" for c in mac_clean):
        raise ValueError(f"Invalid MAC address: {mac_address!r}")

    mac_bytes = bytes.fromhex(mac_clean)
    magic_packet = b"\xff" * 6 + mac_bytes * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.connect_ex((broadcast_ip, port))
        sock.send(magic_packet)
