"""Attachment visibility checks using native output fixtures, without hardware."""

import subprocess
import unittest
from unittest import mock

from usbip_connection import snapshot_connections, wait_for_connection
from usbip_devices import USBConnection, USBDevice
from usbip_tools import USBIPError, USBIPTools


class ConnectionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tools = mock.Mock()
        self.device = USBDevice("3-5.1", "Logitech mouse", "046d:c08a")
        self.connection = USBConnection(0, "Logitech mouse", "192.0.2.10", "3-5.1", "046d:c08a")
        patcher = mock.patch("usbip_connection.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def poll(self, records, address="192.0.2.10", previous=(), **kwargs):
        with mock.patch("usbip_connection.connected_devices", side_effect=records):
            return wait_for_connection(self.tools, self.device, address, previous, **kwargs)

    def test_pre_attach_snapshot_tolerates_unloaded_module(self):
        with mock.patch("usbip_connection.connected_devices", side_effect=USBIPError("driver missing")):
            self.assertEqual(snapshot_connections(self.tools), [])

    def test_snapshot_preserves_existing_imports(self):
        with mock.patch("usbip_connection.connected_devices", return_value=[self.connection]) as read:
            self.assertEqual(snapshot_connections(self.tools), [self.connection])
        read.assert_called_once_with(self.tools, timeout=0.5)

    def test_waits_for_delayed_device_registration(self):
        self.assertEqual(self.poll([[], [], [self.connection]]), self.connection)
        self.assertEqual(self.sleep.call_args_list, [mock.call(0.2), mock.call(0.2)])

    def test_exact_host_and_busid_can_match_existing_record(self):
        self.assertEqual(self.poll([[self.connection]], previous=[self.connection]), self.connection)
        self.sleep.assert_not_called()

    def test_dns_case_and_trailing_dot_do_not_hide_exact_match(self):
        connection = USBConnection(0, "Logitech mouse", "PI.LOCAL.", "3-5.1", "046d:c08a")
        self.assertEqual(self.poll([[connection]], "pi.local", [connection]), connection)

    def test_equivalent_ipv6_addresses_match(self):
        connection = USBConnection(0, "Logitech mouse", "2001:db8::1", "3-5.1", "046d:c08a")
        self.assertEqual(self.poll([[connection]], "2001:0db8:0:0::1", [connection]), connection)

    def test_existing_same_model_on_another_host_never_counts_as_attach(self):
        other = USBConnection(0, "Logitech mouse", "192.0.2.99", "3-5.1", "046d:c08a")
        self.assertIsNone(self.poll([[other]] * 2, previous=[other], attempts=2))

    def test_new_connection_with_conflicting_literal_host_is_rejected(self):
        other = USBConnection(0, "Logitech mouse", "192.0.2.99", "3-5.1", "046d:c08a")
        self.assertIsNone(self.poll([[other]], attempts=1))

    def test_new_port_with_unknown_source_uses_hardware_identity(self):
        connection = USBConnection(1, "Native mouse descriptor", hardware_id="046d:c08a")
        self.assertEqual(self.poll([[connection]]), connection)

    def test_existing_unknown_source_same_model_is_not_accepted(self):
        connection = USBConnection(1, "Logitech mouse", hardware_id="046d:c08a")
        self.assertIsNone(self.poll([[connection]], previous=[connection], attempts=1))

    def test_changed_port_identity_can_be_new_attachment(self):
        previous = USBConnection(1, "Camera", hardware_id="1234:5678")
        connection = USBConnection(1, "Logitech mouse", hardware_id="046d:c08a")
        self.assertEqual(self.poll([[connection]], previous=[previous]), connection)

    def test_unknown_source_can_use_matching_name_if_ids_are_missing(self):
        connection = USBConnection(1, "Logitech mouse")
        self.assertEqual(self.poll([[connection]]), connection)

    def test_generic_unknown_names_are_not_a_device_match(self):
        self.device = USBDevice("3-5.1", "Unknown USB device")
        connection = USBConnection(1, "Unknown USB device")
        self.assertIsNone(self.poll([[connection]], attempts=1))

    def test_dns_request_can_be_recorded_as_ip_on_new_port(self):
        self.assertEqual(self.poll([[self.connection]], "pi.local"), self.connection)

    def test_dns_alias_does_not_accept_unchanged_existing_ip_record(self):
        self.assertIsNone(self.poll([[self.connection]], "pi.local", [self.connection], attempts=1))

    def test_dns_alias_does_not_accept_another_hostname(self):
        other = USBConnection(0, "Logitech mouse", "other.local", "3-5.1", "046d:c08a")
        self.assertIsNone(self.poll([[other]], "pi.local", attempts=1))

    def test_matching_hardware_cannot_override_a_conflicting_remote_busid(self):
        other = USBConnection(0, "Logitech mouse", "192.0.2.10", "1-2", "046d:c08a")
        self.assertIsNone(self.poll([[other]], "pi.local", attempts=1))

    def test_matching_name_cannot_override_conflicting_hardware(self):
        other = USBConnection(0, "Logitech mouse", hardware_id="1234:5678")
        self.assertIsNone(self.poll([[other]], attempts=1))

    def test_tolerates_transient_native_errors_during_registration(self):
        self.assertEqual(self.poll([USBIPError("driver loading"), [], [self.connection]]), self.connection)
        self.assertEqual(self.sleep.call_count, 2)

    def test_permanently_empty_state_has_short_bounded_wait(self):
        self.assertIsNone(self.poll([[]] * 10))
        self.assertEqual(self.sleep.call_count, 9)
        self.assertLessEqual(sum(call.args[0] for call in self.sleep.call_args_list), 2)

    def test_native_errors_until_timeout_leave_attachment_pending(self):
        self.assertIsNone(self.poll([USBIPError("unavailable")] * 2, attempts=2))
        self.sleep.assert_called_once_with(0.2)

    def test_native_read_timeout_remains_pending_then_observes_attachment(self):
        tools = USBIPTools("Linux")
        output = (
            "Port 00: <Port in Use>\n Logitech mouse (046d:c08a)\n"
            " 5-1 -> usbip://192.0.2.10:3240/3-5.1\n"
        )
        responses = [
            subprocess.TimeoutExpired(["usbip", "port"], 0.5),
            subprocess.CompletedProcess([], 0, output, ""),
        ]
        with mock.patch.object(tools, "resolve", return_value="/usr/bin/usbip"):
            with mock.patch("usbip_tools.subprocess.run", side_effect=responses) as native:
                self.assertEqual(
                    wait_for_connection(tools, self.device, "192.0.2.10", attempts=2),
                    self.connection,
                )
        self.assertEqual(native.call_args_list, [
            mock.call(["/usr/bin/usbip", "port"], check=False, timeout=0.5,
                      capture_output=True, text=True),
        ] * 2)
        self.sleep.assert_called_once_with(0.2)

    def test_no_attempts_does_not_read_or_sleep(self):
        with mock.patch("usbip_connection.connected_devices") as read:
            self.assertIsNone(wait_for_connection(self.tools, self.device, "pi.local", attempts=0))
        read.assert_not_called()
        self.sleep.assert_not_called()

    def test_only_native_action_is_reading_current_ports(self):
        self.tools.capture.return_value = subprocess.CompletedProcess(
            [], 0,
            "Port 00: <Port in Use>\n Logitech mouse (046d:c08a)\n"
            " 5-1 -> usbip://192.0.2.10:3240/3-5.1\n", "",
        )
        self.assertEqual(
            wait_for_connection(self.tools, self.device, "192.0.2.10"), self.connection,
        )
        self.assertEqual(self.tools.method_calls, [mock.call.capture("usbip", ["port"], timeout=0.5)])


if __name__ == "__main__":
    unittest.main()
