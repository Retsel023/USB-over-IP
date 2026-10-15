"""Check the guided workflows without touching USB devices or launching sudo."""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import client
import server
import usbip
import usbip_menu as menu
from usbip_devices import USBConnection, USBDevice
from usbip_tools import USBIPError


ROOT = Path(__file__).resolve().parents[1]
MOUSE = USBDevice("3-5.1", "Logitech mouse", "046d:c08a")
KEYBOARD = USBDevice("1-2", "USB keyboard", "1234:5678")
MOUSE_CONNECTION = USBConnection(0, "Logitech mouse", "192.0.2.10", "3-5.1", "046d:c08a")
KEYBOARD_CONNECTION = USBConnection(9, "USB keyboard", "192.0.2.11", "1-2", "1234:5678")


class PromptTests(unittest.TestCase):
    def test_device_picker_retries_invalid_choices_and_returns_numbered_device(self):
        output = io.StringIO()
        with mock.patch("builtins.input", side_effect=["046d:c08a", "0", "3", "abc", "2"]):
            with contextlib.redirect_stdout(output):
                selected = menu.choose_device([MOUSE, KEYBOARD])
        self.assertIs(selected, KEYBOARD)
        self.assertIn("3-5.1", output.getvalue())
        self.assertIn("Logitech mouse", output.getvalue())

    def test_device_picker_cancellation_does_not_select_a_default(self):
        for answer in ("", "q"):
            with self.subTest(answer=answer), mock.patch("builtins.input", return_value=answer):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertIsNone(menu.choose_device([MOUSE]))

    def test_empty_device_list_does_not_prompt_for_selection(self):
        with mock.patch("builtins.input") as prompt:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(menu.choose_device([]))
        prompt.assert_not_called()

    def test_host_prompt_rejects_urls_ports_and_options_before_accepting_address(self):
        with mock.patch("builtins.input", side_effect=[
            "https://192.0.2.10", "192.0.2.10:3240", "--help", "two hosts", "192.0.2.10",
        ]):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(menu.ask_host(), "192.0.2.10")

    def test_host_prompt_can_be_cancelled(self):
        for answer in ("", "q"):
            with self.subTest(answer=answer), mock.patch("builtins.input", return_value=answer):
                self.assertIsNone(menu.ask_host())


