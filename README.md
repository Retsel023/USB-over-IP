# USB over IP

Share USB devices between everyday computers on your LAN. Plug a device into
a spare laptop, desktop, or Raspberry Pi, then use it from another computer
over the network as though it were plugged in locally. The project uses free,
open-source USB/IP tools and operating-system drivers to carry real USB
requests over the network.

The two scripts describe each computer's role:

- **Sharing computer (server):** the computer with the physical USB device
  plugged into its ports. Run `server.py` here.
- **Receiving computer (client):** the computer where you want to use that
  remote USB device. Run `client.py` here.

For example, plug a device into a Pi and run `server.py` on the Pi, then run
`client.py` on your CachyOS laptop to use it. To share a device plugged into
the laptop instead, run `server.py` on the laptop. These are software roles;
the sharing computer can be an ordinary PC or Pi. No dedicated server hardware
or Windows Server edition is required.

Python provides the command-line interface; the installed USB/IP drivers do
the USB forwarding. The receiving computer still needs the normal driver for
the USB device, just as if you had plugged it in locally.

| Role | Linux | Windows |
| --- | --- | --- |
| Sharing computer (server): share a physically connected device | Kernel USB/IP host driver and `usbipd` | [usbipd-win](https://github.com/dorssel/usbipd-win) |
| Receiving computer (client): use a remote device | Kernel virtual host controller and `usbip` | [usbip-win2](https://github.com/vadimgrn/usbip-win2) native Windows client |

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

## Start here: guided menu

After installing the packages for your operating system below, open a terminal
in this repository and start the menu:

```bash
python3 usbip.py
```

On Windows, use `py -3 usbip.py` from an Administrator PowerShell.

- On the computer with the USB device plugged in, choose **Share a USB device**,
  then choose the device by number and confirm sharing. The menu starts the
  sharing service in the background if it is not already running, and shows
  this computer's network addresses.
- On the computer where you want to use it, choose **Use a remote USB device**,
  then **Find and connect a USB device**. The menu looks for sharing computers
  on your local network and lists their shared devices with each computer's
  address. Choose a device by number. You do not need to know its IP address,
  USB bus ID, or local port number.
- To disconnect, choose **Disconnect a remote USB device** and select the
  connected device by number. The menu handles its internal port number.
- To make the device available locally on the sharing computer again, first
  disconnect it on the receiving computer, then choose **Stop sharing a USB
  device** on the sharing computer.

Linux requests your `sudo` password only for an action that changes device or
service state. Browsing devices and checking prerequisites do not need root.
You can keep using the menu while sharing multiple devices. Exiting the menu
leaves the background service and shared devices running; use **Stop sharing**
when finished. The menu does not configure automatic sharing after a reboot.

Automatic lookup checks TCP port **3240** on nearby private IPv4 LAN addresses,
then asks responding computers which USB devices they share. A device must
already be shared on the other computer to appear. Lookup is limited to 512
addresses in total; on networks larger than a `/24`, it checks the `/24` around
your local address. Smaller subnets keep their actual boundaries. If interface
prefixes cannot be read, only this computer's own addresses are checked.
Choose **Connect by address** (option 5) for a computer on another subnet, a
VPN, IPv6, or a network that blocks automatic lookup.

After connecting, the menu waits for the device to appear locally before
confirming success. If registration is still pending, use **Show connected
remote devices** before attempting another connection.

Starting `python3 server.py` or `python3 client.py` without arguments in a
terminal opens the corresponding menu directly. The explicit `menu` subcommand
also opens it. The manual commands below remain available for scripts and
troubleshooting.

Run the examples from this repository's directory. Replace `192.168.1.50` with
your server's address, and `1-2` with a bus ID reported by `server.py list`.
Select a device by bus ID, rather than its vendor/product IDs, so that identical
devices can be selected individually. Devices are never shared automatically.

## Linux package installation

Install these packages on each Linux machine, whether it is the server or the
client. Choose the commands for that machine's distribution.

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

On Arch Linux and Arch-based distributions such as **CachyOS**:

```bash
sudo pacman -Syu --needed python usbip usbutils kmod
```

The Arch [`usbip` package](https://archlinux.org/packages/extra/x86_64/usbip/)
provides both `usbip` and `usbipd` for the client and server roles. Python is
packaged as `python`; the `python3` commands below also work on Arch/CachyOS.
The `-Syu` command performs a full system upgrade as well as installing these
packages. If it updates your kernel, reboot before running the module commands
below so that the running kernel matches the installed modules.

Other distributions need their equivalent USB/IP tools and a kernel built with
USB/IP host support. The tools must match the running kernel where required by
the distribution. If `modprobe` reports a missing module, install the matching
kernel module package or use a kernel with USB/IP enabled.

## Share devices from Linux or a Raspberry Pi

Use this section on the Linux computer where the USB device is physically
plugged in, including a CachyOS laptop or a Pi running Raspberry Pi OS.

After installing the [Linux packages](#linux-package-installation), load the
server module and share the device:

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

For background sharing, the guided menu uses `server.py start`, which starts
Linux `usbipd` in its [native daemon mode](https://man.archlinux.org/man/usbipd.8.en).
The `serve` command remains available when you want foreground diagnostic logs.

To stop sharing, first detach on the client, then use another server terminal:

```bash
sudo python3 server.py unbind --busid 1-2
```

Press Ctrl+C in the `serve` terminal to stop that daemon. Stopping the daemon
does not unbind devices. After unbinding, Linux may require unplugging and
replugging the device or rebinding its native driver before it works locally
again. Repeat the bind step after a server reboot or device reconnection as
needed; bus IDs can change.

## Share devices from a Windows PC

Use this section if the USB device is plugged into a Windows laptop or desktop.
Here, "server" means that the PC shares the device with another computer.

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

## Use remote devices on Linux

Use this section on the Linux computer that will use the remote USB device,
such as your CachyOS laptop when the device is plugged into a Pi.

Install the [Linux packages](#linux-package-installation) for your client's
distribution, including the `pacman` command for Arch/CachyOS. Its kernel needs
the USB/IP virtual host controller module:

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

This local port is assigned when the device is attached; it is different from
the USB bus ID on the sharing computer. The guided menu lists connected
devices by name and finds this port for you.

## Use remote devices on Windows

Install a **signed driver release, version 0.9.8.1 or newer**, of
[usbip-win2](https://github.com/vadimgrn/usbip-win2/releases), selecting the
installer for your Windows architecture. Its supported platforms are Windows
10 x64 version 1903 or newer, and Windows 11 ARM64. Follow upstream's release
installation instructions and restore-point guidance. Use Microsoft's normal
driver signature verification; no test-signing mode is required for a signed
release.

The Windows device-sharing package `usbipd-win` does not provide this native
client driver. Its WSL attachment command makes a device available inside WSL; use
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
