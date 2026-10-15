"""Bounded, read-only discovery of USB/IP services on directly attached IPv4 LANs."""

import ipaddress
import json
import math
import platform
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable, Optional

from usbip_tools import USBIPError


USBIP_PORT = 3240
MAX_SCAN_HOSTS = 512
_PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


@dataclass(frozen=True)
class NetworkAddress:
    """An interface's IPv4 address and actual on-link prefix length."""

    address: str
    prefixlen: int


def _interface(value: NetworkAddress) -> Optional[ipaddress.IPv4Interface]:
    """Discard untrusted or unsupported records before constructing scan targets."""
    if not isinstance(value, NetworkAddress):
        return None
    if not isinstance(value.address, str) or not isinstance(value.prefixlen, int):
        return None
    if isinstance(value.prefixlen, bool) or not 0 <= value.prefixlen <= 32:
        return None
    try:
        address = ipaddress.IPv4Address(value.address)
    except ipaddress.AddressValueError:
        return None
    # Discovery stays on private LANs; public, loopback and link-local addresses
    # remain available through the menu's manual hostname/IP option instead.
    if not any(address in network for network in _PRIVATE_NETWORKS):
        return None
    interface = ipaddress.IPv4Interface(f"{address}/{value.prefixlen}")
    if interface.network.prefixlen < 31 and address in (
        interface.network.network_address, interface.network.broadcast_address,
    ):
        return None
    return interface


def _addresses_from_json(output: str, system: str) -> list[NetworkAddress]:
    records = json.loads(output or "[]")
    if system == "Windows" and isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ValueError("Expected network interface records")
    addresses = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if system == "Linux":
            if record.get("ifname") == "lo" or record.get("operstate") == "DOWN":
                continue
            entries = record.get("addr_info", [])
            if not isinstance(entries, list):
                continue
            entries = [entry for entry in entries if isinstance(entry, dict)
                       and entry.get("family") == "inet"
                       and entry.get("scope") == "global"]
            candidates = [NetworkAddress(entry.get("local"), entry.get("prefixlen"))
                          for entry in entries]
        else:
            candidates = [NetworkAddress(record.get("IPAddress"), record.get("PrefixLength"))]
        for candidate in candidates:
            interface = _interface(candidate)
            if interface is not None:
                address = NetworkAddress(str(interface.ip), interface.network.prefixlen)
                if address not in addresses:
                    addresses.append(address)
    return addresses


def _hostname_addresses() -> list[NetworkAddress]:
    """Keep only local IPs when the native on-link prefixes are unavailable.

    Hostname resolution cannot tell us a subnet's boundaries, so /32 avoids
    guessing a /24 and accidentally probing hosts beyond the attached LAN.
    """
    try:
        records = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    addresses = []
    for record in records:
        candidate = NetworkAddress(record[4][0], 32)
        if _interface(candidate) is not None and candidate not in addresses:
            addresses.append(candidate)
    return addresses


def network_addresses() -> list[NetworkAddress]:
    """Read active IPv4 interface prefixes without elevation or network probes."""
    system = platform.system()
    if system == "Linux":
        command = ["ip", "-j", "-4", "address", "show", "up"]
    elif system == "Windows":
        script = (
            "Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred "
            "| Where-Object { $_.InterfaceAlias -notmatch '^Loopback' } "
            "| Select-Object IPAddress, PrefixLength | ConvertTo-Json -Compress"
        )
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        return _hostname_addresses()
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)
        if result.returncode == 0:
            return _addresses_from_json(result.stdout, system)
        detail = (result.stderr or result.stdout or "").strip()
        if any(message in detail.casefold() for message in (
            "operation not permitted", "permission denied", "access is denied", "access denied",
        )):
            raise USBIPError(
                "Network permissions blocked reading local network interfaces. "
                "Allow local network access, then search again. "
                f"Details: {detail}"
            )
    except PermissionError as error:
        raise USBIPError(
            "Network permissions blocked reading local network interfaces. "
            "Allow local network access, then search again. "
            f"Details: {error}"
        ) from None
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        pass
    return _hostname_addresses()


def scan_targets(
    addresses: Iterable[NetworkAddress], max_hosts: int = MAX_SCAN_HOSTS,
) -> list[str]:
    """Choose up to 512 LAN addresses, limiting broad networks to a local /24.

    Smaller real subnets retain their boundaries. Include this machine so a
    local sharing service can also be selected during setup and testing.
    """
    if isinstance(max_hosts, bool) or not isinstance(max_hosts, int) or max_hosts < 0:
        raise ValueError("max_hosts must be a nonnegative integer")
    limit = min(max_hosts, MAX_SCAN_HOSTS)
    if not limit:
        return []
    interfaces = []
    for address in addresses:
        interface = _interface(address)
        if interface is not None and interface not in interfaces:
            interfaces.append(interface)
    targets = []
    seen = set()

    def add(address: ipaddress.IPv4Address) -> bool:
        if address not in seen and any(address in network for network in _PRIVATE_NETWORKS):
            targets.append(str(address))
            seen.add(address)
        return len(targets) >= limit

    # Keep every interface's own address even when multiple networks hit the cap.
    for interface in interfaces:
        if add(interface.ip):
            return targets
    networks = []
    for interface in interfaces:
        network = ipaddress.IPv4Network(
            f"{interface.ip}/{max(interface.network.prefixlen, 24)}", strict=False,
        )
        if network not in networks:
            networks.append(network)
    for network in networks:
        for address in network.hosts():
            if add(address):
                return targets
    return targets


def _port_open(address: str, timeout: float) -> bool:
    try:
        with socket.create_connection((address, USBIP_PORT), timeout=timeout):
            return True
    except PermissionError as error:
        raise USBIPError(
            "Network permissions blocked the search for USB/IP services. "
            "Allow local network access, then search again. "
            f"Details: {error}"
        ) from None
    except OSError:
        return False


def discover_hosts(
    addresses: Optional[Iterable[NetworkAddress]] = None, *, timeout: float = 0.25,
    workers: int = 32,
) -> list[str]:
    """Return LAN IPs accepting TCP 3240; callers must query USB/IP device lists.

    An open port alone does not prove that the service is USB/IP or that a USB
    device is available. No devices, drivers or services are changed here.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    targets = scan_targets(network_addresses() if addresses is None else addresses)
    if not targets:
        return []
    executor = ThreadPoolExecutor(max_workers=min(workers, 64, len(targets)))
    futures = {}
    hosts = []
    try:
        for address in targets:
            futures[executor.submit(_port_open, address, timeout)] = address
        for future in as_completed(futures):
            if future.result():
                hosts.append(futures[future])
    finally:
        # On Ctrl+C or a permissions error, discard queued work immediately.
        # Only probes already running must finish their short socket timeout.
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    return sorted(hosts, key=ipaddress.IPv4Address)
