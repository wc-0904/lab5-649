"""Raft log and state machine for the Pi node.

Must behave identically to src/raft_log.c and src/network_state.c: the handout
requires that replaying the same log on any node produce the same graph, and
this cluster is heterogeneous, so "any node" spans C firmware and Python.
tests/test_lab5_state.py replays random logs through both and compares.
"""

#: Nodes in the cluster.
NODES = 3

#: CAN buses each node attaches to.
BUSES = 2

#: Entries the STM32 ring holds. The Pi keeps the whole log; see TASKS.md D7.
LOG_RING = 4096

#: Event encodings, matching enum raft_event in include/raft_log.h.
EVENT_NONE = 0x00
COCKPIT_OK = 0x01
COCKPIT_ERROR = 0x02
NODE_UP_BASE = 0x10
NODE_DOWN_BASE = 0x18
LINK_UP_BASE = 0x20
LINK_DOWN_BASE = 0x28

#: Outcomes of offering an AppendEntries to a log, matching enum
#: raft_append_result in include/raft_log.h.
APPEND_ACCEPTED = "ACCEPTED"
APPEND_STALE_TERM = "STALE_TERM"
APPEND_MISSING = "MISSING"
APPEND_MISMATCH = "MISMATCH"
APPEND_FORGOTTEN = "FORGOTTEN"

#: Node names in configured order, for event names and printing.
NODE_NAMES = ("cockpit", "steering", "drivetrain")

#: Initial printed for each node when it is up.
NODE_INITIALS = "CSD"


def event_node(node, up):
    """Build an event for a node coming up or going down.

    :param node: Node ID 1..3.
    :param up: True for up, False for down.
    :return: Encoded event, or EVENT_NONE when the node is invalid.
    """
    if not 1 <= node <= NODES:
        return EVENT_NONE
    return (NODE_UP_BASE if up else NODE_DOWN_BASE) + node


def event_link(node, bus, up):
    """Build an event for a CAN link changing state.

    :param node: Node ID 1..3.
    :param bus: Bus number 1 or 2.
    :param up: True for up, False for down.
    :return: Encoded event, or EVENT_NONE when node or bus is invalid.
    """
    if not 1 <= node <= NODES or not 1 <= bus <= BUSES:
        return EVENT_NONE
    base = LINK_UP_BASE if up else LINK_DOWN_BASE
    return base + (node - 1) * BUSES + (bus - 1)


