"""Check the log and state machine agree between the firmware and the Pi.

Part 4 requires that replaying the same log on any node produce the same graph.
This cluster is heterogeneous: two nodes run the C firmware and one runs Python.
These tests compile the C state machine, replay the same logs through both, and
compare the rendered graph character for character, which is also what the demo
compares across the three consoles.
"""
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from network_state import (COCKPIT_ERROR, COCKPIT_OK, LOG_RING, Network, RaftLog,
                           event_link, event_name, event_node)

HARNESS = r"""
#include "network_state.h"
#include "raft_log.h"
#include <stdio.h>
#include <stdlib.h>

static struct raft_log log_storage;

int main(int argc, char **argv)
{
	unsigned through = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 0) : 0;
	unsigned term, event;

	raft_log_init(&log_storage);
	while (scanf("%u %u", &term, &event) == 2) {
		raft_log_append(&log_storage, (uint8_t)term, (uint8_t)event);
	}

	struct network net;
	char line[NETWORK_PRINT_SIZE];

	network_replay(&net, &log_storage, through);
	network_print(&net, line, sizeof(line));
	printf("%s\n", line);
	printf("%u %u\n", raft_log_first_index(&log_storage),
	       raft_log_last_index(&log_storage));
	return 0;
}
"""


@unittest.skipIf(shutil.which("gcc") is None, "needs gcc to build the C reference")
class StateMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Build the C log and state machine natively."""
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name)
        source = work / "harness.c"
        source.write_text(HARNESS)
        cls.binary = work / "harness"
        subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-std=c11",
                        "-I", str(ROOT / "include"), "-o", str(cls.binary),
                        str(source), str(ROOT / "src" / "raft_log.c"),
                        str(ROOT / "src" / "network_state.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        """Drop the build directory."""
        cls.tmp.cleanup()

    def c_replay(self, events, through=0, term=1):
        """Replay a log through the C implementation.

        :param events: Encoded events in order.
        :param through: Last index to apply, or 0 for all.
        :param term: Term to store with every entry.
        :return: (rendered graph, first index, last index).
        """
        feed = "".join("%d %d\n" % (term, event) for event in events)
        result = subprocess.run([str(self.binary), str(through)], input=feed,
                                capture_output=True, text=True, check=True)
        rendered, indices = result.stdout.splitlines()[:2]
        first, last = (int(value) for value in indices.split())
        return rendered, first, last

    def py_replay(self, events, through=0, term=1, ring=LOG_RING):
        """Replay a log through the Python implementation.

        :param events: Encoded events in order.
        :param through: Last index to apply, or 0 for all.
        :param term: Term to store with every entry.
        :param ring: Entries retained.
        :return: (rendered graph, first index, last index).
        """
        log = RaftLog(ring)
        for event in events:
            log.append(term, event)
        net = Network()
        net.replay(log, through)
        return net.render(), log.first_index(), log.last_index()

    def assert_same(self, events, through=0):
        """Assert both implementations replay a log identically.

        :param events: Encoded events in order.
        :param through: Last index to apply, or 0 for all.
        :return: The rendered graph both produced.
        """
        mine = self.py_replay(events, through)
        theirs = self.c_replay(events, through)
        self.assertEqual(mine, theirs)
        return mine[0]

    def test_empty_log_replays_to_the_empty_graph(self):
        self.assertEqual(self.assert_same([]), "net nodes=... links=...... mode=OK")

    def test_bringing_the_whole_cluster_up(self):
        events = [event_node(n, True) for n in (1, 2, 3)]
        events += [event_link(n, b, True) for n in (1, 2, 3) for b in (1, 2)]
        self.assertEqual(self.assert_same(events),
                         "net nodes=CSD links=121212 mode=OK")

    def test_drop_link_keeps_the_node(self):
        events = [event_node(n, True) for n in (1, 2, 3)]
        events += [event_link(n, b, True) for n in (1, 2, 3) for b in (1, 2)]
        events.append(event_link(2, 2, False))
        # Part 4: the node and the bus remain in the graph.
        self.assertEqual(self.assert_same(events),
                         "net nodes=CSD links=121.12 mode=OK")

    def test_drop_node_removes_every_link_attached_to_it(self):
        events = [event_node(n, True) for n in (1, 2, 3)]
        events += [event_link(n, b, True) for n in (1, 2, 3) for b in (1, 2)]
        events.append(event_node(3, False))
        self.assertEqual(self.assert_same(events),
                         "net nodes=CS. links=1212.. mode=OK")

    def test_a_node_returns_with_no_links_until_addlink_runs(self):
        events = [event_node(n, True) for n in (1, 2, 3)]
        events += [event_link(n, b, True) for n in (1, 2, 3) for b in (1, 2)]
        events += [event_node(3, False), event_node(3, True)]
        self.assertEqual(self.assert_same(events),
                         "net nodes=CSD links=1212.. mode=OK")
        events.append(event_link(3, 1, True))
        self.assertEqual(self.assert_same(events),
                         "net nodes=CSD links=12121. mode=OK")

    def test_cockpit_events_toggle_failure_mode_without_touching_the_graph(self):
        events = [event_node(n, True) for n in (1, 2, 3)]
        self.assertEqual(self.assert_same(events + [COCKPIT_ERROR]),
                         "net nodes=CSD links=...... mode=FAIL")
        self.assertEqual(self.assert_same(events + [COCKPIT_ERROR, COCKPIT_OK]),
                         "net nodes=CSD links=...... mode=OK")

    def test_replay_through_an_index_stops_there(self):
        events = [event_node(1, True), event_node(2, True), event_node(3, True)]
        self.assertEqual(self.assert_same(events, through=1),
                         "net nodes=C.. links=...... mode=OK")
        self.assertEqual(self.assert_same(events, through=2),
                         "net nodes=CS. links=...... mode=OK")

    def test_random_logs_replay_identically_on_both_implementations(self):
        # The determinism requirement of Part 4, across a heterogeneous cluster.
        pool = [COCKPIT_OK, COCKPIT_ERROR]
        pool += [event_node(n, up) for n in (1, 2, 3) for up in (True, False)]
        pool += [event_link(n, b, up)
                 for n in (1, 2, 3) for b in (1, 2) for up in (True, False)]
        rng = random.Random(20261120)
        for trial in range(200):
            events = [rng.choice(pool) for _ in range(rng.randrange(1, 40))]
            with self.subTest(trial=trial):
                self.assert_same(events)

    def test_replay_is_a_pure_function_of_the_log(self):
        # Same log, different starting graphs, must give the same result.
        events = [event_node(1, True), event_link(1, 2, True), COCKPIT_ERROR]
        expected = self.py_replay(events)[0]
        net = Network()
        for event in (event_node(3, True), event_link(3, 1, True), COCKPIT_OK):
            net.apply(event)
        log = RaftLog(LOG_RING)
        for event in events:
            log.append(1, event)
        net.replay(log)
        self.assertEqual(net.render(), expected)

    def test_unknown_events_change_nothing(self):
        events = [event_node(1, True), 0x00, 0x7F, 0xFF, event_link(1, 1, True)]
        self.assertEqual(self.assert_same(events),
                         "net nodes=C.. links=1..... mode=OK")

    def test_ring_drops_the_oldest_entry_and_advances_the_first_index(self):
        # One more than the ring, so index 1 is gone and replay starts at 2.
        events = [event_node(1, True)] + [COCKPIT_OK] * LOG_RING
        rendered, first, last = self.c_replay(events)
        self.assertEqual((first, last), (2, LOG_RING + 1))
        self.assertEqual(self.py_replay(events), (rendered, first, last))
        # The dropped NODE_cockpit_UP is no longer reflected in the graph.
        self.assertEqual(rendered, "net nodes=... links=...... mode=OK")

    def test_event_names_match_the_handout_vocabulary(self):
        self.assertEqual(event_name(event_node(1, True)), "NODE_cockpit_UP")
        self.assertEqual(event_name(event_node(3, False)), "NODE_drivetrain_DOWN")
        self.assertEqual(event_name(event_link(2, 1, False)),
                         "CANLINK_steering_1_DOWN")
        self.assertEqual(event_name(COCKPIT_ERROR), "COCKPIT_ERROR")
        self.assertEqual(event_name(0xFF), "?")

    def test_every_event_encoding_is_distinct(self):
        events = [COCKPIT_OK, COCKPIT_ERROR]
        events += [event_node(n, up) for n in (1, 2, 3) for up in (True, False)]
        events += [event_link(n, b, up)
                   for n in (1, 2, 3) for b in (1, 2) for up in (True, False)]
        self.assertEqual(len(events), 20)
        self.assertEqual(len(set(events)), 20)
        # Twenty values is why five bits suffice in the AppendEntries payload.
        self.assertLess(max(events), 1 << 6)


if __name__ == "__main__":
    unittest.main()
