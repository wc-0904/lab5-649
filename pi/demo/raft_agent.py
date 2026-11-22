"""Raft role state and the leader's periodic frames, for the Pi node.

The wire formats here must stay identical to lab5_ref_encode/decode and
lab5_append_encode/decode in src/lab_protocol.c; tests/test_lab5_config.py
checks the C and Python layouts against each other.
"""
import struct
import time

from protocol import (APPEND, APPEND_ACK_BASE, CLIENT_REQ_BASE,
                      CLIENT_REDIRECT as LAB5_CLIENT_REDIRECT, REF,
                      lab5_id_for)
from network_state import (APPEND_ACCEPTED, Network, RaftLog,
                           event_link, event_name, event_node)
from time_sync import TimeSync, delay_for, local_cycles as local_cycles_now

#: Roles a node can hold.
FOLLOWER, CANDIDATE, LEADER = "FOLLOWER", "CANDIDATE", "LEADER"

#: Term every node adopts when a pre-defined leader is configured.
BOOT_TERM = 1

#: Zero point of the REF clock field: 2026-01-01T00:00:00Z in microseconds.
#: Must equal LAB5_EPOCH_US in include/lab_protocol.h.
EPOCH_US = 1767225600000000

#: Largest offset from EPOCH_US the 48 bit REF field can carry, about 8.9 years.
REF_US_MAX = (1 << 48) - 1

#: Empty AppendEntries period in seconds, per checkoff step 2.
APPEND_PERIOD = .100


def ref_encode(master_us, cycle):
    """Encode a REF payload: 48 bit microsecond clock, 16 bit cycle counter.

    :param master_us: Leader clock in microseconds; only the low 48 bits travel.
    :param cycle: Cycle counter, wrapping at 16 bits.
    :return: Eight bytes.
    """
    # Clamp rather than wrap: a clock before the epoch is a configuration error.
    clock = (0 if master_us < EPOCH_US else master_us - EPOCH_US) & REF_US_MAX
    return bytes((clock >> (8 * i)) & 0xFF for i in range(6)) + struct.pack("<H", cycle & 0xFFFF)


def ref_decode(data):
    """Decode a REF payload.

    :param data: Eight received bytes.
    :return: (master_us, cycle).
    """
    clock = sum(data[i] << (8 * i) for i in range(6)) + EPOCH_US
    return clock, struct.unpack_from("<H", data, 6)[0]


def append_encode(term, prev_index, prev_term, commit, count=0, event=0):
    """Encode an AppendEntries header; count zero is the empty heartbeat append.

    :param term: Leader term.
    :param prev_index: Index of the entry preceding the carried one.
    :param prev_term: Term of that preceding entry.
    :param commit: Leader commit index.
    :param count: Entries carried, 0 or 1.
    :param event: Event type of the carried entry, when count is 1.
    :return: Eight bytes.
    """
    return struct.pack("<BHBHBB", term & 0xFF, prev_index & 0xFFFF, prev_term & 0xFF,
                       commit & 0xFFFF, count & 0xFF, event & 0xFF)


#: Reply results, matching enum lab5_reply_result in include/lab_protocol.h.
REPLY_COMMITTED = 0
REPLY_REDIRECT = 1


def client_encode(event, request):
    """Encode a client request asking the leader to record one event.

    :param event: Encoded event.
    :param request: Request id the client chose.
    :return: Eight bytes.
    """
    return struct.pack("<BH5x", event & 0xFF, request & 0xFFFF)


def client_decode(data):
    """Decode a client request.

    :param data: Eight received bytes.
    :return: Mapping of event and request.
    """
    event, request = struct.unpack("<BH5x", data)
    return {"event": event, "request": request}


def reply_encode(result, leader, request, index, node=0):
    """Encode the leader's reply, carrying either a commit or a redirect.

    The target node is not redundant: one identifier carries every reply and
    clients number their own requests independently, so two followers routinely
    have a request id 1 outstanding at once. Without it, one client's
    acknowledgement clears the other's pending write and that entry is lost.

    :param result: REPLY_COMMITTED or REPLY_REDIRECT.
    :param leader: Node believed to be leader.
    :param request: Request id being answered.
    :param index: Index the entry was given, 0 for a redirect.
    :param node: Node being answered.
    :return: Eight bytes.
    """
    return struct.pack("<BBHHBx", result & 0xFF, leader & 0xFF,
                       request & 0xFFFF, index & 0xFFFF, node & 0xFF)


