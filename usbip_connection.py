"""Wait briefly for native USB/IP attachment records to become visible."""

import ipaddress
import time
from typing import Optional

from usbip_devices import USBConnection, USBDevice, connected_devices
from usbip_tools import USBIPError


def snapshot_connections(tools) -> list[USBConnection]:
    """Read imports with a half-second timeout, allowing a missing driver."""
    try:
        return connected_devices(tools, timeout=0.5)
    except USBIPError:
        return []


def _address_key(address: str) -> str:
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return address.rstrip(".").casefold()


def _is_ip_address(address: str) -> bool:
    try:
        ipaddress.ip_address(address)
        return True
    except ValueError:
        return False


def _same_device(connection: USBConnection, device: USBDevice) -> bool:
    """Use available identity fields without accepting conflicting identifiers."""
    if connection.busid and connection.busid != device.busid:
        return False
    if connection.hardware_id and device.hardware_id:
        if connection.hardware_id.casefold() != device.hardware_id.casefold():
            return False
        return True
    if connection.busid == device.busid:
        return True
    name = " ".join(connection.name.split()).casefold()
    return name == " ".join(device.name.split()).casefold() and name not in {
        "", "unknown usb device", "unknown remote usb device",
    }


def _matches_attachment(connection, device, address, previous_by_port) -> bool:
    same_host = bool(connection.host) and _address_key(connection.host) == _address_key(address)
    if same_host and connection.busid == device.busid:
        return True

    # A model ID cannot identify a particular physical device on its own. Only
    # use partial records when the attachment changed the observed port state.
    if previous_by_port.get(connection.port) == connection:
        return False
    if connection.host and not same_host:
        # Linux may record an IP for a hostname supplied to attach. No DNS or
        # extra network requests are needed to accept a newly registered port.
        if _is_ip_address(address) or not _is_ip_address(connection.host):
            return False
    return _same_device(connection, device)


def wait_for_connection(
    tools,
    device: USBDevice,
    address: str,
    previous=(),
    *,
    attempts: int = 10,
    interval: float = 0.2,
) -> Optional[USBConnection]:
    """Return the intended visible import, or None while registration is pending.

    Native attach can succeed before ``usbip port`` exposes the device. Polling
    is read-only: by default it sleeps at most 1.8 seconds between ten reads,
    each capped at 0.5 seconds (up to 6.8 seconds altogether, apart from process
    startup overhead). Transient driver errors and timeouts remain pending.
    Supply a pre-attach snapshot so an existing identical model cannot satisfy
    a partial-source match.
    """
    previous_by_port = {connection.port: connection for connection in previous}
    for attempt in range(attempts):
        for connection in snapshot_connections(tools):
            if _matches_attachment(connection, device, address, previous_by_port):
                return connection
        if attempt + 1 < attempts:
            time.sleep(interval)
    return None
