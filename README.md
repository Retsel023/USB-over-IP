# USB over IP

Use a USB device plugged into another computer across your LAN, including a
Raspberry Pi server and a Windows or Linux client. The project uses free,
open-source USB/IP tools and operating-system drivers to carry real USB
requests over the network.

`server.py` manages the machine where the USB device is physically plugged in.
`client.py` attaches that device to the machine where you want to use it. Python
provides the command-line interface; the installed USB/IP drivers do the USB
forwarding. The client still needs the normal driver for the USB device, just
as if you had plugged it in locally.

| Role | Linux | Windows |
| --- | --- | --- |
| Server: share a physically connected device | Kernel USB/IP host driver and `usbipd` | [usbipd-win](https://github.com/dorssel/usbipd-win) |
| Client: use a remote device | Kernel virtual host controller and `usbip` | [usbip-win2](https://github.com/vadimgrn/usbip-win2) native Windows client |

Either server can be used with either client through the USB/IP protocol.
Specific devices and driver combinations need to be tested on your hardware.

## Requirements

- Python 3.9 or newer on each machine running these scripts. No Python packages
  or `pip install` step are required.
- The USB/IP software and kernel drivers for each machine's role, installed as
  described below.
- A reachable server address and TCP port **3240** allowed from your client.
- Root privileges on Linux or an Administrator terminal on Windows for sharing,
  attaching, detaching, and starting the server.

USB/IP has no built-in authentication or encryption. Use a trusted LAN or VPN,
and limit the server's firewall rule to the clients that should have access.
Do not expose port 3240 to the public Internet.

Run the examples from this repository's directory. Replace `192.168.1.50` with
your server's address, and `1-2` with a bus ID reported by `server.py list`.
Select a device by bus ID, rather than its vendor/product IDs, so that identical
devices can be selected individually. Devices are never shared automatically.

## Linux or Raspberry Pi server

On Debian or Raspberry Pi OS:

```bash
sudo apt update
sudo apt install python3 usbip usbutils kmod
```

On Ubuntu, USB/IP tools are usually provided by the kernel tools packages:

```bash
sudo apt update
sudo apt install python3 linux-tools-generic linux-tools-$(uname -r) usbutils kmod
```

Other distributions need their equivalent USB/IP tools and a kernel built with
USB/IP host support. The tools must match the running kernel where required by
the distribution. If `modprobe` reports a missing module, install the matching
kernel module package or use a kernel with USB/IP enabled.

```bash
sudo modprobe usbip_host
python3 server.py doctor
python3 server.py list
sudo python3 server.py bind --busid 1-2
sudo python3 server.py serve
```

Keep `serve` running in this terminal while the client connects. It starts the
system `usbipd` in the foreground. If a USB/IP daemon is already listening on
port 3240, use that daemon instead of starting a second one. Binding hands the
device over to USB/IP, disconnecting it from local use; do not bind the only
keyboard, a network adapter providing your connection, or a mounted disk you
are using.

To stop sharing, first detach on the client, then use another server terminal:

```bash
sudo python3 server.py unbind --busid 1-2
```

Press Ctrl+C in the `serve` terminal to stop that daemon. Stopping the daemon
does not unbind devices. After unbinding, Linux may require unplugging and
replugging the device or rebinding its native driver before it works locally
again. Repeat the bind step after a server reboot or device reconnection as
needed; bus IDs can change.

## Windows server

Install [usbipd-win's official release](https://github.com/dorssel/usbipd-win/releases/latest)
for your Windows version and architecture. It supports Windows 10 or Windows
Server 2019 version 1809 and newer on x64/ARM64. For example:

```powershell
winget install --exact --id dorssel.usbipd-win
```

Open a new **Administrator PowerShell** in this repository after installation:

```powershell
py -3 server.py doctor
py -3 server.py list
py -3 server.py bind --busid 1-2
```

The installer normally starts the `usbipd` Windows service automatically and
adds a firewall rule for local subnets. Review that rule to restrict access to
your intended clients. If the installed service is stopped, request its start
and check the result:

```powershell
py -3 server.py serve
py -3 server.py doctor
```

On Windows, `serve` starts the existing service and exits; it does not create a
Python daemon. The Windows service continues running after the command exits.
Sharing persists across reboots with usbipd-win. While a client has the device
attached, Windows cannot use it locally. To remove sharing after detaching on
the client:

```powershell
py -3 server.py unbind --busid 1-2
```

## Linux client

Install the Linux packages described above on the client as well. Its kernel
needs the USB/IP virtual host controller module:

```bash
sudo modprobe vhci_hcd
python3 client.py doctor
python3 client.py list --host 192.168.1.50
sudo python3 client.py attach --host 192.168.1.50 --busid 1-2
python3 client.py port
```

The device should appear to Linux as a locally attached USB device. Check with
`lsusb` and your usual application. To detach, use the local port shown by
`client.py port`, for example:

```bash
sudo python3 client.py detach --port 0
```

## Native Windows client

Install a **signed driver release, version 0.9.8.1 or newer**, of
[usbip-win2](https://github.com/vadimgrn/usbip-win2/releases), selecting the
installer for your Windows architecture. Its supported platforms are Windows
10 x64 version 1903 or newer, and Windows 11 ARM64. Follow upstream's release
installation instructions and restore-point guidance. Use Microsoft's normal
driver signature verification; no test-signing mode is required for a signed
release.

The Windows server package `usbipd-win` does not provide this native client
driver. Its WSL attachment command makes a device available inside WSL; use
`usbip-win2` to make it available to ordinary Windows applications.

Open **Administrator PowerShell** in this repository:

```powershell
py -3 client.py doctor
py -3 client.py list --host 192.168.1.50
py -3 client.py attach --host 192.168.1.50 --busid 1-2
py -3 client.py port
```

Check Device Manager and the application that uses the device. Install its
normal Windows device driver if needed. To detach, use the local port shown by
`client.py port`, for example:

```powershell
py -3 client.py detach --port 1
```

Port numbers come from the client driver and can differ between Linux and
Windows. They are separate from the server's USB bus ID. If attach times out,
run `client.py port` before retrying: the driver may already have attached the
device.

## Check a real connection

1. Plug a nonessential USB device into the server. Run `server.py doctor` and
   `server.py list`, then bind that device and start the server as above.
2. On the client, run `client.py doctor` and `client.py list --host SERVER_IP`.
   Confirm the exported device and bus ID are present.
3. Attach that bus ID, inspect `client.py port`, and confirm the device appears
   in `lsusb` or Windows Device Manager.
4. Use the device in its normal application. A successful device listing alone
   does not demonstrate USB transfers.
5. Close the application, detach the client port, and unbind on the server.
   Confirm the device returns to local use, reconnecting it if necessary.

A device can be attached to only one client at a time. Detach it before using
it elsewhere. Check `client.py port` after a reboot, unplug/replug, or a broken
connection before attaching again. The Python scripts do not implement
reconnection; the native Windows driver may retry after a dropped connection.
The Windows attach command uses `--once` to prevent background retries when
the initial attachment fails. To cancel native Windows background retries for
a device, use `usbip.exe attach -r SERVER_IP -b BUSID --stop` from an
Administrator terminal. Network latency and device drivers affect
compatibility. Audio interfaces, webcams, and other real-time or
high-bandwidth devices may not work reliably with every driver or network.

If a command fails, read its reported native-tool error and rerun `doctor` for
the relevant role. Check the installed tool, loaded kernel module or Windows
driver, administrator privileges, server service, selected bus ID, and TCP
3240 firewall access. The scripts look for installed tools on `PATH` and in
their standard Windows installation directories.

## Development and validation

The Python code uses only the standard library. From the repository root:

```bash
python3 -m unittest discover -s tests -v
```

On Windows use `py -3 -m unittest discover -s tests -v`. Tests exercise the
command wrappers and error handling without requiring physical USB hardware.
Hardware forwarding requires the smoke test above on real Linux/Windows
machines with the corresponding drivers. A cloud container without USB/IP
kernel modules, physical USB devices, or a Windows host cannot validate those
hardware transfers.