def event_name(event):
    """Name an event for the console and the Redis stream.

    :param event: Encoded event.
    :return: Name such as "CANLINK_steering_1_DOWN", or "?" when unknown.
    """
    if event == COCKPIT_OK:
        return "COCKPIT_OK"
    if event == COCKPIT_ERROR:
        return "COCKPIT_ERROR"
    for base, suffix in ((NODE_UP_BASE, "UP"), (NODE_DOWN_BASE, "DOWN")):
        if base < event <= base + NODES:
            return "NODE_%s_%s" % (NODE_NAMES[event - base - 1], suffix)
    for base, suffix in ((LINK_UP_BASE, "UP"), (LINK_DOWN_BASE, "DOWN")):
        if base <= event < base + NODES * BUSES:
            slot = event - base
            return "CANLINK_%s_%d_%s" % (NODE_NAMES[slot // BUSES], slot % BUSES + 1, suffix)
    return "?"


class RaftLog:
    """Replicated log of (term, index, event); the index is the position."""

    def __init__(self, ring=0):
        """Create an empty log.

        :param ring: Entries retained, or 0 for unbounded as on the Pi.
        """
        self.ring = ring
        self.entries = []
        self.first = 1

    def append(self, term, event):
        """Append one entry, dropping the oldest when a bounded ring is full.

        :param term: Term the entry was created in.
        :param event: Encoded event.
        :return: Index assigned; the first entry is index 1.
        """
        index = self.first + len(self.entries)
        self.entries.append((term, event))
        if self.ring and len(self.entries) > self.ring:
            self.entries.pop(0)
            self.first += 1
        return index

    def first_index(self):
        """Index of the oldest retained entry.

        :return: First index, or 0 when empty.
        """
        return self.first if self.entries else 0

    def last_index(self):
        """Index of the newest entry.

        :return: Last index, or 0 when empty.
        """
        return self.first + len(self.entries) - 1 if self.entries else 0

    def get(self, index):
        """Read one entry by index.

        :param index: Entry index.
        :return: (term, event), or None when not retained.
        """
        if not self.entries or not self.first <= index <= self.last_index():
            return None
        return self.entries[index - self.first]

    def truncate(self, from_index):
        """Drop every entry from an index onward.

        :param from_index: Index of the first entry to drop; 1 clears the log.
        """
        if not self.entries or from_index > self.last_index():
            return
        if from_index <= self.first:
            self.entries = []
            return
        del self.entries[from_index - self.first:]

    def accept(self, current_term, leader_term, prev_index, prev_term, count, event):
        """Apply the Raft AppendEntries consistency check.

        Re-sending the same entry is harmless, which matters because every frame
        goes out on both buses and may also arrive relayed.

        :param current_term: Term this node believes it is in.
        :param leader_term: Term from the AppendEntries header.
        :param prev_index: Index preceding the carried entry.
        :param prev_term: Term of that preceding entry.
        :param count: Entries carried, 0 or 1.
        :param event: Event of the carried entry, when count is 1.
        :return: (outcome, match index).
        """
        if leader_term < current_term:
            return APPEND_STALE_TERM, 0
        if prev_index > self.last_index():
            return APPEND_MISSING, self.last_index()
        if prev_index != 0 and self.entries and prev_index < self.first:
            return APPEND_FORGOTTEN, 0
        have = self.term_at(prev_index)
        if have is None:
            return APPEND_FORGOTTEN, 0
        if have != prev_term:
            self.truncate(prev_index)
            return APPEND_MISMATCH, self.last_index()
        if count:
            nxt = prev_index + 1
            existing = self.get(nxt)
            if existing is not None:
                if existing == (leader_term, event):
                    return APPEND_ACCEPTED, nxt
                self.truncate(nxt)
            self.append(leader_term, event)
        return APPEND_ACCEPTED, prev_index + (1 if count else 0)

    def term_at(self, index):
        """Term of a given index, for the AppendEntries consistency check.

        :param index: Entry index; zero means the empty log before any entry.
        :return: Term, or None when unknown.
        """
        if index == 0:
            return 0
        entry = self.get(index)
        return None if entry is None else entry[0]


class Network:
    """The network graph the state machine builds, plus the failure mode.

    Two bitmasks (TASKS.md D9): bit n-1 of nodes is node n, bit (n-1)*2 + (b-1)
    of links is node n on bus b. A link bit only means anything while its node
    bit is set, since drop_node removes a node's links with it.
    """

    def __init__(self):
        """Start from the empty graph, which an empty log replays to."""
        self.reset()

    def reset(self):
        """Clear to the empty graph."""
        self.nodes = 0
        self.links = 0
        self.failure_mode = False

    @staticmethod
    def _node_bit(node):
        """Bit for a node.

        :param node: Node ID 1..3.
        :return: Mask bit, or 0 when invalid.
        """
        return 1 << (node - 1) if 1 <= node <= NODES else 0

    @staticmethod
    def _link_bit(node, bus):
        """Bit for one node's link to one bus.

        :param node: Node ID 1..3.
        :param bus: Bus number 1 or 2.
        :return: Mask bit, or 0 when invalid.
        """
        if not 1 <= node <= NODES or not 1 <= bus <= BUSES:
            return 0
        return 1 << ((node - 1) * BUSES + (bus - 1))

    def _node_links(self, node):
        """Every link bit belonging to one node.

        :param node: Node ID 1..3.
        :return: Mask of that node's link bits.
        """
        return self._link_bit(node, 1) | self._link_bit(node, 2)

    def add_link(self, node, bus):
        """Mark the link between a node and a bus up, adding it if absent.

        :param node: Node ID 1..3.
        :param bus: Bus number 1 or 2.
        """
        self.links |= self._link_bit(node, bus)

    def drop_link(self, node, bus):
        """Mark that link down; node and bus stay in the graph.

        :param node: Node ID 1..3.
        :param bus: Bus number 1 or 2.
        """
        self.links &= ~self._link_bit(node, bus) & 0xFF

    def add_node(self, node):
        """Add a node, or mark it alive again, with no links until add_link.

        :param node: Node ID 1..3.
        """
        self.nodes |= self._node_bit(node)
        self.links &= ~self._node_links(node) & 0xFF

    def drop_node(self, node):
        """Remove a node and every link attached to it.

        :param node: Node ID 1..3.
        """
        self.nodes &= ~self._node_bit(node) & 0xFF
        self.links &= ~self._node_links(node) & 0xFF

    def get_network(self):
        """Read the whole graph, as the Raft client does to build its routes.

        :return: (nodes mask, links mask, failure mode).
        """
        return self.nodes, self.links, self.failure_mode

    def node_up(self, node):
        """Report whether a node is up.

        :param node: Node ID 1..3.
        :return: True when alive.
        """
        bit = self._node_bit(node)
        return bool(bit and self.nodes & bit)

    def link_up(self, node, bus):
        """Report whether a node's link to a bus is up.

        :param node: Node ID 1..3.
        :param bus: Bus number 1 or 2.
        :return: True when the node is up and that link is up.
        """
        bit = self._link_bit(node, bus)
        return bool(bit and self.node_up(node) and self.links & bit)

    def apply(self, event):
        """Apply one event; the only way the graph may change during replay.

        :param event: Encoded event.
        """
        if event == COCKPIT_OK:
            self.failure_mode = False
        elif event == COCKPIT_ERROR:
            self.failure_mode = True
        elif NODE_UP_BASE < event <= NODE_UP_BASE + NODES:
            self.add_node(event - NODE_UP_BASE)
        elif NODE_DOWN_BASE < event <= NODE_DOWN_BASE + NODES:
            self.drop_node(event - NODE_DOWN_BASE)
        elif LINK_UP_BASE <= event < LINK_UP_BASE + NODES * BUSES:
            slot = event - LINK_UP_BASE
            self.add_link(slot // BUSES + 1, slot % BUSES + 1)
        elif LINK_DOWN_BASE <= event < LINK_DOWN_BASE + NODES * BUSES:
            slot = event - LINK_DOWN_BASE
            self.drop_link(slot // BUSES + 1, slot % BUSES + 1)
        # An unknown event changes nothing, so replay stays total.

    def replay(self, log, through=0):
        """Rebuild the graph from the log, up to and including one index.

        :param log: RaftLog to replay.
        :param through: Last index to apply, or 0 for the whole log.
        """
        last = log.last_index()
        if through == 0 or through > last:
            through = last
        self.reset()
        index = log.first_index()
        while index and index <= through:
            entry = log.get(index)
            if entry is not None:
                self.apply(entry[1])
            index += 1

    def render(self):
        """Render the graph as one line, matching network_print() in C.

        :return: Text such as "net nodes=CSD links=1212.. mode=OK".
        """
        nodes = "".join(NODE_INITIALS[n - 1] if self.node_up(n) else "."
                        for n in range(1, NODES + 1))
        links = "".join(str(b) if self.link_up(n, b) else "."
                        for n in range(1, NODES + 1) for b in range(1, BUSES + 1))
        return "net nodes=%s links=%s mode=%s" % (
            nodes, links, "FAIL" if self.failure_mode else "OK")

    def __eq__(self, other):
        """Compare two graphs.

        :param other: Graph to compare with.
        :return: True when both describe the same network and mode.
        """
        return (self.nodes, self.links, self.failure_mode) == (
            other.nodes, other.links, other.failure_mode)
