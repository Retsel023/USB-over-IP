"""Native output fixtures for guided device selection; no hardware required."""

import argparse
import subprocess
import unittest
from unittest import mock

from usbip_devices import (
    USBConnection, USBDevice, connected_devices, local_devices, parse_connected_devices,
    parse_local_linux, parse_remote_devices, parse_windows_devices, remote_devices,
)
from usbip_tools import USBIPError


class DeviceParserTests(unittest.TestCase):
    def test_linux_local_devices_use_bus_ids_and_named_descriptions(self):
        output = """Local USB devices
 - busid 3-5.1 (046d:c08a)
   Logitech, Inc. : unknown product (046d:c08a)

 -   busid 1-2.3 (1234:ABCD)
   Example : USB camera (1234:ABCD)
"""
        self.assertEqual(parse_local_linux(output), [
            USBDevice("3-5.1", "Logitech, Inc. : unknown product", "046d:c08a"),
            USBDevice("1-2.3", "Example : USB camera", "1234:abcd"),
        ])

    def test_linux_local_parser_skips_malformed_ids_and_preserves_unnamed_device(self):
        output = """ - busid 1-2 (1234:5678)
 - busid 046d:c08a (046d:c08a)
   This is a product ID, not a bus ID
 - busid --help
   Malformed device
 - busid 2-1.4
   A USB device
 - busid 99999999999999999999999999999999-1
   Too long
"""
        self.assertEqual(parse_local_linux(output), [
            USBDevice("1-2", "Unknown USB device", "1234:5678"),
            USBDevice("2-1.4", "A USB device"),
        ])

    def test_remote_parser_ignores_headings_paths_interfaces_and_malformed_ids(self):
        output = """Exportable USB devices
======================
 - 192.0.2.10
       3-5.1: Logitech, Inc. : unknown product (046d:c08a)
           : /sys/devices/pci0000:00/usb3/3-5/3-5.1
           : (Defined at Interface level) (00/00/00)
       1-2.3: Example : USB camera (1234:ABCD)
           : Video (0e/01/00)
    046d:c08a: product ID must not be selected
    --help: malformed native data
"""
        self.assertEqual(parse_remote_devices(output), [
            USBDevice("3-5.1", "Logitech, Inc. : unknown product", "046d:c08a"),
            USBDevice("1-2.3", "Example : USB camera", "1234:abcd"),
        ])

    def test_remote_parser_allows_names_without_product_ids(self):
        self.assertEqual(
            parse_remote_devices(" 1-1: Generic USB device\n"),
            [USBDevice("1-1", "Generic USB device")],
        )

    def test_windows_parser_removes_state_and_excludes_persisted_devices(self):
        output = """Connected:
BUSID  VID:PID    DEVICE                                  STATE
1-13   187c:0550  USB Input Device                        Not shared
2-4    05E3:0749  USB3.0 Card Reader                      Shared (forced)
3-5.1  046d:c08a  Logitech USB Input Device               Attached

Persisted:
GUID                                  DEVICE
4b66a1e4-c94d-4569-8e5b-5af5f0b9ba64  USB Mass Storage Device
1-99   1234:5678  This section must never be selected     Shared
"""
        self.assertEqual(parse_windows_devices(output), [
            USBDevice("1-13", "USB Input Device", "187c:0550"),
            USBDevice("2-4", "USB3.0 Card Reader", "05e3:0749"),
            USBDevice("3-5.1", "Logitech USB Input Device", "046d:c08a"),
        ])

    def test_windows_parser_accepts_legacy_and_unpadded_tables(self):
        output = """BUSID DEVICE STATE
1-2 USB Mass Storage Device Shared
2-3 1234:5678 Example USB Device Not shared
bad-id 046d:c08a Bad device Shared
"""
        self.assertEqual(parse_windows_devices(output), [
            USBDevice("1-2", "USB Mass Storage Device"),
            USBDevice("2-3", "Example USB Device", "1234:5678"),
        ])

    def test_no_devices_is_an_empty_list_on_all_platforms(self):
        for parser in (parse_local_linux, parse_remote_devices, parse_windows_devices):
            with self.subTest(parser=parser.__name__):
                self.assertEqual(parser(""), [])

    def test_device_rejects_invalid_bus_ids_even_when_constructed_directly(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            USBDevice("046d:c08a", "Vendor and product ID")

    def test_labels_keep_hardware_id_separate_from_bus_id(self):
        self.assertEqual(USBDevice("3-5.1", "Mouse", "046d:c08a").label,
                         "Mouse (046d:c08a) — 3-5.1")
        self.assertEqual(USBDevice("1-2", "Camera").label, "Camera — 1-2")


class DiscoveryTests(unittest.TestCase):
    def test_linux_discovery_only_captures_list_output(self):
        tools = mock.Mock(system="Linux")
        tools.capture.return_value = subprocess.CompletedProcess(
            [], 0, " - busid 3-5.1 (046d:c08a)\n   Mouse\n", ""
        )
        self.assertEqual(local_devices(tools), [USBDevice("3-5.1", "Mouse", "046d:c08a")])
        tools.capture.assert_called_once_with("usbip", ["list", "-l"])
        tools.require_admin.assert_not_called()
        tools.load_module.assert_not_called()

    def test_windows_discovery_uses_usbipd_not_linux_usbip(self):
        tools = mock.Mock(system="Windows")
        tools.capture.return_value = subprocess.CompletedProcess(
            [], 0, "2-3  1234:5678  Camera  Not shared\n", ""
        )
        self.assertEqual(local_devices(tools), [USBDevice("2-3", "Camera", "1234:5678")])
        tools.capture.assert_called_once_with("usbipd", ["list"])
        tools.require_admin.assert_not_called()

    def test_local_discovery_reports_native_errors(self):
        tools = mock.Mock(system="Linux")
        tools.capture.return_value = subprocess.CompletedProcess([], 2, "", "native diagnostic")
        with self.assertRaisesRegex(USBIPError, "native diagnostic"):
            local_devices(tools)

    def test_remote_discovery_validates_host_and_captures_without_elevation(self):
        tools = mock.Mock(system="Linux")
        tools.capture.return_value = subprocess.CompletedProcess(
            [], 0, " 1-2.3: Camera (1234:5678)\n", ""
        )
        self.assertEqual(remote_devices(tools, "pi.local"), [USBDevice("1-2.3", "Camera", "1234:5678")])
        tools.capture.assert_called_once_with("usbip", ["list", "-r", "pi.local"])
        tools.require_admin.assert_not_called()
        tools.load_module.assert_not_called()

    def test_remote_invalid_host_is_never_passed_to_a_native_command(self):
        tools = mock.Mock(system="Linux")
        with self.assertRaisesRegex(USBIPError, "without a URL or port"):
            remote_devices(tools, "https://pi.local:3240")
        tools.capture.assert_not_called()

    def test_remote_connection_failure_explains_service_and_retains_diagnostic(self):
        tools = mock.Mock(system="Linux")
        for stdout, stderr in (("", "could not connect to pi.local:3240"),
                               ("native failure on stdout", "")):
            with self.subTest(stderr=stderr):
                tools.capture.return_value = subprocess.CompletedProcess([], 1, stdout, stderr)
                with self.assertRaises(USBIPError) as raised:
                    remote_devices(tools, "pi.local")
                message = str(raised.exception)
                self.assertIn("sharing computer", message)
                self.assertIn("USB/IP service are running", message)
                self.assertIn(stderr or stdout, message)
                self.assertNotIn("sudo", message)


class ConnectionTests(unittest.TestCase):
    def test_linux_connection_keeps_identity_when_source_record_is_missing(self):
        output = """Imported USB devices
====================
Port 00: <Port in Use> at Full Speed(12Mbps)
       Logitech, Inc. : unknown product (046d:c08a)
       5-1 -> unknown host, remote port and remote busid
           -> remote bus/dev 003/006
"""
        self.assertEqual(parse_connected_devices(output), [
            USBConnection(0, "Logitech, Inc. : unknown product", hardware_id="046d:c08a"),
        ])

    def test_connections_use_native_ports_and_remote_not_local_bus_ids(self):
        output = """Imported USB devices
Port 00: <Port in Use> at Full Speed(12Mbps)
       Example : Mouse (1234:ABCD)
       5-1 -> usbip://raspberrypi.local:3240/3-5.1
           -> remote bus/dev 003/006
Port 01: <Device in Use> at High Speed(480 Mbps)
       Example : Camera (1234:5678)
       7-2 -> usbip://192.0.2.4:3240/1-2.3
"""
        self.assertEqual(parse_connected_devices(output), [
            USBConnection(0, "Example : Mouse", "raspberrypi.local", "3-5.1", "1234:abcd"),
            USBConnection(1, "Example : Camera", "192.0.2.4", "1-2.3", "1234:5678"),
        ])

    def test_bracketed_and_unbracketed_ipv6_connection_sources(self):
        for source in ("[2001:db8::4]", "2001:db8::4"):
            with self.subTest(source=source):
                output = f"Port01: <Port in Use>\n Camera\n 1-1 -> usbip://{source}:3240/2-3\n"
                self.assertEqual(parse_connected_devices(output), [
                    USBConnection(1, "Camera", "2001:db8::4", "2-3"),
                ])

    def test_current_windows_usbip_win2_format(self):
        output = """Imported USB devices
====================
Port 01: device in use at high speed (480 Mbps)
Example : Camera (1234:5678)
-> usbip://pi.local:3240/1-2.3
-> remote bus/dev: 001/004
-> serial: EXAMPLE
-> mode: zero copy
"""
        self.assertEqual(parse_connected_devices(output), [
            USBConnection(1, "Example : Camera", "pi.local", "1-2.3", "1234:5678"),
        ])

    def test_prefixed_native_output_and_absent_device_descriptors(self):
        output = """usbip: info: Imported USB devices
usbip: info: Port 01: <Port in Use>
usbip: info: 1-2 -> usbip://pi.local:3240/2-3
"""
        self.assertEqual(parse_connected_devices(output), [
            USBConnection(1, "Unknown remote USB device", "pi.local", "2-3"),
        ])

    def test_empty_or_invalid_port_sections_are_not_selectable(self):
        output = """Port 02: <Port Available>
Port 65536: <Port in Use>
    Unusable port
Port -1: <Port in Use>
    Unusable port
"""
        self.assertEqual(parse_connected_devices(output), [])
        self.assertEqual(parse_connected_devices("Imported USB devices\n========\n"), [])

    def test_invalid_section_does_not_supply_identity_to_previous_port(self):
        output = "Port 00: <Port in Use>\nPort -1: <Port in Use>\nUnusable descriptor\n"
        self.assertEqual(parse_connected_devices(output), [USBConnection(0, "Unknown remote USB device")])

    def test_malformed_remote_sources_do_not_become_native_arguments(self):
        for uri in ("usbip://--help:3240/1-2", "usbip://pi.local:3240/046d:c08a",
                    "usbip://pi.local:99999/1-2", "usbip://pi.local:notaport/1-2"):
            with self.subTest(uri=uri):
                output = f"Port 00: <Port in Use>\n Camera (1234:5678)\n 5-1 -> {uri}\n"
                self.assertEqual(parse_connected_devices(output), [
                    USBConnection(0, "Camera", hardware_id="1234:5678"),
                ])

    def test_connection_labels_show_device_identity_without_internal_port_numbers(self):
        known = USBConnection(0, "Mouse", "pi.local", "1-2", "046d:c08a")
        self.assertEqual(known.label, "Mouse (046d:c08a) — pi.local / 1-2")
        self.assertEqual(USBConnection(1, "Camera").label, "Camera")
        self.assertNotIn("port", known.label)

    def test_invalid_native_connection_port_is_rejected(self):
        for native_port in (-1, 65536):
            with self.subTest(native_port=native_port), self.assertRaises(argparse.ArgumentTypeError):
                USBConnection(native_port, "Camera")

    def test_connection_discovery_is_read_only_and_tolerates_nonfatal_stderr(self):
        tools = mock.Mock(system="Linux")
        tools.capture.return_value = subprocess.CompletedProcess(
            [], 0, "Port 00: <Port in Use>\n Camera (1234:5678)\n", "libusbip error read_record"
        )
        self.assertEqual(connected_devices(tools), [USBConnection(0, "Camera", hardware_id="1234:5678")])
        tools.capture.assert_called_once_with("usbip", ["port"])
        tools.require_admin.assert_not_called()
        tools.load_module.assert_not_called()

    def test_connection_discovery_reports_native_failure(self):
        tools = mock.Mock(system="Windows")
        tools.capture.return_value = subprocess.CompletedProcess([], 1, "", "driver is unavailable")
        with self.assertRaisesRegex(USBIPError, "driver is unavailable"):
            connected_devices(tools)


if __name__ == "__main__":
    unittest.main()
