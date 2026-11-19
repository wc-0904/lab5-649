"""Raft role state and the leader's periodic frames, for the Pi node.

The wire formats here must stay identical to lab5_ref_encode/decode and
lab5_append_encode/decode in src/lab_protocol.c; tests/test_lab5_config.py
checks the C and Python layouts against each other.
"""
import struct
import time

from protocol import REF, APPEND

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
        self.last_log_index = 0
        self.last_log_term = 0
        self.heartbeat = config["heartbeat_ms"] / 1000
        self.ref_timer = Periodic(self.heartbeat)
        self.append_timer = Periodic(APPEND_PERIOD)

    def is_leader(self):
        """Report whether this node currently leads.

        :return: True when this node is the leader.
        """
        return self.role == LEADER

    def master_us(self):
        """Read the master clock this node stamps into REF.

        CLOCK_REALTIME, so the value is comparable with the followers' system
        time once P6 sets it. Resolution is microseconds.
        :return: Master clock in microseconds.
        """
        return time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000

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
        """Send an empty AppendEntries every 100 ms when leading.

        :param now: Monotonic time from the caller's loop.
        """
        if not self.is_leader() or not self.append_timer.due(now):
            return
        self.send(APPEND, append_encode(self.term, self.last_log_index,
                                        self.last_log_term, self.commit_index))
        self.appends_sent += 1

    def summary(self):
        """Describe the agent state for the console.

        :return: One line of role and counters.
        """
        return ("raft: term=%d role=%s leader=%s refs=%d appends=%d skipped=%d"
                % (self.term, self.role,
                   self.config["nodes"][self.leader - 1] if self.leader else "none",
                   self.refs_sent, self.appends_sent, self.ref_timer.skipped))
