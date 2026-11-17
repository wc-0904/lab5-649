#!/usr/bin/env python3
"""Historical pre-relay first-pass RTA in microseconds; assumptions, never measurements."""
import math


def response(cost, block, higher):
    """Fixed point for interference tuples (C,T,J); stop on divergence."""
    value = cost + block
    for _ in range(1000):
        candidate = cost + block + sum(math.ceil((value + j) / t) * c for c, t, j in higher)
        if candidate == value:
            return value
        if candidate > 1000000:
            return float("inf")
        value = candidate
    return float("inf")


def node(node_id, buses=1, messages=7):
    """Conservatively batch ISR/driver/dispatch releases rather than one frame/20ms.

    ISR 5 + worker 55 + dispatch 20 = handout 80 us per frame. The batch
    response bounds the last frame of a simultaneous burst, not just CTRL.
    Single-bus receive cost changes; TX C stays at handout's conservative
    two-bus budget 120 us. Equal-priority driver workers are aggregated.
    J=4000 us is a provisional receive-release jitter envelope, not measured.
    """
    arrivals = buses * messages
    rows = [("ISR batch", arrivals * 5, 20000, 300, 5, 4000),
            ("driver batch", arrivals * 55, 20000, 300, 50, 4000),
            ("dispatch batch", arrivals * 20, 20000, 300, 5, 4000)]
    if node_id == 2:
        rows += [("BLINK_F", 20, 20000, 500, 5, 4000),
                 ("STEER", 50, 20000, 1000, 5, 4000),
                 ("TEST/FAULT", 140, 20000, 10000, 50, 0),
                 ("HB_TX", 120, 20000, 20000, 50, 0),
                 ("HB_MON", 30, 20000, 20000, 5, 0),
                 ("FORCE_TX", 120, 50000, 50000, 50, 0)]
    else:
        rows += [("DRIVE", 50, 20000, 500, 50, 4000),
                 ("BLINK_R", 20, 20000, 500, 5, 4000),
                 ("TEST/FAULT", 140, 20000, 10000, 50, 0),
                 ("PID_SIM", 200, 10000, 10000, 5, 0),
                 ("HB_TX", 120, 20000, 20000, 50, 0),
                 ("HB_MON", 30, 20000, 20000, 5, 0)]
    rows += [("recovery (healthy)", 20, 20000, 20000, 5, 0),
             ("supervisor", 20, 100000, 100000, 5, 0)]
    higher, result = [], {}
    for name, c, t, d, b, j in rows:
        r = response(c, b, higher)
        result[name] = r
        print(f"| N{node_id} {name} | {c} | {b} | {t} | {r} | {d} | {'PASS*' if r <= d else 'MISS'} |")
        higher.append((c, t, j))
    return result


def bus():
    """Catalog arbitration with one aggregate FAULT source and bounded jitter inputs."""
    rows = [("CTRL", 20000, 2000, 500), ("FAULT", 20000, 100000, 2000),
            ("HB_N1", 20000, 20000, 500), ("HB_N2", 20000, 20000, 2000),
            ("HB_N3", 20000, 20000, 2000), ("BLINK", 20000, 100000, 500),
            ("FORCE", 50000, 50000, 2000)]
    higher = []
    utilization = 0
    for i, (name, t, d, j) in enumerate(rows):
        c = 270
        b = 270 if i < len(rows) - 1 else 0
        w = b
        for _ in range(1000):
            updated = b + sum(math.ceil((w + jitter + 2) / period) * 270
                              for period, jitter in higher)
            if updated == w:
                break
            w = updated
        r = j + w + c
        print(f"| {name} | {j} | {c} | {b} | {w} | {r} | {d} |")
        higher.append((t, j))
        utilization += c / t
    print(f"\nPer-bus utilization (one aggregate FAULT stream): {utilization:.2%}")
    print(f"Three independent FAULT sources instead: {utilization + 2*270/20000:.2%}")


def main():
    """Print copyable tables with all assumptions explicit in docs/ANALYSIS.md."""
    for buses in (1, 2):
        print(f"\n{buses} bus(es), seven-message burst envelope\n")
        print("| Task | C us | B us | T us | R us | D us | Result |")
        print("|---|---:|---:|---:|---:|---:|---|")
        for node_id in (2, 3):
            node(node_id, buses)
    print("\nBus RTA (ideal ID-priority queue; local controller ordering excluded)\n")
    print("| Message | J us | C us | B us | w us | R us | D us |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    bus()
    print("\nSensitivity: dual buses, nine-message burst (3 FAULT sources)\n")
    for node_id in (2, 3):
        node(node_id, 2, 9)


if __name__ == "__main__":
    main()
