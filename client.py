"""Attach remote USB devices using Linux USB/IP or native Windows usbip-win2."""

import argparse

from usbip_cli import busid, finish, host, port
from usbip_tools import USBIPError, USBIPTools


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check native client prerequisites")
    listing = commands.add_parser("list", help="List devices exported by a server")
    listing.add_argument("--host", required=True, type=host)
    attach = commands.add_parser("attach", help="Attach a device to this computer")
    attach.add_argument("--host", required=True, type=host)
    attach.add_argument("--busid", required=True, type=busid)
    detach = commands.add_parser("detach", help="Detach an imported client port")
    detach.add_argument("--port", required=True, type=port)
    commands.add_parser("port", help="Show imported devices and client port numbers")
    args = parser.parse_args(argv)

    def execute():
        tools = USBIPTools()
        if args.command == "doctor":
            return tools.doctor("client")
        if args.command == "list":
            return tools.run("usbip", ["list", "-r", args.host])
        if args.command == "port":
            return tools.run("usbip", ["port"])

        if args.command == "detach" and tools.system == "Windows" and args.port == 0:
            raise USBIPError("Windows client ports start at 1. "
                             "Run client.py port to find the imported port.")
        tools.require_admin()
        if args.command == "detach":
            return tools.run("usbip", ["detach", "-p", str(args.port)])
        if tools.system == "Linux":
            tools.load_module("vhci_hcd")
        arguments = ["attach", "-r", args.host, "-b", args.busid]
        if tools.system == "Windows":
            # Avoid kernel background retries when the initial attachment fails.
            arguments.append("--once")
        return tools.run("usbip", arguments)

    return finish(execute)


if __name__ == "__main__":
    raise SystemExit(main())
