#!/usr/bin/env python3
"""Generate simulated wheel UDP updates; use only with disconnected actuators."""
import argparse
import socket
import struct
import time


def main():
    """Send the actual Lab 2 datagram shape at 20 ms intervals."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--throttle", type=int, default=25)
    parser.add_argument("--brake", type=int, default=0)
    parser.add_argument("--steer", type=int, default=0, help="normalized -1000..1000")
    parser.add_argument("--button", type=int, choices=range(8), help="pulse this button at second two")
    parser.add_argument("--brake-cycle", action="store_true",
                        help="alternate brake 0/100 every second for scope timing")
    parser.add_argument("--test-recovery", action="store_true",
                        help="self-test at 2 s, then recovery presses at 4 and 4.3 s")
    args = parser.parse_args()
    if args.test_recovery and args.button is not None:
        parser.error("use either --button or --test-recovery")
    if not 0 <= args.throttle <= 100 or not 0 <= args.brake <= 100 or not -1000 <= args.steer <= 1000:
        parser.error("pedals 0..100, steering -1000..1000")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    started = next_send = time.monotonic()
    sequence = 0
    try:
        while time.monotonic() - started < args.seconds:
            elapsed = time.monotonic() - started
            brake = (100 if int(elapsed) % 2 else 0) if args.brake_cycle else args.brake
            packet = bytearray(276)
            struct.pack_into("<Iii", packet, 0, sequence,
                             round(args.steer * 32767 / 1000),
                             32767 - round(args.throttle * 65535 / 100))
            struct.pack_into("<i", packet, 24, 32767 - round(brake * 65535 / 100))
            if args.button is not None and 2 <= time.monotonic() - started < 2.1:
                packet[52 + args.button] = 128
            if args.test_recovery and any(t <= elapsed < t + .080 for t in (2, 4, 4.3)):
                packet[55] = 128
            sock.sendto(packet, (args.host, args.port))
            sequence = (sequence + 1) & 0xFFFFFFFF
            next_send += .020
            time.sleep(max(0, next_send - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