class LaunchTests(unittest.TestCase):
    def launch(self, role, args, *, system="Linux", elevated=False, uid=1000,
               result=0, error=None, admin_error=None, sudo="/usr/bin/sudo"):
        tools = mock.Mock(system=system)
        if admin_error is not None:
            tools.require_admin.side_effect = admin_error
        child = mock.Mock(return_value=subprocess.CompletedProcess([], result))
        if error is not None:
            child.side_effect = error
        with mock.patch.object(menu, "USBIPTools", return_value=tools):
            with mock.patch.object(menu.os, "geteuid", return_value=uid, create=True):
                with mock.patch.object(menu.shutil, "which", return_value=sudo):
                    with mock.patch.object(menu.subprocess, "run", child):
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            status = menu.run_command(role, args, elevated=elevated)
        return status, tools, child

    def assert_command(self, child, role, args, prefix=()):
        child.assert_called_once()
        command = child.call_args.args[0]
        self.assertEqual(command, [*prefix, sys.executable, str(ROOT / f"{role}.py"), *args])
        self.assertTrue(Path(command[len(prefix) + 1]).is_absolute())
        self.assertFalse(child.call_args.kwargs.get("shell", False))
        self.assertFalse(child.call_args.kwargs["check"])

    def test_elevated_linux_action_uses_sudo_and_absolute_script(self):
        args = ["bind", "--busid", "3-5.1"]
        status, tools, child = self.launch("server", args, elevated=True)
        self.assertEqual(status, 0)
        self.assert_command(child, "server", args, prefix=("/usr/bin/sudo",))

    def test_read_only_commands_do_not_request_elevation_and_preserve_child_exit(self):
        status, tools, child = self.launch("client", ["port"], result=12)
        self.assertEqual(status, 12)
        self.assert_command(child, "client", ["port"])
        tools.require_admin.assert_not_called()

    def test_root_does_not_launch_another_sudo(self):
        status, _, child = self.launch("server", ["serve"], elevated=True, uid=0)
        self.assertEqual(status, 0)
        self.assert_command(child, "server", ["serve"])

    def test_missing_sudo_prevents_mutating_command(self):
        status, _, child = self.launch("server", ["serve"], elevated=True, sudo=None)
        self.assertEqual(status, 1)
        child.assert_not_called()

    def test_windows_admin_denial_prevents_launch(self):
        status, tools, child = self.launch(
            "client", ["detach", "--port", "1"], system="Windows", elevated=True,
            admin_error=USBIPError("Open a terminal as administrator."),
        )
        self.assertEqual(status, 1)
        tools.require_admin.assert_called_once_with()
        child.assert_not_called()

    def test_launch_failure_and_interrupt_have_useful_exit_statuses(self):
        for error, expected in ((PermissionError("permission denied"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=error):
                status, _, _ = self.launch("client", ["port"], error=error)
                self.assertEqual(status, expected)

    def test_unsupported_operating_system_never_launches_child(self):
        with mock.patch.object(menu, "USBIPTools", side_effect=USBIPError("Unsupported operating system.")):
            with mock.patch.object(menu.subprocess, "run") as child:
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(menu.run_command("server", ["serve"], elevated=True), 1)
        child.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.tools = mock.Mock(system="Linux")
        self.stack.enter_context(mock.patch.object(menu, "USBIPTools", return_value=self.tools))
        self.command = self.stack.enter_context(mock.patch.object(menu, "run_command", return_value=0))
        self.local = self.stack.enter_context(mock.patch.object(menu, "local_devices", return_value=[MOUSE]))
        self.remote = self.stack.enter_context(mock.patch.object(menu, "remote_devices", return_value=[MOUSE]))
        self.scan = self.stack.enter_context(mock.patch.object(menu, "scan_available_devices", return_value=[]))
        self.snapshot = self.stack.enter_context(mock.patch.object(menu, "snapshot_connections", return_value=[]))
        self.wait = self.stack.enter_context(mock.patch.object(
            menu, "wait_for_connection", return_value=MOUSE_CONNECTION,
        ))
        self.connected = self.stack.enter_context(mock.patch.object(
            menu, "connected_devices", return_value=[MOUSE_CONNECTION],
        ))
        self.running = self.stack.enter_context(mock.patch.object(menu, "service_running", return_value=True))
        self.stack.enter_context(mock.patch.object(menu, "local_addresses", return_value=["192.0.2.1"]))
        self.stack.enter_context(mock.patch.object(menu.time, "sleep"))

    def input(self, answers):
        self.stack.enter_context(mock.patch("builtins.input", side_effect=answers))

    def test_cancelling_share_confirmation_never_changes_device_or_starts_service(self):
        self.input(["1", "1", "n", "0"])
        menu.server_menu()
        self.command.assert_not_called()
        self.running.assert_not_called()

    def test_share_passes_selected_busid_and_does_not_restart_existing_service(self):
        self.input(["1", "1", "y", "0"])
        menu.server_menu()
        self.command.assert_called_once_with("server", ["bind", "--busid", "3-5.1"], elevated=True)
        self.running.assert_called_once_with()

    def test_share_starts_missing_service_only_after_successful_binding(self):
        self.running.side_effect = [False, True]
        self.input(["1", "1", "y", "0"])
        menu.server_menu()
        self.assertEqual(self.command.call_args_list, [
            mock.call("server", ["bind", "--busid", "3-5.1"], elevated=True),
            mock.call("server", ["start"], elevated=True),
        ])

    def test_failed_binding_does_not_start_service(self):
        self.command.return_value = 1
        self.running.return_value = False
        self.input(["1", "1", "y", "0"])
        menu.server_menu()
        self.command.assert_called_once_with("server", ["bind", "--busid", "3-5.1"], elevated=True)
        self.running.assert_not_called()

    def test_manual_connect_uses_remote_busid_and_shows_verified_named_connection(self):
        self.input(["5", "192.0.2.10", "1", "0"])
        menu.client_menu()
        self.remote.assert_called_once_with(self.tools, "192.0.2.10")
        self.command.assert_called_once_with(
            "client", ["attach", "--host", "192.0.2.10", "--busid", "3-5.1"], elevated=True,
        )
        self.snapshot.assert_called_once_with(self.tools)
        self.wait.assert_called_once_with(self.tools, MOUSE, "192.0.2.10", previous=[])
        self.connected.assert_not_called()
        self.scan.assert_not_called()
        self.assertIn("Logitech mouse", self.output.getvalue())

    def test_failed_remote_query_does_not_attach(self):
        self.remote.side_effect = USBIPError("The USB/IP host is not reachable.")
        self.input(["5", "192.0.2.10", "0"])
        menu.client_menu()
        self.command.assert_not_called()

    def test_cancelling_remote_selection_does_not_attach(self):
        self.input(["5", "192.0.2.10", "q", "0"])
        menu.client_menu()
        self.command.assert_not_called()

    def test_failed_attach_does_not_display_connections_as_success(self):
        self.command.return_value = 1
        self.input(["5", "192.0.2.10", "1", "0"])
        menu.client_menu()
        self.command.assert_called_once_with(
            "client", ["attach", "--host", "192.0.2.10", "--busid", "3-5.1"], elevated=True,
        )
        self.connected.assert_not_called()
        self.wait.assert_not_called()

    def test_scan_with_no_available_devices_does_not_attach(self):
        self.input(["1", "0"])
        menu.client_menu()
        self.scan.assert_called_once_with(self.tools)
        self.command.assert_not_called()
        self.snapshot.assert_not_called()
        self.wait.assert_not_called()

    def test_scan_selection_uses_device_address_and_snapshots_before_attaching(self):
        self.scan.return_value = [
            menu.RemoteDevice("192.0.2.10", MOUSE),
            menu.RemoteDevice("192.0.2.11", KEYBOARD),
        ]
        self.wait.return_value = KEYBOARD_CONNECTION
        events = mock.Mock()
        events.attach_mock(self.snapshot, "snapshot")
        events.attach_mock(self.command, "command")
        events.attach_mock(self.wait, "wait")
        self.input(["1", "2", "0"])
        menu.client_menu()
        self.assertEqual(events.mock_calls, [
            mock.call.snapshot(self.tools),
            mock.call.command(
                "client", ["attach", "--host", "192.0.2.11", "--busid", "1-2"], elevated=True,
            ),
            mock.call.wait(self.tools, KEYBOARD, "192.0.2.11", previous=[]),
        ])
        self.assertIn("USB keyboard", self.output.getvalue())
        self.assertIn("192.0.2.11", self.output.getvalue())
        self.remote.assert_not_called()

    def test_scanned_devices_do_not_attach_before_user_selects_one(self):
        self.scan.return_value = [menu.RemoteDevice("192.0.2.10", MOUSE)]
        answers = iter(["1", "1", "0"])

        def answer(prompt):
            value = next(answers)
            if "Device number" in prompt:
                self.command.assert_not_called()
                self.snapshot.assert_not_called()
                self.wait.assert_not_called()
            return value

        with mock.patch("builtins.input", side_effect=answer):
            menu.client_menu()
        self.command.assert_called_once_with(
            "client", ["attach", "--host", "192.0.2.10", "--busid", "3-5.1"], elevated=True,
        )

    def test_scan_selection_can_be_cancelled_without_attachment(self):
        self.scan.return_value = [menu.RemoteDevice("192.0.2.10", MOUSE)]
        self.input(["1", "q", "0"])
        menu.client_menu()
        self.command.assert_not_called()
        self.snapshot.assert_not_called()
        self.wait.assert_not_called()

    def test_unconfirmed_attachment_is_not_reported_as_connected(self):
        self.scan.return_value = [menu.RemoteDevice("192.0.2.10", MOUSE)]
        self.wait.return_value = None
        self.input(["1", "1", "0"])
        menu.client_menu()
        self.wait.assert_called_once_with(self.tools, MOUSE, "192.0.2.10", previous=[])
        self.assertNotIn("Connected Logitech mouse", self.output.getvalue())
        self.assertNotIn("now available", self.output.getvalue())
        self.connected.assert_not_called()

    def test_success_is_printed_only_after_attachment_is_verified(self):
        self.scan.return_value = [menu.RemoteDevice("192.0.2.10", MOUSE)]

        def verify(*args, **kwargs):
            self.assertNotIn("Connected Logitech mouse", self.output.getvalue())
            return MOUSE_CONNECTION

        self.wait.side_effect = verify
        self.input(["1", "1", "0"])
        menu.client_menu()
        self.assertIn("Connected Logitech mouse", self.output.getvalue())
        self.connected.assert_not_called()

    def test_disconnect_uses_selected_device_name_instead_of_asking_for_internal_port(self):
        self.connected.return_value = [MOUSE_CONNECTION, KEYBOARD_CONNECTION]
        self.input(["3", "2", "0"])
        menu.client_menu()
        self.connected.assert_called_once_with(self.tools)
        self.command.assert_called_once_with("client", ["detach", "--port", "9"], elevated=True)
        self.assertIn("USB keyboard", self.output.getvalue())

    def test_show_connected_devices_displays_names_without_running_raw_port_command(self):
        self.input(["2", "0"])
        menu.client_menu()
        self.connected.assert_called_once_with(self.tools)
        self.command.assert_not_called()
        self.assertIn("Logitech mouse", self.output.getvalue())

    def test_connection_with_unknown_source_is_still_disconnectable(self):
        self.connected.return_value = [USBConnection(7, "Unknown USB device")]
        self.input(["3", "1", "0"])
        menu.client_menu()
        self.command.assert_called_once_with("client", ["detach", "--port", "7"], elevated=True)

    def test_disconnect_empty_list_does_not_ask_for_a_device_or_detach(self):
        self.connected.return_value = []
        self.input(["3", "0"])
        menu.client_menu()
        self.command.assert_not_called()

    def test_disconnect_selection_can_be_cancelled(self):
        for answer in ("", "q"):
            with self.subTest(answer=answer):
                with mock.patch("builtins.input", side_effect=["3", answer, "0"]):
                    menu.client_menu()
        self.command.assert_not_called()

    def test_failed_connected_device_query_does_not_detach(self):
        self.connected.side_effect = USBIPError("The connected-device list could not be read.")
        self.input(["3", "0"])
        menu.client_menu()
        self.command.assert_not_called()

    def test_windows_disconnect_uses_discovered_port_one(self):
        self.tools.system = "Windows"
        self.connected.return_value = [USBConnection(1, "Logitech mouse")]
        self.input(["3", "1", "0"])
        menu.client_menu()
        self.command.assert_called_once_with("client", ["detach", "--port", "1"], elevated=True)

    def test_main_menu_retries_bad_choice_and_routes_each_role(self):
        self.input(["invalid", "1", "2", "0"])
        with mock.patch.object(menu, "server_menu") as share, mock.patch.object(menu, "client_menu") as use:
            menu.main_menu()
        share.assert_called_once_with()
        use.assert_called_once_with()
        self.command.assert_not_called()


class EntrypointTests(unittest.TestCase):
    def test_role_script_without_arguments_opens_its_menu_in_terminal(self):
        for module, target in ((server, "server_menu"), (client, "client_menu")):
            with self.subTest(role=module.__name__):
                with mock.patch.object(sys, "argv", [f"{module.__name__}.py"]):
                    with mock.patch.object(sys.stdin, "isatty", return_value=True):
                        with mock.patch.object(menu, target, return_value=7) as guided:
                            self.assertEqual(module.main(), 7)
                guided.assert_called_once_with()

    def test_explicit_menu_command_routes_even_with_nonterminal_input(self):
        for module, target in ((server, "server_menu"), (client, "client_menu")):
            with self.subTest(role=module.__name__):
                with mock.patch.object(sys.stdin, "isatty", return_value=False):
                    with mock.patch.object(menu, target, return_value=0) as guided:
                        self.assertEqual(module.main(["menu"]), 0)
                guided.assert_called_once_with()

    def test_combined_launcher_opens_main_menu_in_terminal(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch.object(usbip, "main_menu", return_value=0) as guided:
                self.assertEqual(usbip.main([]), 0)
        guided.assert_called_once_with()

    def test_combined_launcher_errors_promptly_without_terminal(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            with mock.patch.object(usbip, "main_menu") as guided:
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    with self.assertRaises(SystemExit) as raised:
                        usbip.main([])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("terminal", error.getvalue())
        guided.assert_not_called()


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.tools = mock.Mock(system="Linux")
        self.hosts = self.stack.enter_context(mock.patch.object(menu, "discover_hosts", return_value=[]))
        self.query = self.stack.enter_context(mock.patch.object(menu, "remote_devices"))
        self.command = self.stack.enter_context(mock.patch.object(menu, "run_command"))

    def test_no_listening_hosts_returns_empty_without_querying_or_attaching(self):
        self.assertEqual(menu.scan_available_devices(self.tools), [])
        self.query.assert_not_called()
        self.command.assert_not_called()
        self.tools.run.assert_not_called()

    def test_missing_usbip_tool_stops_before_network_lookup(self):
        self.tools.resolve.side_effect = USBIPError("The usbip command is not installed.")
        with self.assertRaises(USBIPError):
            menu.scan_available_devices(self.tools)
        self.hosts.assert_not_called()
        self.query.assert_not_called()
        self.command.assert_not_called()

    def test_scan_flattens_devices_and_preserves_their_sharing_addresses(self):
        self.hosts.return_value = ["192.0.2.11", "192.0.2.10"]

        def devices(tools, address, *, timeout):
            return {"192.0.2.10": [MOUSE], "192.0.2.11": [KEYBOARD]}[address]

        self.query.side_effect = devices
        found = menu.scan_available_devices(self.tools)
        self.assertEqual(found, [
            menu.RemoteDevice("192.0.2.10", MOUSE),
            menu.RemoteDevice("192.0.2.11", KEYBOARD),
        ])
        self.assertCountEqual(self.query.call_args_list, [
            mock.call(self.tools, "192.0.2.10", timeout=1.5),
            mock.call(self.tools, "192.0.2.11", timeout=1.5),
        ])
        self.command.assert_not_called()
        self.tools.run.assert_not_called()

    def test_one_unreadable_host_does_not_hide_another_hosts_devices(self):
        self.hosts.return_value = ["192.0.2.10", "192.0.2.11"]

        def devices(tools, address, *, timeout):
            if address == "192.0.2.10":
                raise USBIPError("The host stopped sharing before it could be queried.")
            return [KEYBOARD]

        self.query.side_effect = devices
        self.assertEqual(menu.scan_available_devices(self.tools), [menu.RemoteDevice("192.0.2.11", KEYBOARD)])
        self.command.assert_not_called()


class BackgroundServiceTests(unittest.TestCase):
    def test_linux_start_launches_daemon_so_menu_can_continue(self):
        tools = mock.Mock(system="Linux")
        tools.run.return_value = 0
        with mock.patch.object(server, "USBIPTools", return_value=tools):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(server.main(["start"]), 0)
        self.assertEqual(tools.mock_calls, [
            mock.call.require_admin(),
            mock.call.load_module("usbip_host"),
            mock.call.run("usbipd", ["-D"]),
        ])

    def test_background_start_admin_failure_never_loads_module_or_launches_service(self):
        for system in ("Linux", "Windows"):
            with self.subTest(system=system):
                tools = mock.Mock(system=system)
                tools.require_admin.side_effect = USBIPError("Requires administrator privileges.")
                with mock.patch.object(server, "USBIPTools", return_value=tools):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(server.main(["start"]), 1)
                tools.require_admin.assert_called_once_with()
                tools.load_module.assert_not_called()
                tools.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