def reply_decode(data):
    """Decode a leader reply.

    :param data: Eight received bytes.
    :return: Mapping of result, leader, request and index.
    """
    result, leader, request, index, node = struct.unpack("<BBHHBx", data)
    return {"result": result, "leader": leader, "request": request,
            "index": index, "node": node}


def ack_encode(term, match_index, result):
    """Encode an AppendEntries acknowledgement.

    A CAN frame ack only means someone heard the frame, so step 5 of the client
    write path needs this separate frame per follower (TASKS.md D10).

    :param term: The follower's term.
    :param match_index: Highest index it now matches the leader on.
    :param result: Outcome code, 0 accepted.
    :return: Eight bytes.
    """
    return struct.pack("<BHB4x", term & 0xFF, match_index & 0xFFFF, result & 0xFF)


def ack_decode(data):
    """Decode an AppendEntries acknowledgement.

    :param data: Eight received bytes.
    :return: Mapping of term, match_index and result.
    """
    term, match_index, result = struct.unpack("<BHB4x", data)
    return {"term": term, "match_index": match_index, "result": result}


def append_decode(data):
    """Decode an AppendEntries header.

    :param data: Eight received bytes.
    :return: Mapping of the header fields.
    """
    term, prev_index, prev_term, commit, count, event = struct.unpack("<BHBHBB", data)
    return {"term": term, "prev_index": prev_index, "prev_term": prev_term,
            "commit": commit, "count": count, "event": event}


class Periodic:
    """Absolute-schedule trigger that skips missed periods instead of bursting.

    Deadlines are computed as epoch + n * period rather than accumulated, so
    floating point error cannot drift the series. When a tick arrives late the
    schedule jumps forward to the next future deadline: for a time reference a
    skipped REF is better than two REFs a few milliseconds apart, because the
    follower's drift ratio is computed from the interval between consecutive
    receipts.
    """

    def __init__(self, period):
        """Start an idle trigger.

        :param period: Period in seconds.
        """
        self.period = period
        self.epoch = None
        self.count = 0
        self.skipped = 0

    def deadline(self):
        """Report the next scheduled time.

        :return: Monotonic deadline, or None before the first tick.
        """
        return None if self.epoch is None else self.epoch + self.count * self.period

    def due(self, now):
        """Test whether this period has elapsed, advancing the schedule if so.

        :param now: Monotonic time from the caller's loop.
        :return: True when the caller should act.
        """
        if self.epoch is None:
            self.epoch = now
        if now < self.deadline():
            return False
        self.count += 1
        while self.deadline() <= now:
            self.count += 1
            self.skipped += 1
        return True


