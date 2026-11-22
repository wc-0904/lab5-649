#!/usr/bin/env python3
"""Lab 3 cockpit: UDP, parallel CAN, blinker master, health and timing markers."""
import argparse
import csv
import errno
from pathlib import Path
import selectors
import socket
import struct
import sys
import time

from protocol import (APPEND, APPEND_ACK_BASE, CLIENT_REDIRECT as LAB5_CLIENT_REDIRECT,
                      LAB5_FILTER_ID, LAB5_FILTER_MASK,
                      LAB5_RELAY_FLAG, REF, REQ_VOTE_BASE, VOTE_BASE,
                      is_consensus, is_relayed,
                      origin_id, relay_id,
                      CTRL, FAULT, HB1, HB2, HB3, BLINK, FORCE, CATALOG, FAULT_IDS,
                      CAN_FRAME, Dedup, payload, valid, decode_wheel, origin)
from pps import Pps
from time_sync import local_cycles as local_cycles_now
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "part0"))
from can_check import discover
from cluster_config import load as load_cluster_config, node_id as cluster_node_id
from raft_agent import RaftAgent
from network_state import event_link, event_node
from protocol import APPEND_ACK_BASE as _ACK, CLIENT_REQ_BASE, lab5_id_for


def catalog_filters():
    """Subscribe to the Lab 3 catalog and the Lab 5 range, excluding EFF and RTR.

    The Lab 3 identifiers are matched exactly. The eleven Lab 5 consensus
    identifiers are matched as one range, the same way the firmware does it, so
    the leader receives every follower's acknowledgement without a filter each.
    REF gets its own exact filter because it sits below LAB_CTRL, outside that
    range.
    """
    # CAN_EFF_FLAG | CAN_RTR_FLAG | CAN_SFF_MASK. CAN_ERR_FLAG must stay clear:
    # Linux treats a mask containing it as an error subscription, not a data filter.
    exact = [(can_id, 0xC00007FF) for can_id in CATALOG]
    exact.append((REF, 0xC00007FF))
    exact.append((LAB5_FILTER_ID, 0xC0000000 | LAB5_FILTER_MASK))
    # Every relayed copy, from any relayer. The relayer's ID sits in the
    # identifier so two of them arbitrate rather than colliding, which means
    # one filter covers the whole range instead of one per relayer.
    exact.append((LAB5_RELAY_FLAG, 0xC0000000 | LAB5_RELAY_FLAG))
    return b"".join(struct.pack("=II", can_id, mask) for can_id, mask in exact)


#: Half period of the normal blinker, microseconds. 2 Hz means two full on-off
#: cycles a second, so an edge every 250 ms. Must match BLINK_HALF_US in
#: src/lab_tasks.c; the two endpoints divide the same timeline.
BLINK_HALF_US = 250000


class Pins:
    """GPIO ownership via libgpiod v2, with explicit non-measuring opt-out."""
    # Lab 3 markers, then the Lab 5 outputs. See docs/DEMO_WIRING.md.
    OFFSETS = (23, 22, 17, 27, 5, 6, 13, 19, 26)
    UDP, CMD, TX_A, TX_B, DEG_A, DEG_B, PPS, LEADER, SYNC = range(9)

    def __init__(self, disabled):
        self.request = None
        self.level = [False] * len(self.OFFSETS)
        if not disabled:
            import gpiod
            from gpiod.line import Direction, Value
            self.value = Value
            self.request = gpiod.request_lines(
                "/dev/gpiochip0", consumer="lab3-cockpit",
                config={pin: gpiod.LineSettings(direction=Direction.OUTPUT,
                                                output_value=Value.INACTIVE)
                        for pin in self.OFFSETS})

    def set(self, pin, level):
        """Set one marker/LED."""
        self.level[pin] = bool(level)
        if self.request:
            self.request.set_value(self.OFFSETS[pin],
                                   self.value.ACTIVE if level else self.value.INACTIVE)

    def toggle(self, pin):
        """Toggle marker on an observed event."""
        self.set(pin, not self.level[pin])

    def close(self):
        """Release GPIO handles."""
        if self.request:
            self.request.release()


