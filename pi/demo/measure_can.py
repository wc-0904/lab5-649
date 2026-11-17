#!/usr/bin/env python3
"""Controlled CAN A stimulus for isolated STM measurement images, not checkoff."""
import argparse
from pathlib import Path
import socket
import struct
import sys
import time

from protocol import CAN_FRAME, CTRL, payload

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "part0"))
from can_check import discover


def frame(sequence, elapsed):
    """Valid CTRL at zero throttle; alternate released/applied brake each second."""
    body = struct.pack("<hBBBB", 0, 0, 100 if int(elapsed) % 2 else 0, 0, 0)
    return CAN_FRAME.pack(CTRL, 8, payload(CTRL, sequence, body))


def main():
    """Send one frame every 20 ms on CAN A, with no heartbeat or forwarding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=70)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    interfaces = list(discover().values())
    print("MEASUREMENT ONLY: stop cockpit; flash isolated STM images first.")
    print(f"CTRL stimulus on CAN A ({interfaces[0]}), brake alternates every second.")
    sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, b"")
    sock.setblocking(False)
    sock.bind((interfaces[0],))
    started = time.monotonic()
    sequence = sent = errors = 0
    try:
        while time.monotonic() - started < args.seconds:
            now = time.monotonic()
            try:
                sock.send(frame(sequence, now - started))
                sent += 1
            except OSError as error:
                errors += 1
                if errors == 1:
                    print(f"CAN send failed: {error}")
            sequence = (sequence + 1) & 15
            # Do not create catch-up bursts after a delayed userspace wakeup.
            time.sleep(max(0, now + .020 - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    print(f"Sent={sent}; send errors={errors}. Reject timing runs with send errors.")


if __name__ == "__main__":
    main()
