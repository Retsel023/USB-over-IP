"""Read native USB/IP device lists for the guided terminal interface."""

import argparse
from dataclasses import dataclass
import re

from usbip_cli import busid as validate_busid, host as validate_host, port as validate_port
from usbip_tools import USBIPError


_HARDWARE_ID = r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}"
_LOCAL_HEADER = re.compile(
    rf"^\s*-\s+busid\s+(\S+)(?:\s+\(({_HARDWARE_ID})\))?\s*$"
)
_REMOTE_DEVICE = re.compile(r"^\s*(\S+):\s+(.+?)\s*$")
_WINDOWS_DEVICE = re.compile(rf"^\s*(\S+)\s+(?:({_HARDWARE_ID})\s+)?(.+?)\s*$")
_DESCRIPTION_ID = re.compile(rf"\s*\(({_HARDWARE_ID})\)\s*$")


@dataclass(frozen=True)
class USBDevice:
    """One device with a validated bus ID, distinct from its vendor/product ID."""

    busid: str
    name: str
    hardware_id: str = ""

    def __post_init__(self):
        validate_busid(self.busid)

    @property
    def label(self) -> str:
        identity = f" ({self.hardware_id})" if self.hardware_id else ""
        return f"{self.name}{identity} — {self.busid}"


@dataclass(frozen=True)
class USBConnection:
    """One imported device; its native port is used internally for disconnection."""

    port: int
    name: str
    host: str = ""
    busid: str = ""
    hardware_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "port", validate_port(str(self.port)))
        if self.host:
            validate_host(self.host)
        if self.busid:
            validate_busid(self.busid)

    @property
    def label(self) -> str:
        identity = f" ({self.hardware_id})" if self.hardware_id else ""
        source = " / ".join(value for value in (self.host, self.busid) if value)
        return f"{self.name}{identity}" + (f" — {source}" if source else "")


def _valid_busid(value: str) -> bool:
    try:
        validate_busid(value)
    except argparse.ArgumentTypeError:
        return False
    return True


def _description(text: str, hardware_id: str = "") -> tuple[str, str]:
    """Keep the hardware ID separate, avoiding duplicate IDs in menu labels."""
    match = _DESCRIPTION_ID.search(text)
    if match:
        hardware_id = match.group(1)
        text = text[:match.start()]
    return text.strip() or "Unknown USB device", hardware_id.lower()


def parse_local_linux(output: str) -> list[USBDevice]:
    """Parse ``usbip list -l``, preserving hierarchical bus IDs such as 3-5.1."""
    devices = []
    pending = None
    for line in output.splitlines():
        if re.match(r"^\s*-\s+busid(?:\s|$)", line):
            if pending is not None:
                devices.append(USBDevice(*pending))
                pending = None
            match = _LOCAL_HEADER.fullmatch(line)
            if match and _valid_busid(match.group(1)):
                pending = (match.group(1), "Unknown USB device", (match.group(2) or "").lower())
            continue
        text = line.strip()
        if pending is not None and text and not text.startswith((":", "/sys/")):
            name, hardware_id = _description(text, pending[2])
            devices.append(USBDevice(pending[0], name, hardware_id))
            pending = None
    if pending is not None:
        devices.append(USBDevice(*pending))
    return devices


def parse_remote_devices(output: str) -> list[USBDevice]:
    """Parse ``usbip list -r HOST`` without treating interface lines as devices."""
    devices = []
    for line in output.splitlines():
        match = _REMOTE_DEVICE.fullmatch(line)
        if match and _valid_busid(match.group(1)):
            name, hardware_id = _description(match.group(2))
            devices.append(USBDevice(match.group(1), name, hardware_id))
    return devices


def parse_windows_devices(output: str) -> list[USBDevice]:
    """Parse connected devices from ``usbipd list``, excluding persisted GUIDs."""
    devices = []
    for line in output.splitlines():
        if line.strip().lower() == "persisted:":
            break
        match = _WINDOWS_DEVICE.fullmatch(line)
        if not match or not _valid_busid(match.group(1)):
            continue
        body = match.group(3)
        # Native output pads DEVICE and STATE with at least two spaces. Use the
        # final padded column so device descriptions may themselves have spaces.
        padded = re.fullmatch(r"(.*\S)\s{2,}\S.*", body)
        if padded:
            body = padded.group(1)
        else:
            # Also accept copied/unpadded output and older usbipd versions.
            body = re.sub(
                r"\s+(?:Not shared|Shared(?: \(forced\))?|Attached(?: \([^)]*\))?)$",
                "", body,
            )
        name, hardware_id = _description(body, match.group(2) or "")
        devices.append(USBDevice(match.group(1), name, hardware_id))
    return devices


