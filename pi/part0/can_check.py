#!/usr/bin/env python3
"""Bring up and verify the two real MCP2515 SocketCAN interfaces; no CAN transmission."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def ip(*args):
    """Run iproute2 and propagate command errors to the caller."""
    return subprocess.run(["ip", *args], check=True, text=True, capture_output=True).stdout


def discover():
    """Map physical chip selects to interface names, independent of probe order."""
    result = {}
    for bus, chip in (("A", "spi0.0"), ("B", "spi0.1")):
        device = Path("/sys/bus/spi/devices") / chip
        if not (device / "driver").exists():
            raise RuntimeError(f"{chip}: no driver; check boot overlays, reboot, and dmesg")
        driver = (device / "driver").resolve().name
        if driver != "mcp251x":
            raise RuntimeError(f"{chip}: driver is {driver}, expected mcp251x")
        names = list((device / "net").glob("*"))
        if len(names) != 1:
            raise RuntimeError(f"{chip}: expected one CAN network interface, found {len(names)}")
        result[bus] = names[0].name
        print(f"CAN {bus}: {chip} -> {names[0].name} ({driver})", flush=True)
    return result


def check(name):
    """Require a running, error-active controller configured for 500 kbit/s."""
    link = json.loads(ip("-details", "-json", "link", "show", "dev", name))[0]
    info = link.get("linkinfo", {})
    data = info.get("info_data", {})
    bitrate = data.get("bittiming", {}).get("bitrate")
    state = data.get("state")
    passed = (info.get("info_kind") == "can" and "UP" in link.get("flags", [])
              and bitrate == 500000 and state == "ERROR-ACTIVE")
    print(ip("-details", "-statistics", "link", "show", "dev", name), end="")
    print(f"{name}: {'PASS' if passed else 'FAIL'} (bitrate={bitrate}, state={state})")
    return passed


def main():
    """Inspect devices, optionally bring both up, and return a failing status on any failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "up", "check"))
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this on the Raspberry Pi, not the firmware build computer")
    if args.action == "up" and os.geteuid() != 0:
        parser.error("Use sudo python3 pi/part0/can_check.py up")
    devices = discover()
    if args.action == "inspect":
        for name in devices.values():
            print(ip("-details", "link", "show", "dev", name), end="")
        return 0
    if args.action == "up":
        for name in devices.values():
            ip("link", "set", "dev", name, "down")
            ip("link", "set", "dev", name, "type", "can", "bitrate", "500000",
               "loopback", "off", "listen-only", "off", "restart-ms", "0")
            ip("link", "set", "dev", name, "up")
    results = [check(name) for name in devices.values()]
    print("No frames sent. This checks controller initialization, not bus wiring or actual bitrate.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Part 0 FAILED: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        print("Inspect: sudo dmesg | tail -n 80", file=sys.stderr)
        sys.exit(1)
