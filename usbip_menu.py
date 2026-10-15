"""Guided terminal menus for sharing and using USB devices."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Optional

from usbip_cli import host
from usbip_connection import snapshot_connections, wait_for_connection
from usbip_devices import USBDevice, connected_devices, local_devices, remote_devices
from usbip_network import discover_hosts
from usbip_tools import USBIPError, USBIPTools


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class RemoteDevice:
    """An available device together with the computer sharing it."""

    address: str
    device: USBDevice

    @property
    def label(self):
        return f"{self.device.label} — shared by {self.address}"


def scan_available_devices(tools):
    """Find USB/IP listeners on the local LAN and read their exported devices."""
    tools.resolve("usbip")
    hosts = discover_hosts()
    if not hosts:
        return []
    print(f"Reading USB devices from {len(hosts)} computer(s) listening on port 3240...")
    devices = []
    failures = 0
    executor = ThreadPoolExecutor(max_workers=min(32, len(hosts)))
    pending = {
        executor.submit(remote_devices, tools, address, timeout=1.5): address
        for address in hosts
    }
    try:
        for future in as_completed(pending):
            address = pending[future]
            try:
                devices.extend(RemoteDevice(address, device) for device in future.result())
            except USBIPError:
                failures += 1
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True)
    if failures:
        print(f"Could not read devices from {failures} listener(s). 'Connect by address' shows detailed errors.")
    return sorted(
        devices,
        key=lambda item: (ipaddress.ip_address(item.address), item.device.name, item.device.busid),
    )


def _read(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def choose_device(devices, *, heading="USB devices"):
    """Choose a displayed device by number; never interpret a hardware ID as a bus ID."""
    print(f"\n{heading}")
    if not devices:
        print("No USB devices found. Check the cable and refresh the list.")
        return None
    for number, device in enumerate(devices, 1):
        print(f"  {number}. {device.label}")
    while True:
        choice = _read("Device number (Enter or q to go back): ")
        if not choice or choice.lower() == "q":
            return None
        if choice.isdecimal() and 1 <= int(choice) <= len(devices):
            return devices[int(choice) - 1]
        print(f"Enter a number from 1 to {len(devices)}, or q to go back.")


def ask_host(default: Optional[str] = None) -> Optional[str]:
    """Ask for a hostname or address, with the same validation as the command line."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = _read(f"Sharing computer's IP address or hostname{suffix} (q to go back): ")
        if value.lower() == "q":
            return None
        if not value:
            return default
        try:
            return host(value)
        except argparse.ArgumentTypeError:
            print("Enter an IP address or hostname, such as 192.168.1.50 or raspberrypi.local.")


def run_command(role: str, args, *, elevated=False) -> int:
    """Run the existing command interface, elevating only the selected operation."""
    if role not in ("server", "client"):
        raise ValueError("Role must be server or client")
    command = [sys.executable, str(ROOT / f"{role}.py"), *args]
    try:
        if elevated:
            tools = USBIPTools()
            if tools.system == "Linux" and os.geteuid() != 0:
                sudo = shutil.which("sudo")
                if not sudo:
                    raise USBIPError("sudo is unavailable. Open a root terminal to perform this action.")
                print("Administrator access is needed for this action; sudo may ask for your password.")
                command = [sudo, *command]
            else:
                tools.require_admin()
        return subprocess.run(command, check=False).returncode
    except USBIPError as error:
        print(f"Could not complete the action: {error}")
    except OSError as error:
        print(f"Could not start the command: {error}")
    except KeyboardInterrupt:
        print("\nOperation stopped. Returning to the menu.")
        return 130
    return 1


def service_running() -> bool:
    """Check the local USB/IP listener without starting or stopping any service."""
    try:
        with socket.create_connection(("127.0.0.1", 3240), timeout=0.5):
            return True
    except OSError:
        return False


def local_addresses():
    """List candidate local IPv4 addresses without scanning the network."""
    addresses = set()
    if sys.platform.startswith("linux"):
        try:
            result = subprocess.run(
                ["ip", "-j", "-4", "address", "show", "up"],
                capture_output=True, text=True, check=False, timeout=3,
            )
            if result.returncode == 0:
                for interface in json.loads(result.stdout):
                    for entry in interface.get("addr_info", []):
                        value = entry.get("local", "")
                        address = ipaddress.ip_address(value)
                        if not address.is_loopback and not address.is_link_local:
                            addresses.add(str(address))
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
            pass
    if not addresses:
        try:
            for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = ipaddress.ip_address(entry[4][0])
                if not address.is_loopback and not address.is_link_local:
                    addresses.add(str(address))
        except (OSError, ValueError):
            pass
    return sorted(addresses)


def _connection_info():
    addresses = local_addresses()
    if addresses:
        print("\nSharing computer address: " + ", ".join(addresses))
    else:
        print("\nUse this sharing computer's LAN IP address on the receiving computer.")
    print("On the receiving computer, choose 'Use a remote USB device', then 'Find and connect a USB device'.")
    print("Start it with python3 usbip.py on Linux, or py -3 usbip.py on Windows.")


def _start_service():
    if service_running():
        print("The USB/IP sharing service is already listening on port 3240.")
        _connection_info()
        return
    _connection_info()
    print("Starting the sharing service in the background.")
    status = run_command("server", ["start"], elevated=True)
    if status == 0:
        for attempt in range(10):
            if service_running():
                print("Sharing service is running. You can keep using this menu or close it.")
                return
            if attempt < 9:
                time.sleep(0.1)
        print("Service start was requested, but port 3240 is not listening yet.")
        print("Check prerequisites and any existing service before retrying.")
    elif status != 130:
        print("The sharing service did not start. Check the error above, then choose 'Start sharing service'.")


