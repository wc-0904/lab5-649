#!/usr/bin/env python3
"""Configure the two physical CAN interfaces for the template demo, before starting traffic."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "part0"))
from can_check import discover, ip


def main():
    """Enable 500 kbit/s and 20 ms automatic bus-off restart independently."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Run on the Pi: sudo python3 pi/demo/setup_can.py")
    for name in discover().values():
        ip("link", "set", "dev", name, "down")
        ip("link", "set", "dev", name, "txqueuelen", "8")
        ip("link", "set", "dev", name, "type", "can", "bitrate", "500000",
           "loopback", "off", "listen-only", "off", "restart-ms", "20")
        ip("link", "set", "dev", name, "up")
        print(ip("-details", "link", "show", "dev", name), end="")
    print("Run once before cockpit. Do not reset interfaces during a recovery timing test.")


if __name__ == "__main__":
    main()
