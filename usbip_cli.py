"""Input validation and error reporting shared by the two entry points."""

import argparse
import ipaddress
import re
import sys

from usbip_tools import USBIPError


def busid(value):
    if len(value) > 31 or not re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*", value):
        raise argparse.ArgumentTypeError("use a device bus ID such as 1-2 or 1-2.3")
    return value


def host(value):
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    labels = value.rstrip(".").split(".")
    if len(value) > 253 or not all(
        re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
        for label in labels
    ):
        raise argparse.ArgumentTypeError("use an IP address or hostname, without a URL or port")
    return value


def port(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use the numeric client port from client.py port") from None
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("client port must be between 0 and 65535")
    return number


def finish(action):
    try:
        return action()
    except USBIPError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
