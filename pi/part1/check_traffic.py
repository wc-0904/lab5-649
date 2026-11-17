#!/usr/bin/env python3
"""Observe four real CAN streams for ten seconds; never transmit."""
import argparse
import select
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "part0"))
from can_check import discover


def main():
    """Validate IDs, bus markers and advancing counters on both physical buses."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error("--seconds must be between 1 and 300")
    if sys.platform != "linux":
        parser.error("Run on the Raspberry Pi")
    interfaces = discover()
    stats = {(bus, node): {"count": 0, "last": None, "advances": 0}
             for bus in ("A", "B") for node in (2, 3)}
    sockets = {}
    invalid = 0
    try:
        for bus, interface in interfaces.items():
            sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sockets[sock] = bus
            sock.bind((interface,))
        print(f"Listening for {args.seconds:g} seconds; both Nucleos must be running.", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select(list(sockets), [], [], max(0, deadline - time.monotonic()))
            for sock in ready:
                raw = sock.recv(16)
                if len(raw) != 16:
                    continue
                can_id, dlc, payload = struct.unpack("=IB3x8s", raw)
                if can_id not in (0x702, 0x703):
                    continue
                bus = sockets[sock]
                node = can_id - 0x700
                if (dlc != 8 or payload[0] != node or payload[1] != (bus == "B")
                        or payload[6:] != b"\xa5\x5a"):
                    invalid += 1
                    continue
                counter = int.from_bytes(payload[2:6], "little")
                entry = stats[bus, node]
                if entry["last"] is not None and counter == (entry["last"] + 1) % (1 << 32):
                    entry["advances"] += 1
                entry["last"] = counter
                entry["count"] += 1
    finally:
        for sock in sockets:
            sock.close()
    passed = invalid == 0
    for (bus, node), entry in stats.items():
        good = entry["count"] >= 2 and entry["advances"] >= 1
        passed &= good
        print(f"CAN {bus} N{node} ID=0x{0x700+node:03X}: "
              f"{entry['count']} frames (~{entry['count']/args.seconds:.1f}/s), "
              f"last={entry['last']}: {'PASS' if good else 'FAIL'}")
    print(f"Malformed/wrong-bus diagnostic frames: {invalid}")
    print("PASS requires every stream and advancing counters; expected healthy rate is ~10/s each.")
    print("This is a connectivity check, not a worst-case timing measurement.")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(f"Traffic check FAILED: {error}", file=sys.stderr)
        sys.exit(1)
