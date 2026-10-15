"""Exercise the command routing without requiring USB hardware or drivers."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import client
import server
from usbip_tools import USBIPError


ROOT = Path(__file__).resolve().parents[1]


class CommandTests(unittest.TestCase):
    def invoke(self, module, args, system="Linux", result=0, error=None):
        tools = mock.Mock()
        tools.system = system
        tools.run.return_value = result
        tools.doctor.return_value = result
        if error is not None:
            tools.run.side_effect = error
        output = io.StringIO()
        with mock.patch.object(module, "USBIPTools", return_value=tools):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                status = module.main(args)
        return status, tools, output.getvalue()

    def test_doctor_passes_role_and_status(self):
        for module, role in ((server, "server"), (client, "client")):
            with self.subTest(role=role):
                status, tools, _ = self.invoke(module, ["doctor"], result=1)
                self.assertEqual(status, 1)
                tools.doctor.assert_called_once_with(role)
                tools.run.assert_not_called()

    def test_server_linux_list_is_read_only(self):
        status, tools, _ = self.invoke(server, ["list"])
        self.assertEqual(status, 0)
        tools.run.assert_called_once_with("usbip", ["list", "-l"])
        tools.require_admin.assert_not_called()
        tools.load_module.assert_not_called()

    def test_server_windows_list(self):
        status, tools, _ = self.invoke(server, ["list"], system="Windows")
        self.assertEqual(status, 0)
        tools.run.assert_called_once_with("usbipd", ["list"])
        tools.require_admin.assert_not_called()

    def test_server_linux_bind_loads_module_before_binding(self):
        status, tools, _ = self.invoke(server, ["bind", "--busid", "1-2.3"])
        self.assertEqual(status, 0)
        self.assertEqual(tools.mock_calls, [
            mock.call.require_admin(),
            mock.call.load_module("usbip_host"),
            mock.call.run("usbip", ["bind", "-b", "1-2.3"]),
        ])

    def test_server_linux_unbind_does_not_load_module(self):
        status, tools, _ = self.invoke(server, ["unbind", "--busid", "1-2"])
        self.assertEqual(status, 0)
        tools.require_admin.assert_called_once_with()
        tools.load_module.assert_not_called()
        tools.run.assert_called_once_with("usbip", ["unbind", "-b", "1-2"])

    def test_server_windows_binding_commands(self):
        for command in ("bind", "unbind"):
            with self.subTest(command=command):
                status, tools, _ = self.invoke(
                    server, [command, "--busid", "3-7"], system="Windows"
                )
                self.assertEqual(status, 0)
                tools.require_admin.assert_called_once_with()
                tools.load_module.assert_not_called()
                tools.run.assert_called_once_with(
                    "usbipd", [command, "--busid", "3-7"]
                )

    def test_server_linux_serve_runs_in_foreground_without_timeout(self):
        status, tools, _ = self.invoke(server, ["serve"])
        self.assertEqual(status, 0)
        self.assertEqual(tools.mock_calls, [
            mock.call.require_admin(),
            mock.call.load_module("usbip_host"),
            mock.call.run("usbipd", [], timeout=None),
        ])

    def test_server_windows_serve_accepts_already_running_service(self):
        for native_status, expected in ((0, 0), (1056, 0), (5, 5)):
            with self.subTest(native_status=native_status):
                status, tools, _ = self.invoke(
                    server, ["serve"], system="Windows", result=native_status
                )
                self.assertEqual(status, expected)
                tools.require_admin.assert_called_once_with()
                tools.run.assert_called_once_with("sc", ["start", "usbipd"])

    def test_client_lists_remote_host_without_admin(self):
        for system in ("Linux", "Windows"):
            for host in ("usb.example.test", "192.0.2.10", "2001:db8::5"):
                with self.subTest(system=system, host=host):
                    status, tools, _ = self.invoke(
                        client, ["list", "--host", host], system=system
                    )
                    self.assertEqual(status, 0)
                    tools.run.assert_called_once_with("usbip", ["list", "-r", host])
                    tools.require_admin.assert_not_called()

    def test_client_linux_attach_loads_virtual_host_controller(self):
        status, tools, _ = self.invoke(
            client, ["attach", "--host", "192.0.2.10", "--busid", "1-2"]
        )
        self.assertEqual(status, 0)
        self.assertEqual(tools.mock_calls, [
            mock.call.require_admin(),
            mock.call.load_module("vhci_hcd"),
            mock.call.run("usbip", ["attach", "-r", "192.0.2.10", "-b", "1-2"]),
        ])

    def test_windows_attach_uses_once_without_linux_module(self):
        status, tools, _ = self.invoke(
            client, ["attach", "--host", "192.0.2.10", "--busid", "1-2"],
            system="Windows",
        )
        self.assertEqual(status, 0)
        tools.require_admin.assert_called_once_with()
        tools.load_module.assert_not_called()
        tools.run.assert_called_once_with(
            "usbip", ["attach", "-r", "192.0.2.10", "-b", "1-2", "--once"]
        )

    def test_windows_detach_rejects_port_zero_before_native_command(self):
        status, tools, error = self.invoke(
            client, ["detach", "--port", "0"], system="Windows"
        )
        self.assertEqual(status, 1)
        tools.run.assert_not_called()
        self.assertTrue(error.strip())

    def test_client_detach_uses_local_port_and_preserves_failure(self):
        status, tools, _ = self.invoke(client, ["detach", "--port", "0"], result=7)
        self.assertEqual(status, 7)
        tools.require_admin.assert_called_once_with()
        tools.run.assert_called_once_with("usbip", ["detach", "-p", "0"])

    def test_client_port_is_read_only(self):
        status, tools, _ = self.invoke(client, ["port"])
        self.assertEqual(status, 0)
        tools.run.assert_called_once_with("usbip", ["port"])
        tools.require_admin.assert_not_called()

    def test_native_failure_status_propagates(self):
        for module, args in ((server, ["list"]), (client, ["port"])):
            with self.subTest(module=module.__name__):
                status, _, _ = self.invoke(module, args, result=12)
                self.assertEqual(status, 12)

    def test_native_errors_have_actionable_stderr(self):
        for module, args in ((server, ["list"]), (client, ["port"])):
            with self.subTest(module=module.__name__):
                status, _, error = self.invoke(module, args, error=USBIPError("missing driver"))
                self.assertEqual(status, 1)
                self.assertIn("missing driver", error)

    def test_keyboard_interrupt_returns_conventional_status(self):
        for module, args in ((server, ["serve"]), (client, ["port"])):
            with self.subTest(module=module.__name__):
                status, _, _ = self.invoke(module, args, error=KeyboardInterrupt())
                self.assertEqual(status, 130)

    def test_permission_denial_prevents_mutating_native_commands(self):
        cases = (
            (server, ["bind", "--busid", "1-2"]),
            (server, ["unbind", "--busid", "1-2"]),
            (server, ["serve"]),
            (client, ["attach", "--host", "localhost", "--busid", "1-2"]),
            (client, ["detach", "--port", "1"]),
        )
        for system in ("Linux", "Windows"):
            for module, args in cases:
                with self.subTest(system=system, args=args):
                    tools = mock.Mock(system=system)
                    tools.require_admin.side_effect = USBIPError("requires elevation")
                    with mock.patch.object(module, "USBIPTools", return_value=tools):
                        with contextlib.redirect_stderr(io.StringIO()):
                            status = module.main(args)
                    self.assertEqual(status, 1)
                    tools.load_module.assert_not_called()
                    tools.run.assert_not_called()


class ArgumentTests(unittest.TestCase):
    def assert_invalid(self, module, args):
        with mock.patch.object(module, "USBIPTools") as factory:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    module.main(args)
        self.assertEqual(raised.exception.code, 2)
        factory.assert_not_called()

    def test_no_command_or_unknown_command_is_an_error(self):
        for module in (server, client):
            for args in ([], ["bogus"]):
                with self.subTest(module=module.__name__, args=args):
                    self.assert_invalid(module, args)

    def test_required_arguments_are_checked_before_tools(self):
        cases = (
            (server, ["bind"]),
            (server, ["unbind"]),
            (client, ["list"]),
            (client, ["attach", "--host", "localhost"]),
            (client, ["attach", "--busid", "1-2"]),
            (client, ["detach"]),
        )
        for module, args in cases:
            with self.subTest(module=module.__name__, args=args):
                self.assert_invalid(module, args)

    def test_malformed_busids_cannot_reach_native_commands(self):
        for busid in ("", "1", "1-", "-1-2", "1-2;echo", "1-2/3", "1-2\n", "1-" + "2" * 30):
            for module, prefix in (
                (server, ["bind"]),
                (client, ["attach", "--host", "localhost"]),
            ):
                with self.subTest(busid=busid, module=module.__name__):
                    self.assert_invalid(module, prefix + ["--busid", busid])

    def test_host_must_be_hostname_or_ip_not_option_or_url(self):
        for host in ("", "-evil", "two hosts", "host\nname", "https://host", "host/path", "user@host"):
            with self.subTest(host=host):
                self.assert_invalid(client, ["list", "--host", host])

    def test_invalid_detach_ports_are_rejected(self):
        for port in ("-1", "65536", "abc", "1.0"):
            with self.subTest(port=port):
                self.assert_invalid(client, ["detach", "--port", port])


class ProcessTests(unittest.TestCase):
    def test_importing_scripts_does_not_connect_to_or_open_sockets(self):
        code = (
            "from unittest.mock import patch; "
            "p=patch('socket.socket', side_effect=AssertionError('socket on import')); "
            "p.start(); import client, server; p.stop()"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses a Linux executable fixture")
    def test_real_cli_preserves_native_output_and_return_code(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "usbip"
            executable.write_text(
                "#!" + sys.executable + "\n"
                "import os, sys\n"
                "print('native arguments: ' + '|'.join(sys.argv[1:]))\n"
                "print('native diagnostic', file=sys.stderr)\n"
                "sys.exit(int(os.environ.get('USBIP_TEST_STATUS', '0')))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""))
            for script, args, expected in (
                ("server.py", ["list"], "list|-l"),
                ("client.py", ["list", "--host", "192.0.2.10"], "list|-r|192.0.2.10"),
            ):
                for status in (0, 9):
                    with self.subTest(script=script, status=status):
                        env["USBIP_TEST_STATUS"] = str(status)
                        result = subprocess.run(
                            [sys.executable, str(ROOT / script)] + args,
                            cwd=directory, env=env, capture_output=True,
                            text=True, timeout=10,
                        )
                        self.assertEqual(result.returncode, status, result.stderr)
                        self.assertIn("native arguments: " + expected, result.stdout)
                        self.assertIn("native diagnostic", result.stderr)


if __name__ == "__main__":
    unittest.main()
