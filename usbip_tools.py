"""Small, dependency-free wrappers around the operating system's USB/IP tools."""

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional


class USBIPError(Exception):
    """A missing prerequisite or failure to launch a native USB/IP command."""


class _ServiceStatus(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "service_type", "current_state", "controls_accepted", "win32_exit_code",
            "service_exit_code", "checkpoint", "wait_hint",
        )
    ]


class USBIPTools:
    """Find and invoke native tools without a shell or a custom USB driver."""

    def __init__(self, system: Optional[str] = None):
        detected = platform.system() if system is None else system
        supported = {"linux": "Linux", "windows": "Windows"}
        try:
            self.system = supported[detected.lower()]
        except (AttributeError, KeyError):
            raise USBIPError(
                f"Unsupported operating system {detected!r}; use Linux or Windows."
            ) from None

    def _install_hint(self, tool: str) -> str:
        if self.system == "Linux":
            return (
                "On Debian/Raspberry Pi OS install: sudo apt install usbip usbutils kmod. "
                "On Ubuntu install: sudo apt install linux-tools-generic "
                'linux-tools-$(uname -r) usbutils kmod.'
            )
        if tool.removesuffix(".exe").lower() == "usbipd":
            return (
                "Install usbipd-win from https://github.com/dorssel/usbipd-win "
                "and reopen your terminal."
            )
        if tool.removesuffix(".exe").lower() == "sc":
            return "Restore the Windows Service Control utility (System32/sc.exe)."
        return (
            "Install the signed v0.9.8.1 or newer driver and client from "
            "https://github.com/vadimgrn/usbip-win2 and reopen your terminal."
        )

    def resolve(self, tool: str) -> str:
        """Return an executable path, including standard non-PATH locations."""
        executable = shutil.which(tool)
        if executable:
            return executable

        if self.system == "Linux":
            executable = shutil.which(tool, path="/usr/sbin:/sbin:/usr/bin:/bin")
            if executable:
                return executable
        else:
            name = tool.removesuffix(".exe").lower()
            candidates = []
            if name in ("usbip", "usbipd"):
                folder = "USBip" if name == "usbip" else "usbipd-win"
                for variable, default in (
                    ("ProgramFiles", r"C:\Program Files"),
                    ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                ):
                    candidates.append(
                        Path(os.environ.get(variable, default)) / folder / f"{name}.exe"
                    )
            elif name == "sc":
                candidates.append(
                    Path(os.environ.get("SystemRoot", r"C:\Windows"))
                    / "System32" / "sc.exe"
                )
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)

        raise USBIPError(f"Required command {tool!r} was not found. {self._install_hint(tool)}")

    def _call(
        self,
        tool: str,
        args: Iterable[str],
        *,
        timeout: Optional[float] = 30,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        command = [self.resolve(tool), *args]
        options = {"check": False, "timeout": timeout}
        if capture:
            options.update(capture_output=True, text=True)
        try:
            return subprocess.run(command, **options)
        except FileNotFoundError:
            raise USBIPError(
                f"Cannot launch {tool!r}; its executable is missing. {self._install_hint(tool)}"
            ) from None
        except subprocess.TimeoutExpired:
            raise USBIPError(
                f"{tool!r} timed out after {timeout} seconds; check the remote host "
                "and the native tool's configuration. The operation may already "
                "have changed device state; run python client.py port before "
                "retrying an attach."
            ) from None
        except OSError as error:
            raise USBIPError(
                f"Cannot run {tool!r}: {error}. Check its installation and permissions."
            ) from None

    def run(
        self, tool: str, args: Iterable[str], *, timeout: Optional[float] = 30
    ) -> int:
        """Run with inherited streams; return the native tool's exit status."""
        return self._call(tool, args, timeout=timeout).returncode

    def require_admin(self) -> None:
        """Require elevation before changing drivers, modules, or device state."""
        if self.system == "Linux":
            if os.geteuid() != 0:
                raise USBIPError("This operation requires root; rerun it with sudo.")
            return
        try:
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            raise USBIPError(
                "Cannot verify Windows administrator privileges; run this command "
                "in a Windows terminal opened with Run as administrator."
            ) from None
        if not elevated:
            raise USBIPError(
                "This operation requires administrator privileges; open a terminal "
                "with Run as administrator and rerun it."
            )

    def load_module(self, name: str) -> None:
        """Load a required Linux kernel module, reporting native failures."""
        if self.system != "Linux":
            raise USBIPError("Linux kernel modules cannot be loaded on Windows.")
        if (Path("/sys/module") / name).is_dir():
            return
        self.require_admin()
        result = self.run("modprobe", [name])
        if result:
            raise USBIPError(
                f"modprobe {name} failed (exit {result}). Install modules matching "
                "the running kernel and run on a host that permits loading them."
            )

    def _linux_module_ready(self, name: str, has_modprobe: bool) -> bool:
        if (Path("/sys/module") / name).is_dir():
            print(f"Kernel module {name}: loaded")
            return True
        if not has_modprobe:
            print(f"Kernel module {name}: cannot check availability without modprobe")
            return False
        try:
            result = self._call("modprobe", ["-n", "--verbose", name], capture=True)
        except USBIPError as error:
            print(f"Kernel module {name}: {error}")
            return False
        if result.returncode == 0:
            print(f"Kernel module {name}: available; the elevated command will load it")
            return True
        detail = (result.stderr or result.stdout or "modprobe could not find the module").strip()
        print(f"Kernel module {name}: unavailable ({detail})")
        print("Install modules for the running kernel; containers may not provide USB/IP access.")
        return False

    def _windows_service_state(self, service: str) -> int:
        """Read SCM's numeric state without parsing localized command output."""
        try:
            api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except (AttributeError, OSError) as error:
            raise USBIPError(f"Cannot access Windows Service Control Manager: {error}") from None
        api.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.OpenSCManagerW.restype = ctypes.c_void_p
        api.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.OpenServiceW.restype = ctypes.c_void_p
        api.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ServiceStatus)]
        api.QueryServiceStatus.restype = ctypes.c_int
        api.CloseServiceHandle.argtypes = [ctypes.c_void_p]
        api.CloseServiceHandle.restype = ctypes.c_int

        manager = api.OpenSCManagerW(None, None, 1)  # SC_MANAGER_CONNECT
        if not manager:
            raise USBIPError(
                f"Cannot open Windows Service Control Manager (error {ctypes.get_last_error()})."
            )
        try:
            handle = api.OpenServiceW(manager, service, 4)  # SERVICE_QUERY_STATUS
            if not handle:
                raise USBIPError(
                    f"Cannot open {service!r} (Windows error {ctypes.get_last_error()})."
                )
            try:
                status = _ServiceStatus()
                if not api.QueryServiceStatus(handle, ctypes.byref(status)):
                    raise USBIPError(
                        f"Cannot query {service!r} (Windows error {ctypes.get_last_error()})."
                    )
                return status.current_state
            finally:
                api.CloseServiceHandle(handle)
        finally:
            api.CloseServiceHandle(manager)

    def _windows_service_ready(self, role: str) -> bool:
        service = "usbip2_ude" if role == "client" else "usbipd"
        label = "USB/IP virtual USB controller driver" if role == "client" else "USB/IP server service"
        try:
            state = self._windows_service_state(service)
        except USBIPError as error:
            print(f"{label}: {error}")
            state = None
        if state == 4:  # SERVICE_RUNNING
            print(f"{label}: running ({service})")
            return True
        print(f"{label}: not running or could not be verified (sc query {service})")
        tool = "usbip" if role == "client" else "usbipd"
        print(self._install_hint(tool))
        print("Finish the native driver/service installation and start it from an elevated terminal.")
        return False

    def doctor(self, role: str) -> int:
        """Inspect prerequisites without elevating, loading modules, or changing devices."""
        if role not in ("client", "server"):
            raise USBIPError("Doctor role must be 'client' or 'server'.")
        if self.system == "Linux":
            module = "usbip_host" if role == "server" else "vhci_hcd"
            required = ("usbip",)
            if not (Path("/sys/module") / module).is_dir():
                required += ("modprobe",)
            if role == "server":
                required += ("usbipd",)
        else:
            required = ("usbip",) if role == "client" else ("usbipd", "sc")

        print(f"USB/IP {role} prerequisites on {self.system}:")
        found = {}
        ready = True
        for tool in required:
            try:
                found[tool] = self.resolve(tool)
                print(f"{tool}: {found[tool]}")
            except USBIPError as error:
                print(error)
                ready = False

        if self.system == "Linux":
            ready = self._linux_module_ready(module, "modprobe" in found) and ready
            if role == "server":
                usb_sysfs = Path("/sys/bus/usb/devices")
                if usb_sysfs.is_dir():
                    print(f"USB device sysfs: {usb_sysfs}")
                else:
                    print("USB device sysfs is missing; use a Linux host with physical USB access.")
                    ready = False
        else:
            ready = self._windows_service_ready(role) and ready
        return 0 if ready else 1
