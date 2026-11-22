"""Raft role state and the leader's periodic frames, for the Pi node.

The wire formats here must stay identical to lab5_ref_encode/decode and
lab5_append_encode/decode in src/lab_protocol.c; tests/test_lab5_config.py
checks the C and Python layouts against each other.
"""
import struct
import time

from protocol import (APPEND, APPEND_ACK_BASE, CLIENT_REQ_BASE,
                      CLIENT_REDIRECT as LAB5_CLIENT_REDIRECT, REF,
                      REQ_VOTE_BASE, VOTE_BASE, lab5_id_for)
from election import Election
from network_state import (APPEND_ACCEPTED, APPEND_RESULT_CODES, COCKPIT_ERROR,
                           COCKPIT_OK, Network, RaftLog,
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
#: Bits of the REF payload given to the cycle counter; the top two of byte 7
#: carry the master's node ID minus one. Must match LAB5_REF_CYCLE_BITS.
REF_CYCLE_BITS = 14
#: Largest cycle value the REF payload can carry.
REF_CYCLE_MAX = (1 << REF_CYCLE_BITS) - 1

#: Empty AppendEntries period in seconds, per checkoff step 2.
APPEND_PERIOD = .100


def ref_encode(master_us, cycle, master=1):
    """Encode a REF payload: 48 bit microsecond clock, 16 bit cycle counter.

    :param master_us: Leader clock in microseconds; only the low 48 bits travel.
    :param cycle: Cycle counter, wrapping at 16 bits.
    :return: Eight bytes.
    """
    # Clamp rather than wrap: a clock before the epoch is a configuration error.
    clock = (0 if master_us < EPOCH_US else master_us - EPOCH_US) & REF_US_MAX
    packed = (cycle & REF_CYCLE_MAX) | (((master - 1) & 3) << 14)
    return bytes((clock >> (8 * i)) & 0xFF for i in range(6)) + struct.pack("<H", packed)


def ref_decode(data):
    """Decode a REF payload.

    :param data: Eight received bytes.
    :return: (master_us, cycle, master node id).
    """
    clock = sum(data[i] << (8 * i) for i in range(6)) + EPOCH_US
    packed = struct.unpack_from("<H", data, 6)[0]
    return clock, packed & REF_CYCLE_MAX, ((packed >> 14) & 3) + 1


def append_encode(term, prev_index, prev_term, commit, count=0, entry_term=0,
                  event=0):
    """Encode an AppendEntries header; count zero is the empty heartbeat append.

    :param term: Leader term.
    :param prev_index: Index of the entry preceding the carried one.
    :param prev_term: Term of that preceding entry.
    :param commit: Leader commit index.
    :param count: Entries carried, 0 or 1.
    :param entry_term: Term the carried entry was created under. Not the same
        as term once the log spans more than one; conflating them makes a
        follower rewrite history and deadlocks the consistency check.
    :param event: Event type of the carried entry, when count is 1.
    :return: Eight bytes.
    """
    index = prev_index & APPEND_INDEX_MAX
    # Bit 15 of the index field carries the count, which is 0 or 1.
    packed = index | (0x8000 if count else 0)
    return struct.pack("<BHBHBB", term & 0xFF, packed, prev_term & 0xFF,
                       commit & APPEND_INDEX_MAX, entry_term & 0xFF, event & 0xFF)


#: How long to wait before resending an unanswered client request, seconds.
#: Must match CLIENT_RETRY_MS in src/raft_agent.c.
CLIENT_RETRY = 0.200

#: Largest log index the AppendEntries header can carry. Must match
#: LAB5_APPEND_INDEX_MAX in include/lab_protocol.h.
APPEND_INDEX_MAX = 0x7FFF

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


def vote_request_encode(term, last_index, last_term):
    """Encode a vote request.

    Must match struct lab5_vote_request in include/lab_protocol.h: byte 0 the
    candidate's term, bytes 1..2 its last log index, byte 3 that entry's term.
    The identifier says who is standing, so no candidate field is carried.

    :param term: Candidate's term.
    :param last_index: Index of the last entry in the candidate's log.
    :param last_term: Term of that entry.
    :return: Eight bytes.
    """
    return struct.pack("<BHB4x", term & 0xFF, last_index & 0xFFFF, last_term & 0xFF)


def vote_request_decode(data):
    """Decode a vote request.

    :param data: Eight received bytes.
    :return: (term, last_index, last_term).
    """
    return struct.unpack("<BHB4x", bytes(data[:8]))


def vote_encode(term, granted, candidate):
    """Encode a vote.

    Must match struct lab5_vote: byte 0 the voter's term, byte 1 nonzero when
    granted, byte 2 the candidate being answered. The candidate is not
    redundant: two candidates can stand in one term and a vote meant for one
    must not count towards the other's majority.

    :param term: Voter's term.
    :param granted: True to grant.
    :param candidate: Node being answered.
    :return: Eight bytes.
    """
    return struct.pack("<BBB5x", term & 0xFF, 1 if granted else 0, candidate & 0xFF)


def vote_decode(data):
    """Decode a vote.

    :param data: Eight received bytes.
    :return: (term, granted, candidate).
    """
    term, granted, candidate = struct.unpack("<BBB5x", bytes(data[:8]))
    return term, bool(granted), candidate


def append_decode(data):
    """Decode an AppendEntries header.

    :param data: Eight received bytes.
    :return: Mapping of the header fields.
    """
    term, packed, prev_term, commit, entry_term, event = struct.unpack("<BHBHBB", data)
    return {"term": term, "prev_index": packed & APPEND_INDEX_MAX,
            "prev_term": prev_term, "commit": commit & APPEND_INDEX_MAX,
            "count": 1 if packed & 0x8000 else 0, "entry_term": entry_term,
            "event": event}


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
        # Election. Mirrors src/raft_agent.c; see TASKS.md D13 and D14.
        self.election = Election(config["election_seed"], node_id)
        self.election_base_ms = config["missed_heartbeats"] * config["heartbeat_ms"]
        self.election_timeout = self.draw_timeout()
        self.voted_for = 0
        self.votes = set()
        self.last_contact = time.monotonic()
        self.elections = 0
        # Client side: one request in flight at a time, retried until answered.
        self.pending_event = None
        self.pending_request = 0
        self.pending_sent = 0.0
        self.next_request = 0
        self.self_events = []
        self.refill_self_reports()
        # Cockpit liveness, which only the leader records.
        self.cockpit_known = False
        self.cockpit_alive = False
        # Entries already shown. Everything the log held when this node started
        # following is marked replayed; what arrives afterwards is live.
        self.displayed = 0
        self.replay_until = 0
        self.following_since = False

    def draw_timeout(self):
        """Draw an election timeout in seconds.

        A fresh draw for every round, not one reused: two nodes that collided
        once would otherwise collide again on every retry.

        :return: Timeout in seconds.
        """
        jitter = self.config["election_timeout_jitter_ms"]
        return self.election.timeout_ms(self.election_base_ms, jitter) / 1000

    def adopt_term(self, term):
        """Step down to follower in a newer term.

        Raft's one unconditional rule: any message carrying a higher term makes
        this node a follower in that term, whatever it was doing.

        :param term: The newer term.
        """
        self.term = term
        self.role = FOLLOWER
        self.leader = 0
        self.voted_for = 0
        self.votes = set()

    def log_is_current(self, last_index, last_term):
        """Report whether a candidate's log is at least as current as ours.

        The up to date test from Raft 5.4.1, and the reason an election cannot
        lose a committed entry: a candidate missing entries cannot gather a
        majority because every node holding them refuses it.

        :param last_index: Candidate's last log index.
        :param last_term: Term of that entry.
        :return: True when the candidate may be voted for.
        """
        mine_index = self.log.last_index()
        mine_term = self.log.term_at(mine_index) if mine_index else 0
        if mine_term is None:
            # Aged out of the ring: do not claim to be ahead on evidence we no
            # longer hold.
            return True
        if last_term != mine_term:
            return last_term > mine_term
        return last_index >= mine_index

    def election_tick(self, now):
        """Start an election if nothing has been heard from a leader.

        :param now: Monotonic time from the caller's loop.
        :return: True when this node started standing.
        """
        if self.role == LEADER or now - self.last_contact < self.election_timeout:
            return False
        self.term += 1
        self.role = CANDIDATE
        self.leader = 0
        self.voted_for = self.node_id
        # A candidate votes for itself, the first of the majority.
        self.votes = {self.node_id}
        self.last_contact = now
        self.election_timeout = self.draw_timeout()
        self.elections += 1
        last_index = self.log.last_index()
        last_term = self.log.term_at(last_index) or 0 if last_index else 0
        self.send(lab5_id_for(REQ_VOTE_BASE, self.node_id),
                  vote_request_encode(self.term, last_index, last_term))
        if self.config["Debug"]:
            print("raft: standing term=%d last=%d/%d next timeout=%d ms"
                  % (self.term, last_index, last_term,
                     self.election_timeout * 1000), flush=True)
        return True

    def on_vote_request(self, node, data):
        """Answer a vote request.

        :param node: Node that sent it, taken from the identifier.
        :param data: Eight received bytes.
        :return: (can_id, payload) to transmit.
        """
        term, last_index, last_term = vote_request_decode(data)
        if term > self.term:
            self.adopt_term(term)
        granted = (term >= self.term and self.log_is_current(last_index, last_term)
                   and self.voted_for in (0, node))
        if granted:
            self.voted_for = node
            # Granting restarts the timer. Without this every node that voted
            # would stand itself a moment later and the term would climb on
            # every round without anyone winning.
            self.last_contact = time.monotonic()
        if self.config["Debug"]:
            print("raft: vote %s for %s term=%d"
                  % ("granted" if granted else "refused",
                     self.config["nodes"][node - 1], term), flush=True)
        return (lab5_id_for(VOTE_BASE, self.node_id),
                vote_encode(self.term, granted, node))

    def on_vote(self, node, data):
        """Count a vote and take the leadership on a majority.

        :param node: Node that voted, taken from the identifier.
        :param data: Eight received bytes.
        :return: True when this node just became leader.
        """
        term, granted, candidate = vote_decode(data)
        if term > self.term:
            self.adopt_term(term)
            return False
        # Three tests: this term, addressed to this candidate because two can
        # stand at once, and this node still standing.
        if not (self.role == CANDIDATE and term == self.term
                and candidate == self.node_id and granted):
            return False
        self.votes.add(node)
        if len(self.votes) < self.majority():
            return False
        self.role = LEADER
        self.leader = self.node_id
        self.last_contact = time.monotonic()
        # The global estimate this node held as a follower becomes what it now
        # stamps into REF, which is why the handover does not step the timeline.
        self.adopt_master_role()
        self.next_index = {peer: self.log.last_index() + 1 for peer in self.peers}
        self.match_index = {peer: 0 for peer in self.peers}
        if self.config["Debug"]:
            print("raft: LEADER term=%d with %d of %d votes"
                  % (self.term, len(self.votes), self.config["N"]), flush=True)
        return True

    def on_append(self, data):
        """Apply an AppendEntries from a leader and acknowledge it.

        Without this the Pi cannot step down. It boots believing it leads,
        because default_leader names it, and nothing else carries a term it
        would have to respect: REF has no term field and a vote only arrives
        during an election it took part in. A cockpit restarted after an
        election therefore kept transmitting REF alongside the real leader and
        dragged every follower between two timelines. That is checkoff step 7,
        "Pi back, rejoins as a follower, LEADER stays on the STM32".

        :param data: Eight received bytes.
        :return: (can_id, payload) to transmit, or None when ignored.
        """
        header = append_decode(data)
        stepped_down = False
        if self.is_leader() and header["term"] <= self.term:
            # Our own append, heard back on the other bus or relayed. Applying
            # it would mean a leader running its own consistency check against
            # its own log, which can truncate entries it is still replicating.
            # A *higher* term is a different matter and falls through.
            return None
        if header["term"] > self.term:
            was_leader = self.is_leader()
            self.adopt_term(header["term"])
            stepped_down = was_leader
            if was_leader:
                # Whatever this node appended while it led is not safe: the new
                # leader's log wins. Report itself again through that leader.
                self.refill_self_reports()
                self.pending_event = None
                self.cockpit_known = False
                # Its own entries are gone with its leadership, so the display
                # cursor restarts and the new leader's log is the replay.
                self.displayed = 0
                self.following_since = False
        elif header["term"] == self.term and self.role == CANDIDATE:
            # Someone else won this term. Stop standing.
            self.role = FOLLOWER
            self.votes = set()
        if header["term"] >= self.term:
            # Contact from a leader at or above our term holds the election off.
            self.last_contact = time.monotonic()
            if not self.following_since:
                # First contact from a leader. Everything it has already
                # committed happened while this node was away or leading, so
                # it is replay; what comes after is live.
                self.following_since = True
                self.replay_until = header["commit"]
        result, match = self.log.accept(self.term, header["term"],
                                        header["prev_index"], header["prev_term"],
                                        header["count"], header["entry_term"],
                                        header["event"])
        if result == APPEND_ACCEPTED:
            # Commit no further than what we actually hold.
            target = min(header["commit"], self.log.last_index())
            if target > self.commit_index:
                self.commit_index = target
                self.net.replay(self.log, self.commit_index)
                self.show_new_entries()
        if stepped_down and self.config["Debug"]:
            print("raft: stepping down, term=%d leader is elsewhere" % self.term,
                  flush=True)
        return (lab5_id_for(APPEND_ACK_BASE, self.node_id),
                ack_encode(self.term, match, APPEND_RESULT_CODES[result]))

    def majority(self):
        """Report how many nodes make a majority.

        :return: N // 2 + 1.
        """
        return self.config["N"] // 2 + 1

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
        master_us, cycle, master = ref_decode(data)
        # The frame names its master, which is the only way a node learns who
        # leads when default_leader is random: REF and APPEND each use one
        # shared identifier and neither otherwise says who sent it.
        if 1 <= master <= self.config["N"]:
            self.leader = master
        # A reference is contact from a live leader, so it holds this node's
        # own election off. Without it the Pi would stand against a healthy
        # STM32 master every 60 to 99 ms.
        self.last_contact = time.monotonic()
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
        self.send(REF, ref_encode(self.master_us(), self.cycle, self.node_id))
        self.cycle = (self.cycle + 1) & REF_CYCLE_MAX
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
        # The entry's own term travels with it; it is not the leader's term.
        count, entry_term, event = (1, entry[0], entry[1]) if entry else (0, 0, 0)
        return append_encode(self.term, prev_index, prev_term, self.commit_index,
                             count, entry_term, event)

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

    def report_cockpit(self, alive):
        """Record whether the cockpit is heartbeating. Leader only.

        On this node the answer is trivially yes whenever it leads, because
        the cockpit is this node. The interesting case is the other direction:
        the cockpit cannot report its own death, so COCKPIT_ERROR waits for
        whoever wins the election. That is why checkoff step 6 allows a second
        for the failure mode and only 100 ms for the motor to brake; braking is
        local and does not wait for consensus.

        :param alive: True when the cockpit is heartbeating.
        """
        if not self.is_leader():
            return
        if self.cockpit_known and self.cockpit_alive == alive:
            return
        self.cockpit_known = True
        self.cockpit_alive = alive
        self.client_append(COCKPIT_OK if alive else COCKPIT_ERROR)

    def show_new_entries(self):
        """Print entries committed since the last call, exactly once, in order.

        Checkoff step 7 asks that events held while the cockpit was away appear
        on its display exactly once, in order, marked replayed, and then live
        events. The log already gives ordering and exactly-once for free: a
        cursor over committed indices cannot skip or repeat. The only thing
        needing a decision is where replayed stops and live begins, and the
        natural line is the commit index the leader reported in the first
        append this node accepted after it started following (TASKS.md D26).
        """
        while self.displayed < self.commit_index:
            self.displayed += 1
            entry = self.log.get(self.displayed)
            if entry is None:
                continue
            mark = " replayed" if self.displayed <= self.replay_until else ""
            if self.config["Debug"]:
                print("raft: event idx=%d term=%d %s%s"
                      % (self.displayed, entry[0], event_name(entry[1]), mark),
                      flush=True)

    def refill_self_reports(self):
        """Queue this node's own existence and links for reporting.

        Called at boot and again whenever this node stops leading. A node that
        appended its own entries while it led cannot assume they survived: a
        new leader's log wins, and anything this node wrote under an older term
        is overwritten. Re-reporting is how it gets back into the graph, and it
        is free when nothing changed because the leader answers a repeated
        request without appending a second copy.
        """
        self.self_events = [event_node(self.node_id, True)]
        self.self_events += [event_link(self.node_id, bus, True) for bus in (1, 2)]

    def client_tick(self, now):
        """Drive this node's own reports and retry an unanswered one.

        :param now: Monotonic time from the caller's loop.
        """
        if self.pending_event is not None:
            self.client_poll(now)
            return
        if self.self_events and self.client_report(self.self_events[0], now):
            self.self_events.pop(0)

    def client_report(self, event, now):
        """Record an event, through the leader when that is someone else.

        client_append() only works while this node leads, so as a follower the
        cockpit's own NODE_cockpit_UP never reached the log at all. A node has
        to be able to report itself to whoever is currently leading, which is
        steps 1 and 6 of the handout's write path from the client's side.

        :param event: Encoded event to record.
        :param now: Monotonic time from the caller's loop.
        :return: True when the event was taken, false when one is already in
            flight and the caller should try again.
        """
        if self.is_leader():
            self.client_append(event)
            return True
        if self.pending_event is not None:
            return False
        self.next_request = (self.next_request + 1) & 0xFFFF
        self.pending_event = event
        self.pending_request = self.next_request
        self.pending_sent = now
        self.send(lab5_id_for(CLIENT_REQ_BASE, self.node_id),
                  client_encode(event, self.pending_request))
        return True

    def client_poll(self, now):
        """Resend an unanswered client request.

        The leader may have changed, or the frame may have been lost on both
        buses at once. Retrying costs nothing, because the leader remembers the
        last request it answered per node and replies again rather than
        appending a second copy.

        :param now: Monotonic time from the caller's loop.
        """
        if self.pending_event is None or self.is_leader():
            return
        if now - self.pending_sent < CLIENT_RETRY:
            return
        self.pending_sent = now
        self.send(lab5_id_for(CLIENT_REQ_BASE, self.node_id),
                  client_encode(self.pending_event, self.pending_request))

    def on_reply(self, data):
        """Apply the leader's answer to one of this node's client requests.

        :param data: Eight received bytes.
        """
        reply = reply_decode(data)
        if reply["node"] != self.node_id or reply["request"] != self.pending_request:
            # Addressed to another client, or to a request already settled.
            return
        if reply["result"] == REPLY_COMMITTED:
            self.pending_event = None
        elif reply["leader"]:
            # Redirected: remember who leads and let the retry go there.
            self.leader = reply["leader"]

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