class Cockpit:
    """One event loop implements the Pi logical tasks; Linux latency is measured."""
    def __init__(self, args):
        self.args = args
        self.pins = Pins(args.no_gpio)
        # The Pi is the default time master, so its edge is the baseline every
        # STM32 edge is compared against. It is also the worst edge in the
        # cluster, for reasons pps.py documents.
        self.pps = Pps(self.pins, Pins.PPS, enabled=not args.no_gpio and not args.no_pps)
        self.selector = selectors.DefaultSelector()
        self.can = []
        for bus, name in discover().items():
            sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, catalog_filters())
            sock.setblocking(False)
            sock.bind((name,))
            self.selector.register(sock, selectors.EVENT_READ, len(self.can))
            self.can.append(sock)
        self.cluster = load_cluster_config()
        self.agent = RaftAgent(self.cluster, cluster_node_id(self.cluster, args.node),
                               self.send_lab5)
        print("config  hash=%s N=%d majority=%d leader=%s"
              % (self.cluster["hash"], self.cluster["N"], self.cluster["majority"],
                 self.cluster["default_leader"]), flush=True)
        print(self.agent.summary(), flush=True)
        self.show_raft_state()
        # Bootstrap the log with what the leader knows about itself. Link and
        # node detection for the peers is P5; this is enough for the entries to
        # replicate and commit.
        # The agent queues this node's own reports and drives them from the
        # run loop, so they survive this node losing the leadership.
        self.seen_nodes = {self.agent.node_id}
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setblocking(False)
        self.udp.bind((args.bind, args.port))
        self.selector.register(self.udp, selectors.EVENT_READ, "udp")
        self.sequence = {can_id: 0 for can_id in CATALOG}
        self.dedup = Dedup()
        self.heartbeat = [[float("-inf")] * 2 for _ in range(3)]
        self.fault_at = [float("-inf")] * 3
        self.local_fail = False
        self.error = True
        self.degraded = [True, True]
        self.last_udp = float("-inf")
        self.last_wheel_sequence = None
        self.command = None
        self.last_ctrl_tx = float("-inf")
        self.pending_command = None
        self.last_blink = float("-inf")
        self.last_fault = float("-inf")
        self.blink_state = 0
        self.desired_blink = 0
        self.phase = 0
        self.next_edge = time.monotonic()
        self.button_previous = 0
        self.recovery_press = None
        self.force = 0
        self.remote = None
        self.samples = []
        self.sample_overflow = 0
        self.send_errors = [0, 0]
        # Last frame seen on each bus, which is how this node detects its own
        # link failures: on a healthy bus something arrives every 20 ms.
        self.last_rx = [float("-inf"), float("-inf")]
        self.bad_frames = 0
        self.inject = args.inject_invalid
        self.forwarded = [0, 0]

    def write_bus(self, bus, frame):
        """One nonblocking SocketCAN write; failure never stalls the other bus."""
        self.pins.toggle(2 + bus)
        try:
            self.can[bus].send(frame)
            return True
        except OSError as error:
            if error.errno not in (errno.ENOBUFS, errno.ENETDOWN, errno.EAGAIN,
                                  errno.ENETUNREACH, errno.EIO):
                raise
            self.send_errors[bus] += 1
            return False

    def send(self, can_id, body=b""):
        """Originate identical copies with one sequence increment."""
        data = payload(can_id, self.sequence[can_id], body)
        self.sequence[can_id] = (self.sequence[can_id] + 1) & 15
        frame = CAN_FRAME.pack(can_id, 8, data)
        return sum(self.write_bus(bus, frame) for bus in (0, 1))

    def receive_lab5(self, can_id, data, now, now_cycles=None):
        """Hand a Lab 5 frame to the agent.

        Runs in the event loop beside the Lab 3 receive path, so nothing here
        may block; the agent only updates counters and indices.
        :param can_id: Lab 5 identifier.
        :param data: Eight received bytes.
        :param now: Monotonic time from the caller's loop.
        :param now_cycles: Local microsecond counter captured at the socket read.
        """
        if can_id == LAB5_CLIENT_REDIRECT:
            # The leader's answer to one of this node's own requests.
            self.agent.on_reply(data)
            return
        if can_id == APPEND:
            # Only a follower applies these. A leader hearing its own append
            # back on the other bus must ignore it, and a leader that is no
            # longer current learns so here: this is the only frame carrying a
            # term it has to respect.
            before = self.agent.is_leader()
            ack = self.agent.on_append(data)
            if ack is not None:
                self.send_lab5(*ack)
            if before != self.agent.is_leader():
                self.show_raft_state()
            return
        if can_id == REF:
            # A leader hears its own REF back on the other bus; on_ref ignores
            # that. While following, this is what puts the Pi on the shared
            # timeline, and SYNC then means a live master rather than self.
            if self.agent.on_ref(data, now_cycles):
                self.pins.set(Pins.SYNC, True)
            return
        for node in range(1, self.cluster["N"] + 1):
            if node == self.agent.node_id:
                continue
            if can_id == lab5_id_for(REQ_VOTE_BASE, node):
                self.send_lab5(*self.agent.on_vote_request(node, data))
                return
            if can_id == lab5_id_for(VOTE_BASE, node):
                if self.agent.on_vote(node, data):
                    self.show_raft_state()
                return
            if can_id == lab5_id_for(APPEND_ACK_BASE, node):
                if self.agent.on_ack(node, data):
                    self.show_raft_state()
                return
            if can_id == lab5_id_for(CLIENT_REQ_BASE, node):
                reply = self.agent.on_client_request(node, data)
                if reply is not None:
                    self.send_lab5(*reply)
                return
        # REF, APPEND and our own replies return on the other bus; ignore them.

    def show_raft_state(self):
        """Drive the LEADER and SYNC indicators from the agent's role.

        Checkoff step 1 reads these directly: LEADER on the Pi only, SYNC on all
        three. While this node leads it is its own time master, so it is
        trivially synchronized; P9 extends SYNC to track a remote master.
        """
        self.pins.set(Pins.LEADER, self.agent.is_leader())
        self.pins.set(Pins.SYNC, self.agent.is_leader())

    def send_lab5(self, can_id, data):
        """Send a Lab 5 frame on both buses with the payload untouched.

        send() stamps a Lab 3 sequence number into byte zero; consensus and time
        frames use all eight bytes, so they bypass it.
        :param can_id: Lab 5 identifier.
        :param data: Eight bytes, sent exactly as given.
        """
        frame = CAN_FRAME.pack(can_id, 8, data)
        for bus in (0, 1):
            self.write_bus(bus, frame)

    def forward(self, can_id, data, ingress):
        """Relay a validated first copy unchanged to the opposite interface.

        Runs in the selector event loop, so Linux scheduling/queue delays remain
        measured inputs. No userspace retry backlog is created after a failed send.
        """
        if self.local_fail:
            return
        destination = 1 - ingress
        if self.write_bus(destination, CAN_FRAME.pack(can_id, 8, data)):
            self.forwarded[destination] += 1

    def fault(self, reason, now):
        """Send one FAULT per local failure episode; stop heartbeats until recovery."""
        if self.local_fail:
            return
        self.recovery_press = None
        self.local_fail = True
        self.error = True
        if now - self.last_fault >= .020:
            self.send(FAULT, bytes((1, reason)))
            self.last_fault = now

    def test_press(self, now):
        """Single press fails; two subsequent presses within 500 ms recover."""
        if not self.local_fail:
            self.fault(1, now)
        elif self.recovery_press is not None and now - self.recovery_press <= .500:
            self.local_fail = False
            self.recovery_press = None
            self.pending_command = None
            # monitor() still requires fresh input and both peers before clearing error.
        else:
            self.recovery_press = now

    def udp_rx(self, now):
        """Validate one wheel update and retain the latest command."""
        raw, remote = self.udp.recvfrom(2048)
        received_ns = time.monotonic_ns()
        self.pins.toggle(Pins.UDP)
        try:
            sequence, steer, throttle, brake, buttons = decode_wheel(raw)
        except ValueError:
            self.fault(2, now)
            return
        if self.last_wheel_sequence is not None and now - self.last_udp < .060:
            delta = (sequence - self.last_wheel_sequence) & 0xFFFFFFFF
            if delta == 0 or delta >= 0x80000000:
                return
        self.last_wheel_sequence = sequence
        self.last_udp = now
        self.remote = remote
        if buttons & 8 and not self.button_previous & 8:
            self.test_press(now)
        # Rising edges toggle left/right request; both buttons request hazards.
        rising = buttons & ~self.button_previous
        if rising & 0x30 == 0x30:
            self.desired_blink = 0 if self.desired_blink == 3 else 3
        elif rising & 0x20:
            self.desired_blink = 0 if self.desired_blink == 1 else 1
        elif rising & 0x10:
            self.desired_blink = 0 if self.desired_blink == 2 else 2
        self.button_previous = buttons
        if self.inject:
            throttle = 101  # Valid CRC, invalid semantic range.
            self.inject = False
        self.pending_command = (struct.pack("<hBBBB", steer, throttle, brake, buttons, 0),
                                received_ns)

    def ctrl_tx(self, now):
        """Publish at minimum 20 ms spacing; record UDP dequeue-to-write latency."""
        if self.local_fail or self.pending_command is None or now - self.last_ctrl_tx < .020:
            return
        body, received_ns = self.pending_command
        self.pending_command = None
        data = payload(CTRL, self.sequence[CTRL], body)
        self.sequence[CTRL] = (self.sequence[CTRL] + 1) & 15
        frame = CAN_FRAME.pack(CTRL, 8, data)
        first_ok = self.write_bus(0, frame)
        self.pins.toggle(Pins.CMD)
        elapsed = (time.monotonic_ns() - received_ns) / 1000
        self.write_bus(1, frame)
        sent = int(first_ok)
        if len(self.samples) < 200000:
            self.samples.append((received_ns, elapsed, sent))
        else:
            self.sample_overflow += 1
        self.last_ctrl_tx = now

    def receive(self, bus, now):
        """Record per-bus reachability, suppress copies, and bridge foreign traffic."""
        raw = self.can[bus].recv(16)
        self.last_rx[bus] = now
        if len(raw) != 16:
            self.bad_frames += 1
            return
        can_id, length, data = CAN_FRAME.unpack(raw)
        if length != 8:
            self.bad_frames += 1
            return
        # Not named `origin`: that is the Lab 3 helper deciding who sent a
        # catalog frame, and it is called a few lines below.
        base_id = origin_id(can_id)
        if base_id == REF or is_consensus(base_id):
            # One relayed copy onto the other bus, before handling it. A node
            # holding both links bridges reflexively rather than consulting its
            # routing table, so there is no convergence window for traffic to
            # fall into; see TASKS.md D25.
            if not is_relayed(can_id) and not self.local_fail:
                self.forward(relay_id(base_id, self.agent.node_id), data, bus)
            # Lab 5 traffic: valid() covers the Lab 3 catalog only, so these
            # would otherwise be counted as malformed. The counter is read here
            # rather than inside receive_lab5 so the sync instant is as close to
            # the socket read as Python allows (TASKS.md D17).
            self.receive_lab5(base_id, data, now, local_cycles_now())
            return
        if not valid(can_id, data):
            self.bad_frames += 1
            return
        if origin(can_id, data) == 1:
            return  # Returning copies of our own traffic must never be relayed.
        if can_id in (HB2, HB3):
            peer = can_id - HB1 + 1
            self.heartbeat[can_id - HB1][bus] = now
            if peer not in self.seen_nodes:
                # First sight of a peer: record it so the graph is not empty.
                self.seen_nodes.add(peer)
                self.agent.client_append(event_node(peer, True))
        if self.dedup.duplicate(can_id, data, bus, now):
            return
        self.forward(can_id, data, bus)
        if can_id in FAULT_IDS:
            self.fault_at[data[1] - 1] = now
            self.error = True
        elif can_id == FORCE:
            self.force = struct.unpack("b", data[1:2])[0]
            if self.remote and self.args.force_port:
                # Optional diagnostic feedback: one signed byte to explicit port.
                try:
                    self.udp.sendto(data[1:2], (self.remote[0], self.args.force_port))
                except OSError:
                    pass

    def monitor(self, now):
        """20 ms health poll; single-bus degradation does not imply zone failure."""
        self.degraded = [any(now - self.heartbeat[p][b] >= .040 for p in (1, 2))
                         for b in (0, 1)]
        peer_failed = any(now - max(self.heartbeat[p]) >= .060 or
                          max(self.heartbeat[p]) <= self.fault_at[p] for p in (1, 2))
        self.error = self.local_fail or now - self.last_udp >= .060 or peer_failed
        for bus in (0, 1):
            self.pins.set(Pins.DEG_A + bus, self.degraded[bus])
        self.show_raft_state()

    def blink_tx(self, now):
        """Announce which lamps are selected; the nodes own the phase.

        From P8 the blink phase is a pure function of global time on every
        node, so this message no longer carries timing. It carries intent:
        left, right, both or cancel. The phase byte is still sent because the
        Lab 3 catalog defines the frame that way and the two endpoints have to
        agree on the layout, and because it is a useful cross check against the
        node's own phase when reading a candump.

        A direction change goes out at once rather than waiting for an edge, so
        left, right and cancel feel immediate at the checkoff.

        :param now: Monotonic time from the caller's loop.
        """
        desired = 3 if self.error else self.desired_blink
        if now - self.last_blink < .020:
            return
        changed = desired != self.blink_state
        if not changed and now < self.next_edge:
            return
        self.blink_state = desired
        self.phase = int(self.blink_phase())
        self.send(BLINK, bytes((desired, self.phase)))
        self.last_blink = now
        self.next_edge = now + (.250 if self.error else .500)

    def blink_phase(self):
        """Derive the blink phase from global time, as the nodes do.

        Must match blink_phase() in src/lab_tasks.c: the same division of the
        same timeline, so the Pi's view of the phase and a node's cannot drift
        apart. Hazards run fast only when the cockpit itself is gone, which
        cannot be true here, so this node always uses the 2 Hz half period.

        :return: True for the lit half of the cycle.
        """
        return (self.agent.global_us() // BLINK_HALF_US) % 2 == 1

    def drift_report(self):
        """Print the global time and the drift the timeline is correcting for.

        Part 6 requires a periodic report of the drift error. On the leader
        there is nothing to correct, so the line states that outright rather
        than printing a ratio of one and inviting the question.
        """
        if not self.cluster["Debug"]:
            return
        global_us = self.agent.global_us()
        line = f"sync: global={global_us // 1000000}.{global_us % 1000000:06d}"
        if self.agent.is_leader():
            print(f"{line} master=self")
        else:
            print(f"{line} ratio={self.agent.sync.ppm()} ppm "
                  f"correction={self.agent.sync.correction_us} us")

    def run(self):
        """Service critical CTRL first, then bounded socket batches and timers."""
        self.pps.start()
        started = time.monotonic()
        next_hb = next_monitor = next_drift = started
        try:
            while not self.args.duration or time.monotonic() - started < self.args.duration:
                for key, _ in self.selector.select(.001):
                    now = time.monotonic()
                    if key.data == "udp":
                        self.udp_rx(now)
                    else:
                        self.receive(key.data, now)
                    self.ctrl_tx(time.monotonic())
                now = time.monotonic()
                self.ctrl_tx(now)
                if now >= next_monitor:
                    self.monitor(now)
                    next_monitor = now + .020
                if now >= next_hb:
                    if not self.local_fail:
                        health = int(not self.degraded[0]) | (int(not self.degraded[1]) << 1)
                        self.send(HB1, bytes((int(self.error), health)))
                    next_hb = now + .020
                self.blink_tx(now)
                if self.agent.election_tick(now):
                    self.show_raft_state()
                event = self.agent.poll_links(self.last_rx, now)
                if event is not None:
                    self.agent.link_event = event
                self.agent.client_tick(now)
                # This node is the cockpit, so while it leads the answer is
                # always yes; the other direction is recorded by whoever
                # replaces it.
                self.agent.report_cockpit(True)
                self.agent.ref_tx(now)
                self.agent.append_tx(now)
                if now >= next_drift:
                    self.drift_report()
                    next_drift = now + 1.0
        except KeyboardInterrupt:
            pass
        finally:
            self.pps.close()
            self.pins.close()
            self.selector.close()
            for sock in [*self.can, self.udp]:
                sock.close()
            if self.args.csv:
                with open(self.args.csv, "w", newline="") as output:
                    writer = csv.writer(output)
                    writer.writerow(("udp_dequeue_monotonic_ns", "first_can_write_elapsed_us",
                                     "first_can_write_succeeded"))
                    writer.writerows(self.samples)
            timings = sorted(row[1] for row in self.samples if row[2] > 0)
            if timings:
                print(f"UDP dequeue -> first CAN write: n={len(timings)} max={max(timings):.1f} us "
                      f"p99={timings[min(len(timings)-1, int(len(timings)*.99))]:.1f} us")
            edges, mean_us, worst_us, realtime = self.pps.stats()
            if edges:
                print(f"PPS edges={edges} mean error={mean_us:+.1f} us "
                      f"worst={worst_us:+d} us sched_fifo={realtime}")
                if not realtime:
                    print("PPS ran at normal priority; run as root for SCHED_FIFO "
                          "and a smaller spread.")
            print(self.agent.summary())
            print(f"CAN send errors A/B={self.send_errors}; malformed={self.bad_frames}; "
                  f"sample overflow={self.sample_overflow}; dummy force={self.force}; relayed A/B={self.forwarded}")
            print("These are observed elapsed times, not hard WCET or wire completion bounds.")


def main():
    """Parse command-line options and run the real SocketCAN cockpit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="cockpit", help="this node's ID in cluster.conf")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--no-gpio", action="store_true", help="functional check only; no scope markers")
    parser.add_argument("--no-pps", action="store_true",
                        help="leave the PPS pin alone; the thread asks for SCHED_FIFO")
    parser.add_argument("--inject-invalid", action="store_true", help="next CTRL throttle=101, valid CRC")
    parser.add_argument("--force-port", type=int, help="optional one-byte dummy FORCE reply UDP port")
    args = parser.parse_args()
    if args.duration < 0:
        parser.error("duration must be nonnegative")
    Cockpit(args).run()


if __name__ == "__main__":
    main()
