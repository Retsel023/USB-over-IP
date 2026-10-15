"""Share USB devices plugged into this computer (the USB/IP server role)."""

import argparse
import sys

from usbip_cli import busid, finish
from usbip_tools import USBIPTools


def main(argv=None):
    if argv is None and len(sys.argv) == 1 and sys.stdin.isatty():
        from usbip_menu import server_menu
        return finish(server_menu)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("menu", help="Choose USB devices through a guided menu")
    commands.add_parser("doctor", help="Check prerequisites for sharing local USB devices")
    commands.add_parser("list", help="List local devices and their bus IDs")
    for command, help_text in (
        ("bind", "Share one device (may disconnect it from local applications)"),
        ("unbind", "Stop sharing one device"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument(
            "--busid", required=True, type=busid,
            help="USB bus ID from 'list', such as 3-5.1; use 'menu' to choose by number",
        )
    commands.add_parser(
        "serve", help="Run Linux usbipd in foreground or start the Windows service"
    )
    commands.add_parser("start", help="Start the sharing service in the background")
    args = parser.parse_args(argv)
    if args.command == "menu":
        from usbip_menu import server_menu
        return finish(server_menu)

    def execute():
        tools = USBIPTools()
        if args.command == "doctor":
            return tools.doctor("server")
        if args.command == "list":
            if tools.system == "Windows":
                return tools.run("usbipd", ["list"])
            return tools.run("usbip", ["list", "-l"])

        tools.require_admin()
        if args.command in ("serve", "start"):
            if tools.system == "Windows":
                result = tools.run("sc", ["start", "usbipd"])
                if result in (0, 1056):
                    print("USB/IP service start requested (or already running). "
                          "Run server.py doctor to check readiness.")
                    return 0
                return result
            tools.load_module("usbip_host")
            if args.command == "start":
                result = tools.run("usbipd", ["-D"])
                if result == 0:
                    print("USB/IP sharing service start requested in the background. "
                          "Use unbind to stop sharing individual devices.")
                return result
            print("Starting usbipd on TCP 3240 in the foreground. "
                  "Ctrl+C stops this daemon; use unbind to stop sharing devices.",
                  flush=True)
            return tools.run("usbipd", [], timeout=None)

        if tools.system == "Windows":
            return tools.run("usbipd", [args.command, "--busid", args.busid])
        if args.command == "bind":
            tools.load_module("usbip_host")
        return tools.run("usbip", [args.command, "-b", args.busid])

    return finish(execute)


if __name__ == "__main__":
    raise SystemExit(main())
