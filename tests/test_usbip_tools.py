"""Native command boundary tests; no kernel or Windows installation required."""

import contextlib
import ctypes
import io
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from usbip_tools import USBIPError, USBIPTools


class ToolTests(unittest.TestCase):
    def test_supported_platform_names_are_case_insensitive(self):
        for name, expected in (("Linux", "Linux"), ("linux", "Linux"), ("WINDOWS", "Windows")):
            with self.subTest(name=name):
                self.assertEqual(USBIPTools(name).system, expected)

    def test_unsupported_platform_has_clear_error(self):
        with self.assertRaisesRegex(USBIPError, "Unsupported operating system"):
            USBIPTools("Darwin")

    def test_native_arguments_are_passed_as_list_with_inherited_streams(self):
        tools = USBIPTools("Linux")
        completed = subprocess.CompletedProcess([], 17)
        with mock.patch.object(tools, "resolve", return_value="/native/usbip"):
            with mock.patch("usbip_tools.subprocess.run", return_value=completed) as run:
                status = tools.run("usbip", ["list", "-r", "192.0.2.1"])
        self.assertEqual(status, 17)
        args, options = run.call_args
        self.assertEqual(args, (["/native/usbip", "list", "-r", "192.0.2.1"],))
        self.assertFalse(options.get("shell", False))
        self.assertFalse(options.get("check", False))
        self.assertFalse(options.get("capture_output", False))
        self.assertEqual(options["timeout"], 30)

    def test_foreground_daemon_can_run_without_timeout(self):
        tools = USBIPTools("Linux")
        with mock.patch.object(tools, "resolve", return_value="/native/usbipd"):
            with mock.patch("usbip_tools.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(tools.run("usbipd", [], timeout=None), 0)
        self.assertIsNone(run.call_args.kwargs["timeout"])

    def test_launch_failures_are_converted_to_user_errors(self):
        failures = (
            (FileNotFoundError("gone"), "missing"),
            (PermissionError("denied"), "permissions"),
            (subprocess.TimeoutExpired("usbip", 30), "timed out"),
        )
        for failure, expected in failures:
            with self.subTest(failure=type(failure).__name__):
                tools = USBIPTools("Linux")
                with mock.patch.object(tools, "resolve", return_value="/native/usbip"):
                    with mock.patch("usbip_tools.subprocess.run", side_effect=failure):
                        with self.assertRaisesRegex(USBIPError, expected):
                            tools.run("usbip", ["list", "-l"])

    def test_keyboard_interrupt_is_not_hidden(self):
        tools = USBIPTools("Linux")
        with mock.patch.object(tools, "resolve", return_value="/native/usbip"):
            with mock.patch("usbip_tools.subprocess.run", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    tools.run("usbip", ["list", "-l"])

    def test_linux_resolver_finds_sbin_when_missing_from_user_path(self):
        with mock.patch("usbip_tools.shutil.which", side_effect=[None, "/usr/sbin/usbip"]) as which:
            self.assertEqual(USBIPTools("Linux").resolve("usbip"), "/usr/sbin/usbip")
        self.assertEqual(which.call_args_list[0], mock.call("usbip"))
        self.assertIn("/usr/sbin", which.call_args_list[1].kwargs["path"])

    def test_missing_binary_names_prerequisite_and_installation(self):
        for system, tool, hint in (
            ("Linux", "usbip", "apt install"),
            ("Windows", "usbip", "usbip-win2"),
            ("Windows", "usbipd", "usbipd-win"),
        ):
            with self.subTest(system=system, tool=tool):
                with mock.patch("usbip_tools.shutil.which", return_value=None):
                    with mock.patch("usbip_tools.Path.is_file", return_value=False):
                        with self.assertRaises(USBIPError) as raised:
                            USBIPTools(system).resolve(tool)
                self.assertIn(tool, str(raised.exception))
                self.assertIn(hint, str(raised.exception))

    def test_windows_resolver_finds_program_files_without_path_entry(self):
        for tool, folder in (("usbip", "USBip"), ("usbipd", "usbipd-win")):
            with self.subTest(tool=tool):
                with mock.patch.dict("os.environ", {"ProgramFiles": "/native/program-files"}):
                    with mock.patch("usbip_tools.shutil.which", return_value=None):
                        with mock.patch("usbip_tools.Path.is_file", return_value=True):
                            path = USBIPTools("Windows").resolve(tool)
                self.assertEqual(path, str(Path("/native/program-files") / folder / (tool + ".exe")))

    def test_linux_admin_check_rejects_non_root(self):
        with mock.patch("usbip_tools.os.geteuid", return_value=1000, create=True):
            with self.assertRaisesRegex(USBIPError, "sudo"):
                USBIPTools("Linux").require_admin()

    def test_linux_admin_check_accepts_root(self):
        with mock.patch("usbip_tools.os.geteuid", return_value=0, create=True):
            USBIPTools("Linux").require_admin()

    def test_windows_admin_check_requires_actual_elevation(self):
        for elevated in (0, 1):
            with self.subTest(elevated=elevated):
                windll = mock.Mock()
                windll.shell32.IsUserAnAdmin.return_value = elevated
                with mock.patch.object(ctypes, "windll", windll, create=True):
                    if elevated:
                        USBIPTools("Windows").require_admin()
                    else:
                        with self.assertRaisesRegex(USBIPError, "administrator"):
                            USBIPTools("Windows").require_admin()

    def test_windows_failure_to_check_elevation_is_not_accepted(self):
        windll = mock.Mock()
        windll.shell32.IsUserAnAdmin.side_effect = OSError("failed")
        with mock.patch.object(ctypes, "windll", windll, create=True):
            with self.assertRaisesRegex(USBIPError, "administrator"):
                USBIPTools("Windows").require_admin()

    def test_linux_module_failure_is_reported(self):
        tools = USBIPTools("Linux")
        with mock.patch.object(tools, "require_admin"):
            with mock.patch("usbip_tools.Path.is_dir", return_value=False):
                with mock.patch.object(tools, "run", return_value=1) as run:
                    with self.assertRaisesRegex(USBIPError, "modprobe vhci_hcd failed"):
                        tools.load_module("vhci_hcd")
        run.assert_called_once_with("modprobe", ["vhci_hcd"])

    def test_loaded_linux_module_does_not_require_modprobe_or_elevation(self):
        tools = USBIPTools("Linux")
        with mock.patch("usbip_tools.Path.is_dir", return_value=True):
            with mock.patch.object(tools, "require_admin") as admin:
                with mock.patch.object(tools, "run") as run:
                    tools.load_module("vhci_hcd")
        admin.assert_not_called()
        run.assert_not_called()

    def test_windows_cannot_load_linux_modules(self):
        tools = USBIPTools("Windows")
        with mock.patch.object(tools, "run") as run:
            with self.assertRaisesRegex(USBIPError, "Linux kernel modules"):
                tools.load_module("vhci_hcd")
        run.assert_not_called()

    def test_doctor_returns_failure_for_missing_required_tools_without_mutation(self):
        for system in ("Linux", "Windows"):
            for role in ("client", "server"):
                with self.subTest(system=system, role=role):
                    tools = USBIPTools(system)
                    with mock.patch.object(tools, "resolve", side_effect=USBIPError("missing usbip")):
                        with mock.patch.object(tools, "require_admin") as admin:
                            with mock.patch.object(tools, "load_module") as load:
                                with mock.patch("usbip_tools.Path.is_dir", return_value=False):
                                    with contextlib.redirect_stdout(io.StringIO()) as output:
                                        status = tools.doctor(role)
                    self.assertEqual(status, 1)
                    self.assertIn("missing usbip", output.getvalue())
                    admin.assert_not_called()
                    load.assert_not_called()

    def test_linux_doctor_accepts_loaded_module_without_modprobe(self):
        for role in ("client", "server"):
            with self.subTest(role=role):
                tools = USBIPTools("Linux")
                with mock.patch("usbip_tools.Path.is_dir", return_value=True):
                    with mock.patch.object(tools, "resolve", side_effect=lambda name: "/native/" + name) as resolve:
                        with mock.patch.object(tools, "run") as run:
                            with contextlib.redirect_stdout(io.StringIO()):
                                status = tools.doctor(role)
                self.assertEqual(status, 0)
                self.assertNotIn(mock.call("modprobe"), resolve.call_args_list)
                run.assert_not_called()

    def test_linux_doctor_checks_module_availability_without_loading_it(self):
        tools = USBIPTools("Linux")
        for native_status, expected in ((0, 0), (1, 1)):
            with self.subTest(native_status=native_status):
                completed = subprocess.CompletedProcess([], native_status, "", "module missing")
                with mock.patch("usbip_tools.Path.is_dir", return_value=False):
                    with mock.patch.object(tools, "resolve", side_effect=lambda name: "/native/" + name):
                        with mock.patch("usbip_tools.subprocess.run", return_value=completed) as run:
                            with contextlib.redirect_stdout(io.StringIO()):
                                status = tools.doctor("client")
                self.assertEqual(status, expected)
                self.assertEqual(run.call_args.args[0], ["/native/modprobe", "-n", "--verbose", "vhci_hcd"])

    def test_windows_doctor_requires_running_driver_or_service(self):
        for role, service in (("client", "usbip2_ude"), ("server", "usbipd")):
            for state, expected in ((4, 0), (1, 1), (2, 1)):
                with self.subTest(role=role, state=state):
                    tools = USBIPTools("Windows")
                    with mock.patch.object(tools, "resolve", side_effect=lambda name: "/native/" + name):
                        with mock.patch.object(tools, "_windows_service_state", return_value=state) as query:
                            with mock.patch.object(tools, "run") as run:
                                with contextlib.redirect_stdout(io.StringIO()):
                                    status = tools.doctor(role)
                    self.assertEqual(status, expected)
                    query.assert_called_once_with(service)
                    run.assert_not_called()

    def test_windows_doctor_reports_service_query_errors_as_not_ready(self):
        tools = USBIPTools("Windows")
        with mock.patch.object(tools, "resolve", side_effect=lambda name: "/native/" + name):
            with mock.patch.object(tools, "_windows_service_state", side_effect=USBIPError("service is missing")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    status = tools.doctor("client")
        self.assertEqual(status, 1)
        self.assertIn("service is missing", output.getvalue())

    def test_windows_service_query_closes_native_handles(self):
        api = mock.Mock()
        api.OpenSCManagerW.return_value = 100
        api.OpenServiceW.return_value = 200

        def query_status(handle, status):
            self.assertEqual(handle, 200)
            status._obj.current_state = 4
            return 1

        api.QueryServiceStatus.side_effect = query_status
        with mock.patch.object(ctypes, "WinDLL", return_value=api, create=True):
            state = USBIPTools("Windows")._windows_service_state("usbip2_ude")
        self.assertEqual(state, 4)
        api.OpenServiceW.assert_called_once_with(100, "usbip2_ude", 4)
        self.assertEqual(api.CloseServiceHandle.call_args_list, [mock.call(200), mock.call(100)])

    def test_windows_service_query_error_still_closes_handles(self):
        api = mock.Mock()
        api.OpenSCManagerW.return_value = 100
        api.OpenServiceW.return_value = 200
        api.QueryServiceStatus.return_value = 0
        with mock.patch.object(ctypes, "WinDLL", return_value=api, create=True):
            with mock.patch.object(ctypes, "get_last_error", return_value=5, create=True):
                with self.assertRaisesRegex(USBIPError, "Cannot query"):
                    USBIPTools("Windows")._windows_service_state("usbipd")
        self.assertEqual(api.CloseServiceHandle.call_args_list, [mock.call(200), mock.call(100)])


if __name__ == "__main__":
    unittest.main()
