"""LAN discovery fixtures; these tests never probe real network hosts."""

import ipaddress
import json
import socket
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from usbip_network import MAX_SCAN_HOSTS, NetworkAddress, discover_hosts, network_addresses, scan_targets
from usbip_tools import USBIPError


class AddressTests(unittest.TestCase):
    @mock.patch("usbip_network.platform.system", return_value="Linux")
    @mock.patch("usbip_network.subprocess.run")
    def test_linux_preserves_global_ipv4_prefixes_on_up_interfaces(self, run, system):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps([
            {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1",
                                          "prefixlen": 8, "scope": "host"}]},
            {"ifname": "eth0", "operstate": "UP", "addr_info": [
                {"family": "inet", "local": "172.16.200.28", "prefixlen": 28, "scope": "global"},
                {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"},
                {"family": "inet", "local": "169.254.1.2", "prefixlen": 16, "scope": "link"},
            ]},
            {"ifname": "eth1", "operstate": "DOWN", "addr_info": [
                {"family": "inet", "local": "10.1.1.1", "prefixlen": 24, "scope": "global"},
            ]},
        ]), "")
        self.assertEqual(network_addresses(), [NetworkAddress("172.16.200.28", 28)])
        run.assert_called_once_with(["ip", "-j", "-4", "address", "show", "up"],
                                    capture_output=True, text=True, check=False, timeout=5)

    @mock.patch("usbip_network.platform.system", return_value="Windows")
    @mock.patch("usbip_network.subprocess.run")
    def test_windows_single_record_and_array_are_supported(self, run, system):
        for payload in ({"IPAddress": "192.168.5.10", "PrefixLength": 30},
                        [{"IPAddress": "192.168.5.10", "PrefixLength": 30}]):
            with self.subTest(payload=payload):
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
                self.assertEqual(network_addresses(), [NetworkAddress("192.168.5.10", 30)])
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertIn("Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred", command[4])

    @mock.patch("usbip_network.platform.system", return_value="Linux")
    @mock.patch("usbip_network.subprocess.run")
    def test_malformed_records_are_discarded(self, run, system):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps([
            None, {"addr_info": None}, {"addr_info": [
                None,
                {"family": "inet", "scope": "global", "local": "example.com", "prefixlen": 24},
                {"family": "inet", "scope": "global", "local": "10.1.1.2", "prefixlen": "24"},
                {"family": "inet", "scope": "global", "local": "10.1.1.2", "prefixlen": 33},
                {"family": "inet", "scope": "global", "local": "10.1.1.2", "prefixlen": 24},
                {"family": "inet", "scope": "global", "local": "10.1.1.2", "prefixlen": 24},
            ]},
        ]), "")
        self.assertEqual(network_addresses(), [NetworkAddress("10.1.1.2", 24)])

    @mock.patch("usbip_network.platform.system", return_value="Linux")
    @mock.patch("usbip_network.socket.gethostname", return_value="my-laptop")
    @mock.patch("usbip_network.socket.getaddrinfo")
    @mock.patch("usbip_network.subprocess.run")
    def test_native_failure_uses_only_local_private_hostname_ipv4(self, run, lookup, hostname, system):
        run.side_effect = FileNotFoundError("no ip command")
        lookup.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 0)) for value in (
            "127.0.1.1", "192.168.1.5", "192.168.1.5", "8.8.8.8", "169.254.3.4",
        )]
        addresses = network_addresses()
        self.assertEqual(addresses, [NetworkAddress("192.168.1.5", 32)])
        self.assertEqual(scan_targets(addresses), ["192.168.1.5"])
        lookup.assert_called_once_with("my-laptop", None, socket.AF_INET, socket.SOCK_STREAM)

    @mock.patch("usbip_network.platform.system", return_value="Linux")
    @mock.patch("usbip_network.socket.getaddrinfo", side_effect=socket.gaierror("unavailable"))
    @mock.patch("usbip_network.subprocess.run")
    def test_native_invalid_json_timeout_and_nonzero_return_are_safe(self, run, lookup, system):
        for value in (subprocess.CompletedProcess([], 0, "not json", ""),
                      subprocess.CompletedProcess([], 1, "", "failed"),
                      subprocess.TimeoutExpired("ip", 5)):
            with self.subTest(value=value):
                run.side_effect = value if isinstance(value, Exception) else None
                run.return_value = value
                self.assertEqual(network_addresses(), [])

    @mock.patch("usbip_network.socket.getaddrinfo")
    @mock.patch("usbip_network.socket.create_connection")
    @mock.patch("usbip_network.subprocess.run", side_effect=PermissionError("Operation not permitted"))
    def test_native_interface_permission_error_stops_discovery_without_fallback_or_probes(self, run, connect, lookup):
        for system in ("Linux", "Windows"):
            with self.subTest(system=system), mock.patch("usbip_network.platform.system", return_value=system):
                with self.assertRaisesRegex(USBIPError, "Network permissions blocked reading local network interfaces"):
                    discover_hosts()
        lookup.assert_not_called()
        connect.assert_not_called()

    @mock.patch("usbip_network.socket.getaddrinfo")
    @mock.patch("usbip_network.socket.create_connection")
    @mock.patch("usbip_network.subprocess.run")
    def test_native_permission_diagnostics_stop_discovery_without_fallback_or_probes(self, run, connect, lookup):
        diagnostics = (
            ("Linux", "Cannot open netlink socket: Operation not permitted"),
            ("Linux", "Permission denied"),
            ("Windows", "Get-NetIPAddress: Access is denied."),
            ("Windows", "ACCESS DENIED"),
        )
        for system, diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic), mock.patch("usbip_network.platform.system", return_value=system):
                run.return_value = subprocess.CompletedProcess([], 1, "", diagnostic)
                with self.assertRaises(USBIPError) as raised:
                    discover_hosts()
                self.assertIn("Network permissions blocked reading local network interfaces", str(raised.exception))
                self.assertIn(diagnostic, str(raised.exception))
        lookup.assert_not_called()
        connect.assert_not_called()

    @mock.patch("usbip_network.platform.system", return_value="Linux")
    @mock.patch("usbip_network.socket.getaddrinfo")
    @mock.patch("usbip_network.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "[]", ""))
    def test_empty_native_configuration_does_not_guess_a_network(self, run, lookup, system):
        self.assertEqual(network_addresses(), [])
        lookup.assert_not_called()


class TargetTests(unittest.TestCase):
    def test_small_actual_subnet_excludes_network_broadcast_and_keeps_own_ip(self):
        self.assertEqual(scan_targets([NetworkAddress("192.168.1.2", 30)]),
                         ["192.168.1.2", "192.168.1.1"])

    def test_31_and_32_keep_usable_endpoints(self):
        self.assertEqual(scan_targets([NetworkAddress("10.2.3.0", 31)]), ["10.2.3.0", "10.2.3.1"])
        self.assertEqual(scan_targets([NetworkAddress("10.2.3.7", 32)]), ["10.2.3.7"])

    def test_24_scans_254_unique_usable_addresses(self):
        targets = scan_targets([NetworkAddress("172.16.200.28", 24)])
        self.assertEqual(len(targets), 254)
        self.assertEqual(targets[0], "172.16.200.28")
        self.assertEqual(set(targets), {f"172.16.200.{number}" for number in range(1, 255)})

    def test_large_subnet_is_capped_to_local_24_without_off_subnet_probes(self):
        targets = scan_targets([NetworkAddress("10.11.12.13", 16)])
        self.assertEqual(len(targets), 254)
        network = ipaddress.IPv4Network("10.11.12.0/24")
        self.assertTrue(all(ipaddress.IPv4Address(address) in network for address in targets))
        self.assertNotIn("10.11.12.0", targets)
        self.assertNotIn("10.11.12.255", targets)

    def test_own_address_at_broad_subnets_24_boundary_is_kept(self):
        self.assertIn("10.11.12.0", scan_targets([NetworkAddress("10.11.12.0", 16)]))

    def test_interfaces_and_overlapping_subnets_are_deduplicated(self):
        targets = scan_targets([NetworkAddress("10.1.1.5", 24), NetworkAddress("10.1.1.5", 24),
                                NetworkAddress("10.1.1.6", 28)])
        self.assertEqual(len(targets), 254)
        self.assertEqual(targets[:2], ["10.1.1.5", "10.1.1.6"])

    def test_global_cap_is_applied_and_each_interface_is_included_first(self):
        records = [NetworkAddress(f"192.168.{number}.8", 24) for number in range(4)]
        targets = scan_targets(records, max_hosts=999999)
        self.assertEqual(len(targets), MAX_SCAN_HOSTS)
        self.assertEqual(targets[:4], [value.address for value in records])
        self.assertEqual(len(scan_targets(records, max_hosts=3)), 3)
        self.assertEqual(scan_targets(records, max_hosts=0), [])

    def test_public_loopback_linklocal_bad_prefixes_and_hostnames_are_skipped(self):
        records = [NetworkAddress(address, prefix) for address, prefix in (
            ("8.8.8.8", 24), ("192.0.2.3", 24), ("127.0.0.1", 8),
            ("169.254.1.2", 16), ("224.1.1.1", 24), ("0.0.0.0", 0),
            ("10.1.1.2", -1), ("10.1.1.2", 33), ("10.1.1.2", True),
            ("host.local", 24), ("192.168.1.0", 24), ("192.168.1.255", 24),
            ("192.168.1.1/24", 24), ("--help", 24), ("fe80::1", 64),
        )]
        self.assertEqual(scan_targets([None, "10.1.1.2", *records]), [])

    def test_invalid_limits_are_rejected(self):
        for value in (-1, True, 3.5, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                scan_targets([], max_hosts=value)


class DiscoveryTests(unittest.TestCase):
    @mock.patch("usbip_network.socket.create_connection")
    def test_open_port_hosts_are_sorted_and_established_connections_are_closed(self, connect):
        connections = []

        def open_socket(endpoint, timeout):
            self.assertEqual(endpoint[1], 3240)
            self.assertEqual(timeout, 0.1)
            connection = mock.MagicMock()
            connections.append(connection)
            return connection

        connect.side_effect = open_socket
        self.assertEqual(discover_hosts([NetworkAddress("192.168.1.2", 30)], timeout=0.1),
                         ["192.168.1.1", "192.168.1.2"])
        self.assertEqual(connect.call_count, 2)
        for connection in connections:
            connection.__exit__.assert_called_once()

    @mock.patch("usbip_network.socket.create_connection")
    def test_connection_refusal_timeout_and_network_error_are_not_results(self, connect):
        failures = {
            "192.168.1.1": ConnectionRefusedError(),
            "192.168.1.2": socket.timeout(),
            "192.168.1.3": OSError("network unreachable"),
        }

        def open_socket(endpoint, timeout):
            if endpoint[0] in failures:
                raise failures[endpoint[0]]
            return mock.MagicMock()

        connect.side_effect = open_socket
        self.assertEqual(discover_hosts([NetworkAddress("192.168.1.4", 29)]),
                         ["192.168.1.4", "192.168.1.5", "192.168.1.6"])

    @mock.patch("usbip_network.socket.create_connection", side_effect=PermissionError("Operation not permitted"))
    def test_permission_denial_is_reported_instead_of_an_empty_result(self, connect):
        with self.assertRaisesRegex(USBIPError, "Network permissions blocked.*Operation not permitted"):
            discover_hosts([NetworkAddress("192.168.1.2", 32)])

    @mock.patch("usbip_network.socket.create_connection")
    def test_error_and_interruption_cancel_queued_probes(self, connect):
        for failure in (PermissionError("Operation not permitted"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                release = threading.Event()
                timer = threading.Timer(0.05, release.set)

                def open_socket(endpoint, timeout):
                    if endpoint[0] == "192.168.1.2":
                        raise failure
                    release.wait(1)
                    raise socket.timeout()

                connect.reset_mock()
                connect.side_effect = open_socket
                timer.start()
                try:
                    expected = USBIPError if isinstance(failure, PermissionError) else KeyboardInterrupt
                    with self.assertRaises(expected):
                        discover_hosts([NetworkAddress("192.168.1.2", 24)], workers=1)
                    # A whole /24 must not continue after cancellation. The
                    # currently running probe may complete before shutdown.
                    self.assertLess(connect.call_count, 254)
                finally:
                    release.set()
                    timer.cancel()
                    timer.join()

    @mock.patch("usbip_network.network_addresses", return_value=[])
    @mock.patch("usbip_network.socket.create_connection")
    @mock.patch("usbip_network.ThreadPoolExecutor")
    def test_no_addresses_means_no_executor_and_no_probes(self, executor, connect, addresses):
        self.assertEqual(discover_hosts(), [])
        executor.assert_not_called()
        connect.assert_not_called()
        addresses.assert_called_once()

    @mock.patch("usbip_network.network_addresses")
    @mock.patch("usbip_network.socket.create_connection")
    def test_explicit_empty_list_does_not_trigger_interface_lookup(self, connect, addresses):
        self.assertEqual(discover_hosts([]), [])
        addresses.assert_not_called()
        connect.assert_not_called()

    @mock.patch("usbip_network.socket.create_connection")
    def test_untrusted_addresses_never_reach_sockets(self, connect):
        self.assertEqual(discover_hosts([NetworkAddress("example.com", 24),
                                         NetworkAddress("8.8.8.8", 24)]), [])
        connect.assert_not_called()

    def test_invalid_timeouts_and_workers_fail_before_network_activity(self):
        for value in (0, -1, float("nan"), float("inf"), True, "1"):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                discover_hosts([], timeout=value)
        for value in (0, -1, True, "1", 1.5):
            with self.subTest(workers=value), self.assertRaises(ValueError):
                discover_hosts([], workers=value)

    @mock.patch("usbip_network.socket.create_connection", return_value=mock.MagicMock())
    @mock.patch("usbip_network.ThreadPoolExecutor", wraps=ThreadPoolExecutor)
    def test_workers_are_bounded_by_host_count_and_hard_limit(self, executor, connect):
        discover_hosts([NetworkAddress("192.168.1.2", 30)], workers=1000)
        executor.assert_called_once_with(max_workers=2)
        executor.reset_mock()
        discover_hosts([NetworkAddress("192.168.1.2", 24)], workers=1000)
        executor.assert_called_once_with(max_workers=64)


if __name__ == "__main__":
    unittest.main()
