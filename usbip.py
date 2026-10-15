"""Share or use USB devices through a guided terminal menu."""

import argparse
import sys

from usbip_cli import finish
from usbip_menu import main_menu


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("open this menu in a terminal; for scripts, use server.py or client.py subcommands")
    return finish(main_menu)


if __name__ == "__main__":
    raise SystemExit(main())