def server_menu() -> int:
    while True:
        print("\nShare USB devices plugged into this computer")
        print("  1. Share a USB device")
        print("  2. Stop sharing a USB device")
        print("  3. Show local USB devices")
        print("  4. Start sharing service")
        print("  5. Check prerequisites")
        print("  0. Back / quit")
        choice = _read("Choose: ").lower()
        if choice in ("0", "q"):
            print("Shared devices and the background service keep running after this menu closes.")
            print("Choose 'Stop sharing a USB device' when you want to return a device to local use.")
            return 0
        try:
            if choice in ("1", "2", "3"):
                devices = local_devices(USBIPTools())
                if choice == "3":
                    print("\nLocal USB devices")
                    for number, device in enumerate(devices, 1):
                        print(f"  {number}. {device.label}")
                    if not devices:
                        print("No USB devices found. Check the cable and try again.")
                    continue
                device = choose_device(devices, heading="Choose the USB device plugged into this computer")
                if device is None:
                    continue
                if choice == "1":
                    print("Sharing temporarily disconnects this device from applications on this computer.")
                    print("Choose a device you can spare, rather than the keyboard, network adapter, or disk you need.")
                    if _read(f"Share {device.name}? [y/N]: ").lower() not in ("y", "yes"):
                        print("Cancelled. The device was left as it was.")
                        continue
                    if run_command("server", ["bind", "--busid", device.busid], elevated=True) == 0:
                        print(f"Device selected for sharing: {device.name} (bus {device.busid}).")
                        _start_service()
                    else:
                        print("The device could not be shared. Check the error above.")
                elif run_command("server", ["unbind", "--busid", device.busid], elevated=True) == 0:
                    print("Sharing stopped. Reconnect the USB device if it does not return to local use.")
            elif choice == "4":
                _start_service()
            elif choice == "5":
                run_command("server", ["doctor"])
            else:
                print("Choose one of the menu numbers, or 0 to go back.")
        except USBIPError as error:
            print(f"\n{error}")


def _show_connections(connections):
    print("\nConnected remote USB devices")
    if not connections:
        print("No remote USB devices are connected to this computer.")
    for number, connection in enumerate(connections, 1):
        print(f"  {number}. {connection.label}")


def client_menu() -> int:
    previous_host = None
    while True:
        print("\nUse USB devices shared by another computer")
        print("  1. Find and connect a USB device")
        print("  2. Show connected remote devices")
        print("  3. Disconnect a remote USB device")
        print("  4. Check prerequisites")
        print("  5. Connect by address")
        print("  0. Back / quit")
        choice = _read("Choose: ").lower()
        if choice in ("0", "q"):
            return 0
        try:
            if choice in ("1", "5"):
                tools = USBIPTools()
                if choice == "1":
                    print("Searching nearby LAN addresses for shared USB devices on port 3240...")
                    available = scan_available_devices(tools)
                    if not available:
                        print("No shared USB devices found nearby. Share a device on the other computer first.")
                        print("For another network, a VPN, or a blocked lookup, choose 'Connect by address' (5).")
                        continue
                    selected = choose_device(available, heading="Available USB devices on your network")
                    if selected is None:
                        continue
                    address, device = selected.address, selected.device
                else:
                    address = ask_host(previous_host)
                    if address is None:
                        continue
                    devices = remote_devices(tools, address)
                    if not devices:
                        print("No devices are being shared. Choose 'Share a USB device' on the other computer first.")
                        continue
                    device = choose_device(devices, heading=f"Devices shared by {address}")
                    if device is None:
                        continue
                previous_host = address
                before = snapshot_connections(tools)
                status = run_command(
                    "client", ["attach", "--host", address, "--busid", device.busid], elevated=True,
                )
                if status == 0:
                    print("Waiting for the USB device to appear on this computer...")
                    connection = wait_for_connection(tools, device, address, previous=before)
                    if connection is not None:
                        print(f"Connected {device.name}. It is now available to applications on this computer.")
                        _show_connections([connection])
                    else:
                        print("Connection requested, but the device is not visible yet.")
                        print("Wait, then choose 'Show connected remote devices' before trying to attach again.")
                else:
                    print("The connection did not complete. Check connected devices before trying again.")
            elif choice == "2":
                _show_connections(connected_devices(USBIPTools()))
            elif choice == "3":
                connections = connected_devices(USBIPTools())
                if not connections:
                    print("No remote USB devices are connected to this computer.")
                    continue
                connection = choose_device(connections, heading="Choose the remote USB device to disconnect")
                if connection is None:
                    continue
                if run_command("client", ["detach", "--port", str(connection.port)], elevated=True) == 0:
                    print(f"Disconnected {connection.name} from this computer.")
            elif choice == "4":
                run_command("client", ["doctor"])
            else:
                print("Choose one of the menu numbers, or 0 to go back.")
        except USBIPError as error:
            print(f"\n{error}")


def main_menu() -> int:
    while True:
        print("\nUSB over IP")
        print("  1. Share a USB device plugged into this computer")
        print("  2. Use a remote USB device from another computer")
        print("  0. Quit")
        choice = _read("Choose: ").lower()
        if choice in ("0", "q"):
            return 0
        if choice == "1":
            server_menu()
        elif choice == "2":
            client_menu()
        else:
            print("Choose 1, 2, or 0.")