def _connection_source(text: str) -> tuple[str, str]:
    """Read only the remote URI, never the imported device's local bus ID."""
    match = re.search(r"usbip://([^\s/]+)/([^\s/]+)", text)
    if not match:
        return "", ""
    authority, remote_busid = match.groups()
    # Linux records may print IPv6 either bracketed as a URI or unbracketed.
    # Splitting off the final TCP port handles both without truncating IPv6.
    hostname, separator, tcp_port = authority.rpartition(":")
    if not separator or len(tcp_port) > 5 or not tcp_port.isascii() or not tcp_port.isdecimal():
        return "", ""
    if not 1 <= int(tcp_port) <= 65535:
        return "", ""
    hostname = hostname.removeprefix("[").removesuffix("]")
    try:
        validate_host(hostname)
        validate_busid(remote_busid)
    except argparse.ArgumentTypeError:
        return "", ""
    return hostname, remote_busid


def parse_connected_devices(output: str) -> list[USBConnection]:
    """Parse Linux/Windows ``usbip port`` while keeping native ports out of labels."""
    connections = []
    current = None
    for raw_line in output.splitlines():
        text = re.sub(r"^\s*usbip:\s+info:\s*", "", raw_line).strip()
        header = re.fullmatch(r"Port\s*([^\s:]+)\s*:\s*(.*)", text, re.IGNORECASE)
        if header:
            if current is not None:
                connections.append(USBConnection(**current))
            current = None
            if not header.group(1).isascii() or not header.group(1).isdecimal():
                continue
            try:
                native_port = validate_port(header.group(1))
            except argparse.ArgumentTypeError:
                continue
            if re.search(r"<(?:Port Available|Port Empty|No device)>", header.group(2), re.IGNORECASE):
                continue
            current = {"port": native_port, "name": "Unknown remote USB device"}
            continue
        if current is None or not text:
            continue
        if "usbip://" in text:
            hostname, remote_busid = _connection_source(text)
            if hostname:
                current.update(host=hostname, busid=remote_busid)
            continue
        if "->" in text or text.startswith((":", "/sys/", "libusbip ")):
            continue
        if current["name"] == "Unknown remote USB device":
            current["name"], current["hardware_id"] = _description(text)
    if current is not None:
        connections.append(USBConnection(**current))
    return connections


def _diagnostic(result) -> str:
    return (result.stderr or result.stdout or "No diagnostic was returned.").strip()


def local_devices(tools) -> list[USBDevice]:
    """List plugged-in devices without elevation or changes to USB drivers."""
    if tools.system == "Linux":
        result = tools.capture("usbip", ["list", "-l"])
        parser = parse_local_linux
    elif tools.system == "Windows":
        result = tools.capture("usbipd", ["list"])
        parser = parse_windows_devices
    else:
        raise USBIPError("Local USB discovery requires Linux or Windows.")
    if result.returncode:
        raise USBIPError(
            f"Could not list local USB devices (exit {result.returncode}): {_diagnostic(result)}"
        )
    return parser(result.stdout or "")


def remote_devices(tools, host: str, *, timeout=None) -> list[USBDevice]:
    """List devices offered by another computer's running USB/IP service."""
    try:
        checked_host = validate_host(host)
    except argparse.ArgumentTypeError as error:
        raise USBIPError(str(error)) from None
    arguments = ["list", "-r", checked_host]
    if timeout is None:
        result = tools.capture("usbip", arguments)
    else:
        result = tools.capture("usbip", arguments, timeout=timeout)
    if result.returncode:
        raise USBIPError(
            f"Could not reach the USB/IP service on {checked_host} "
            f"(exit {result.returncode}). Make sure the sharing computer and its "
            "USB/IP service are running and reachable over the network. "
            f"{_diagnostic(result)}"
        )
    return parse_remote_devices(result.stdout or "")


def connected_devices(tools, *, timeout=None) -> list[USBConnection]:
    """Read existing imports without elevation, attachment, or disconnection."""
    if timeout is None:
        result = tools.capture("usbip", ["port"])
    else:
        result = tools.capture("usbip", ["port"], timeout=timeout)
    if result.returncode:
        raise USBIPError(
            f"Could not list connected remote USB devices (exit {result.returncode}): "
            f"{_diagnostic(result)}"
        )
    return parse_connected_devices(result.stdout or "")
