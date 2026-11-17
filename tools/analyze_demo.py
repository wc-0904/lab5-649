#!/usr/bin/env python3
"""Relay-enabled illustrative RTA, microseconds. Budgets are NOT measurements."""
import math

CATALOG = [(0x10, "CTRL", 20000, 2000),
           (0x20, "FAULT_N1", 20000, 100000),
           (0x21, "FAULT_N2", 20000, 100000),
           (0x22, "FAULT_N3", 20000, 100000),
           (0x100, "HB_N1", 20000, 20000),
           (0x101, "HB_N2", 20000, 20000),
           (0x102, "HB_N3", 20000, 20000),
           (0x200, "BLINK", 20000, 100000),
           (0x300, "FORCE", 50000, 50000)]
RELAY_IDS = {row[0] for row in CATALOG}


def fixed_point(cost, blocking, higher):
    """Response with provisional 10 ms higher-priority release jitter."""
    r = cost + blocking
    for _ in range(1000):
        new = cost + blocking + sum(math.ceil((r + 10000) / t) * c for c, t in higher)
        if new == r:
            return r
        if new > 1000000:
            raise ValueError("model did not converge within one second")
        r = new
    raise ValueError("model failed to converge")


def node(node_id):
    """Aggregate bursts, including same-priority relay and TX service work."""
    # Conservative per-bus envelope: 9 originals plus two copies of every ID.
    rx = 54
    tx = 12 if node_id == 2 else 11
    relay_jobs = 6 if node_id == 2 else 7
    rows = [("IRQ batch", rx * 5, 20000, 300, 5),
            ("RX driver batch", rx * 55, 20000, 300, 50),
            ("dispatch batch", rx * 20, 20000, 300, 5),
            ("TX A/B + RELAY service batch", tx * 60 + relay_jobs * 10,
             20000, 2000, 50)]
    rows += ([("BLINK_F", 40, 20000, 500, 5), ("STEER", 55, 20000, 1000, 5)]
             if node_id == 2 else
             [("DRIVE", 55, 20000, 500, 50), ("BLINK_R", 40, 20000, 500, 5)])
    rows += [("TEST/FAULT enqueue", 30, 20000, 10000, 5)]
    if node_id == 3:
        rows += [("PID_SIM", 200, 10000, 10000, 5)]
    rows += [("HB enqueue", 10, 20000, 20000, 5),
             ("HB_MON", 30, 20000, 20000, 5)]
    if node_id == 2:
        rows += [("FORCE enqueue", 10, 50000, 50000, 5)]
    rows += [("watchdog supervisor", 20, 100000, 100000, 5)]
    higher, results = [], {}
    for name, c, t, d, b in rows:
        r = fixed_point(c, b, higher)
        print(f"| N{node_id} {name} | {c} | {b} | {r} | {d} | {'within assumption' if r <= d else 'not established'} |")
        higher.append((c, t))
        results[name] = r
    return results


def bus(relay=False):
    """Ideal ID-priority bus: include same-ID forwarded streams as interference.

    Ten ms jitter for forwarded frames is a provisional envelope, not a derived
    proof. Software expiry may drop traffic earlier; an RTA cannot certify delivery.
    """
    streams = [(i, n, t, d, 500 if i in (0x10, 0x20, 0x100, 0x200) else 5000)
               for i, n, t, d in CATALOG]
    if relay:
        streams += [(i, n + f" relay copy {copy}", t, d, 10000)
                    for copy in (1, 2) for i, n, t, d in CATALOG]
    for index, (can_id, name, period, deadline, jitter) in enumerate(streams):
        blocking = 270 if any(other[0] > can_id for other in streams) else 0
        w = blocking
        for _ in range(1000):
            new = blocking + sum(math.ceil((w + other[4] + 2) / other[2]) * 270
                                 for j, other in enumerate(streams)
                                 if j != index and other[0] <= can_id)
            if new == w:
                break
            w = new
        print(f"| {name} | {jitter} | 270 | {blocking} | {w} | {jitter+w+270} | {deadline} |")
    utilization = sum(270 / row[2] for row in streams)
    print(f"\nPer-bus envelope utilization: **{utilization:.2%}**.\n")


def main():
    """Print reproducible tables and explicitly conditional end-to-end estimates."""
    print("# Relay-enabled illustrative tables\n\nNot measured; see ANALYSIS.md for limitations.\n")
    print("| Task / burst | C us | B us | R us | D us | Interpretation |\n|---|---:|---:|---:|---:|---|")
    n2 = node(2)
    n3 = node(3)
    for relay in (False, True):
        print(f"\n## Ideal bus envelope: {'with relay copies' if relay else 'original catalog'}\n")
        print("| Message | J us | C us | B us | w us | R us | D us |\n|---|---:|---:|---:|---:|---:|---:|")
        bus(relay)
    # Optimistic wire hops (one lower-priority frame + CTRL), pessimistic CPU batches.
    brake = 500 + 540 + n2['dispatch batch'] + n2['TX A/B + RELAY service batch'] + 540 + n3['dispatch batch'] + n3['DRIVE']
    print(f"Illustrative two-hop brake sum = **{brake / 1000:.3f} ms**, against 2 ms.")
    print("This mixes assumed stage envelopes and is not a validated end-to-end bound.")


if __name__ == "__main__":
    main()
