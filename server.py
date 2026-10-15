"""Share physical USB devices using the operating system's USB/IP server."""

import argparse

from usbip_cli import busid, finish
from usbip_tools import USBIPTools


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check native server prerequisites")
    commands.add_parser("list", help="List local devices and their bus IDs")
    for command, help_text in (
        ("bind", "Share one device (may disconnect it from local applications)"),
        ("unbind", "Stop sharing one device"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument("--busid", required=True, type=busid)
    commands.add_parser(
        "serve", help="Run Linux usbipd in foreground or start the Windows service"
    )
    args = parser.parse_args(argv)

    def execute():
        tools = USBIPTools()
        if args.command == "doctor":
            return tools.doctor("server")
        if args.command == "list":
            if tools.system == "Windows":
                return tools.run("usbipd", ["list"])
            return tools.run("usbip", ["list", "-l"])

        tools.require_admin()
        if args.command == "serve":
            if tools.system == "Windows":
                result = tools.run("sc", ["start", "usbipd"])
                if result in (0, 1056):
                    print("USB/IP service start requested (or already running). "
                          "Run server.py doctor to check readiness.")
                    return 0
                return result
            tools.load_module("usbip_host")
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