class RaftAgent:
    """Role state plus the leader's REF and empty APPEND transmitters.

    Deliberately non-blocking: ref_tx and append_tx are deadline checks driven
    from the cockpit's existing event loop, so nothing here can delay the CAN
    receive path. See TASKS.md D29.
    """

    def __init__(self, config, node_id, send):
        """Adopt the configured boot role.

        :param config: Mapping from cluster_config.load().
        :param node_id: Numeric ID of this node.
        :param send: Callable taking (can_id, eight bytes) to transmit.
        """
        self.config = config
        self.node_id = node_id
        self.send = send
        self.leader = config["default_leader_id"]
        # A pre-defined leader means term 1 and no election at boot, per Part 2.
        self.term = BOOT_TERM if self.leader else 0
        self.role = LEADER if self.leader == node_id else FOLLOWER
        self.cycle = 0
        self.refs_sent = 0
        self.appends_sent = 0
        self.commit_index = 0
        self.log = RaftLog()
        self.net = Network()
        self.peers = [n for n in range(1, config["N"] + 1) if n != node_id]
        # Raft's nextIndex and matchIndex, one per follower.
        self.next_index = {peer: 1 for peer in self.peers}
        self.match_index = {peer: 0 for peer in self.peers}
        self.acks = 0
        self.committed = 0
        # Requests already answered, so a relayed duplicate does not append twice.
        self.client_seen = {}
        self.heartbeat = config["heartbeat_ms"] / 1000
        self.ref_timer = Periodic(self.heartbeat)
        self.append_timer = Periodic(APPEND_PERIOD)
        self.catchup_timer = Periodic(self.heartbeat)
        # Global timeline. A leader is its own master, so this stays idle until
        # a remote REF arrives; it is what keeps the timeline continuous across
        # the handover P9 adds.
        self.sync = TimeSync(hz=1000000)
        self.master_offset_us = 0
        self.refs_received = 0

    def is_leader(self):
        """Report whether this node currently leads.

        :return: True when this node is the leader.
        """
        return self.role == LEADER

    def master_us(self):
        """Read the master clock this node stamps into REF.

        CLOCK_REALTIME plus any offset carried over from a period when this
        node was following someone else. The offset is zero on a cold boot with
        a pre-defined leader, and non-zero only after this node has taken the
        master role over from another, which is what keeps the global timeline
        from stepping at the handover.
        :return: Master clock in microseconds.
        """
        return time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000 + self.master_offset_us

    def on_ref(self, data, local_cycles=None):
        """Apply a reference message from a remote time master.

        Only meaningful while following: a leader hears its own REF back on the
        other bus and must not synchronize to itself.
        :param data: Eight received bytes.
        :param local_cycles: Local counter at receipt, defaulting to now.
        :return: True when the reference was applied.
        """
        if self.is_leader():
            return False
        master_us, cycle = ref_decode(data)
        # The delay belongs to the master's transmit path, so it follows the
        # master rather than being fixed for the cluster.
        self.sync.delay_us = delay_for(self.leader)
        self.sync.on_ref(master_us, local_cycles if local_cycles is not None
                         else local_cycles_now())
        self.cycle = cycle
        self.refs_received += 1
        return True

    def global_us(self, local_cycles=None):
        """Read the global time this node believes in.

        :param local_cycles: Local counter to evaluate at, defaulting to now.
        :return: Global microseconds.
        """
        if self.is_leader() or not self.sync.have_last:
            return self.master_us()
        return self.sync.global_us(local_cycles if local_cycles is not None
                                   else local_cycles_now())

    def adopt_master_role(self):
        """Carry the current global estimate into this node's own clock.

        Called when this node becomes the time master. Freezing the difference
        between the estimate it was already tracking and its own clock means
        the REF it starts sending continues the same timeline rather than
        jumping to its local realtime clock.
        """
        if not self.sync.have_last:
            return
        estimate = self.sync.global_us(local_cycles_now())
        own = time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000
        self.master_offset_us = estimate - own

    def ref_tx(self, now):
        """Send REF at the configured heartbeat period when leading.

        :param now: Monotonic time from the caller's loop.
        """
        if not self.is_leader() or not self.ref_timer.due(now):
            return
        self.send(REF, ref_encode(self.master_us(), self.cycle))
        self.cycle = (self.cycle + 1) & 0xFFFF
        self.refs_sent += 1

    def append_tx(self, now):
        """Send AppendEntries: every 100 ms when idle, faster while replicating.

        One identifier is broadcast to every follower, so the header is built
        from the **furthest behind** follower's nextIndex. A follower that
        already holds that entry accepts it idempotently, which is why
        raft_log_accept() had to be written that way: on this bus each append
        arrives two to four times anyway, once per bus plus relayed copies.

        :param now: Monotonic time from the caller's loop.
        """
        if not self.is_leader():
            return
        behind = self.log.last_index() >= min(self.next_index.values(),
                                              default=self.log.last_index() + 1)
        # Idle: the 100 ms heartbeat of checkoff step 2. Replicating: catch up
        # at the heartbeat rate instead of one entry per 100 ms.
        due = self.append_timer.due(now)
        if not due and not (behind and self.catchup_timer.due(now)):
            return
        self.send(APPEND, self.append_payload())
        self.appends_sent += 1

    def append_payload(self):
        """Build the AppendEntries header for the furthest behind follower.

        :return: Eight bytes.
        """
        nxt = min(self.next_index.values(), default=self.log.last_index() + 1)
        nxt = max(1, min(nxt, self.log.last_index() + 1))
        prev_index = nxt - 1
        prev_term = self.log.term_at(prev_index) or 0
        entry = self.log.get(nxt)
        count, event = (1, entry[1]) if entry else (0, 0)
        return append_encode(self.term, prev_index, prev_term, self.commit_index,
                             count, event)

    def client_append(self, event):
        """Steps 1 and 2 of Figure 2: the client calls the leader, which appends.

        The entry is not committed here. Commit happens between steps 5 and 6,
        once a majority has stored it, which on_ack() detects.

        :param event: Encoded event to record.
        :return: Index assigned, or None when this node is not the leader and
            the caller must be redirected.
        """
        if not self.is_leader():
            return None
        index = self.log.append(self.term, event)
        if self.config["Debug"]:
            print("raft: append idx=%d term=%d %s"
                  % (index, self.term, event_name(event)), flush=True)
        return index

    def on_client_request(self, node, data):
        """Steps 1, 2 and 6 of Figure 2, or the redirect when not leading.

        A client that reaches a follower is told who the leader is rather than
        being ignored, so the write path fails closed. Duplicate requests, which
        this bus produces by relaying, are answered from the recorded index
        instead of appending a second entry: checkoff step 4 wants exactly one
        entry per change.

        :param node: Node ID that asked.
        :param data: Eight received bytes.
        :return: (can_id, payload) to send back, or None when nothing to say.
        """
        request = client_decode(data)
        key = (node, request["request"])
        if not self.is_leader():
            if not self.leader:
                return None
            return (LAB5_CLIENT_REDIRECT,
                    reply_encode(REPLY_REDIRECT, self.leader, request["request"], 0, node))
        if key in self.client_seen:
            index = self.client_seen[key]
        else:
            index = self.client_append(request["event"])
            if index is None:
                return None
            self.client_seen[key] = index
        return (LAB5_CLIENT_REDIRECT,
                reply_encode(REPLY_COMMITTED, self.node_id, request["request"], index,
                             node))

    def routing_table(self):
        """Distil the committed graph into the routes this node needs.

        :return: Mapping of destination node to (bus, relay, reachable).
        """
        return self.net.routing_table(self.node_id)

    def on_ack(self, node, data):
        """Step 5 of Figure 2: a follower reports what it has stored.

        :param node: Node ID the acknowledgement came from.
        :param data: Eight received bytes.
        :return: True when the commit index advanced.
        """
        if not self.is_leader() or node not in self.next_index:
            return False
        ack = ack_decode(data)
        self.acks += 1
        if ack["result"] == 0:
            self.match_index[node] = max(self.match_index[node], ack["match_index"])
            self.next_index[node] = self.match_index[node] + 1
        else:
            # Gap or conflict: back up one and try again on the next append.
            self.next_index[node] = max(1, self.next_index[node] - 1)
        return self.advance_commit()

    def advance_commit(self):
        """Commit the highest index a majority has stored, in the current term.

        Raft only commits an entry from the leader's own term this way, which
        keeps a later leader from committing a predecessor's entry by counting.

        :return: True when the commit index advanced.
        """
        majority = self.config["majority"]
        before = self.commit_index
        for index in range(self.log.last_index(), self.commit_index, -1):
            if self.log.term_at(index) != self.term:
                continue
            stored = 1 + sum(1 for peer in self.peers
                             if self.match_index[peer] >= index)
            if stored >= majority:
                self.commit_index = index
                break
        if self.commit_index == before:
            return False
        self.net.replay(self.log, self.commit_index)
        self.committed = self.commit_index
        if self.config["Debug"]:
            print("raft: commit idx=%d %s" % (self.commit_index, self.net.render()),
                  flush=True)
        return True

    def network(self):
        """Return the committed network graph, as the Raft client reads it.

        :return: The Network object the state machine has built.
        """
        return self.net

    def summary(self):
        """Describe the agent state for the console.

        :return: One line of role and counters.
        """
        return ("raft: term=%d role=%s leader=%s refs=%d appends=%d skipped=%d "
                "log=%d commit=%d acks=%d | %s"
                % (self.term, self.role,
                   self.config["nodes"][self.leader - 1] if self.leader else "none",
                   self.refs_sent, self.appends_sent, self.ref_timer.skipped,
                   self.log.last_index(), self.commit_index, self.acks,
                   self.net.render()))
