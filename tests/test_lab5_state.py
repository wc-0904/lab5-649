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

from network_state import (APPEND_ACCEPTED, APPEND_FORGOTTEN, APPEND_MISMATCH,
                           APPEND_MISSING, APPEND_STALE_TERM, COCKPIT_ERROR,
                           COCKPIT_OK, LOG_RING, Network, RaftLog, event_link,
                           event_name, event_node)

HARNESS = r"""
#include "network_state.h"
#include "raft_log.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct raft_log log_storage;

static const char *name(enum raft_append_result r)
{
	switch (r) {
	case RAFT_APPEND_ACCEPTED: return "ACCEPTED";
	case RAFT_APPEND_STALE_TERM: return "STALE_TERM";
	case RAFT_APPEND_MISSING: return "MISSING";
	case RAFT_APPEND_MISMATCH: return "MISMATCH";
	default: return "FORGOTTEN";
	}
}

/*
 * "replay <through>" reads "term event" lines and prints the graph.
 * "accept" reads "current leader prev_index prev_term count event" lines and
 * prints the outcome and match index of each.
 */
int main(int argc, char **argv)
{
	const char *mode = argc > 1 ? argv[1] : "replay";

	raft_log_init(&log_storage);
	if (strcmp(mode, "accept") == 0) {
		unsigned cur, lead, prev_index, prev_term, count, event;
		uint32_t match;

		while (scanf("%u %u %u %u %u %u", &cur, &lead, &prev_index,
			     &prev_term, &count, &event) == 6) {
			enum raft_append_result r = raft_log_accept(
				&log_storage, (uint8_t)cur, (uint8_t)lead, prev_index,
				(uint8_t)prev_term, (uint8_t)count, (uint8_t)event,
				&match);

			printf("%s %u\n", name(r), match);
		}
		printf("%u %u\n", raft_log_first_index(&log_storage),
		       raft_log_last_index(&log_storage));
		return 0;
	}

	unsigned through = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 0) : 0;
	unsigned term, event;

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
        result = subprocess.run([str(self.binary), "replay", str(through)], input=feed,
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

    def c_accept(self, offers):
        """Run a sequence of AppendEntries offers through the C implementation.

        :param offers: (current_term, leader_term, prev_index, prev_term,
            count, event) tuples.
        :return: (list of (outcome, match index), first index, last index).
        """
        feed = "".join("%d %d %d %d %d %d\n" % offer for offer in offers)
        result = subprocess.run([str(self.binary), "accept"], input=feed,
                                capture_output=True, text=True, check=True)
        lines = result.stdout.splitlines()
        outcomes = [(parts[0], int(parts[1]))
                    for parts in (line.split() for line in lines[:-1])]
        first, last = (int(value) for value in lines[-1].split())
        return outcomes, first, last

    def py_accept(self, offers):
        """Run the same offers through the Python implementation.

        :param offers: Same shape as c_accept.
        :return: (list of (outcome, match index), first index, last index).
        """
        log = RaftLog(LOG_RING)
        outcomes = [log.accept(*offer) for offer in offers]
        return outcomes, log.first_index(), log.last_index()

    def assert_accept_same(self, offers):
        """Assert both implementations handle the same offers identically.

        :param offers: Same shape as c_accept.
        :return: The shared result.
        """
        mine = self.py_accept(offers)
        theirs = self.c_accept(offers)
        self.assertEqual(mine, theirs)
        return mine

    def test_appending_onto_an_empty_log(self):
        up = event_node(1, True)
        outcomes, first, last = self.assert_accept_same([(1, 1, 0, 0, 1, up)])
        self.assertEqual(outcomes, [(APPEND_ACCEPTED, 1)])
        self.assertEqual((first, last), (1, 1))

    def test_an_empty_append_matches_without_growing_the_log(self):
        up = event_node(1, True)
        outcomes, _, last = self.assert_accept_same(
            [(1, 1, 0, 0, 1, up), (1, 1, 1, 1, 0, 0)])
        self.assertEqual(outcomes[1], (APPEND_ACCEPTED, 1))
        self.assertEqual(last, 1)

    def test_a_stale_leader_term_is_rejected(self):
        outcomes, _, last = self.assert_accept_same([(5, 4, 0, 0, 1, COCKPIT_OK)])
        self.assertEqual(outcomes, [(APPEND_STALE_TERM, 0)])
        self.assertEqual(last, 0)

    def test_a_gap_is_reported_with_our_last_index_so_the_leader_can_back_up(self):
        up = event_node(1, True)
        offers = [(1, 1, 0, 0, 1, up), (1, 1, 5, 1, 1, COCKPIT_OK)]
        outcomes, _, last = self.assert_accept_same(offers)
        self.assertEqual(outcomes[1], (APPEND_MISSING, 1))
        self.assertEqual(last, 1)

    def test_a_conflicting_tail_is_dropped(self):
        # Store index 1 in term 1, then a leader in term 2 disagrees about it.
        offers = [(1, 1, 0, 0, 1, event_node(1, True)),
                  (1, 1, 1, 1, 1, event_node(2, True)),
                  (2, 2, 1, 2, 1, event_node(3, True))]
        outcomes, _, last = self.assert_accept_same(offers)
        self.assertEqual(outcomes[2][0], APPEND_MISMATCH)
        # Index 1 disagreed, so everything from there is gone.
        self.assertEqual(last, 0)

    def test_resending_the_same_entry_is_idempotent(self):
        # Every frame goes out on both buses and may also arrive relayed.
        up = event_node(1, True)
        offer = (1, 1, 0, 0, 1, up)
        outcomes, _, last = self.assert_accept_same([offer, offer, offer])
        self.assertEqual(outcomes, [(APPEND_ACCEPTED, 1)] * 3)
        self.assertEqual(last, 1)

    def test_a_later_term_overwrites_the_same_index(self):
        offers = [(1, 1, 0, 0, 1, event_node(1, True)),
                  (2, 2, 0, 0, 1, event_node(2, True))]
        outcomes, _, last = self.assert_accept_same(offers)
        self.assertEqual(outcomes[1], (APPEND_ACCEPTED, 1))
        self.assertEqual(last, 1)

    def test_catch_up_replays_a_follower_from_behind(self):
        # D11: a rejoining node catches up over AppendEntries, no flash needed.
        events = [event_node(n, True) for n in (1, 2, 3)]
        offers = [(1, 1, i, 1 if i else 0, 1, event)
                  for i, event in enumerate(events)]
        outcomes, _, last = self.assert_accept_same(offers)
        self.assertEqual([o[0] for o in outcomes], [APPEND_ACCEPTED] * 3)
        self.assertEqual(last, 3)
        self.assertEqual(self.py_replay(events)[0], "net nodes=CSD links=...... mode=OK")

    def test_random_offer_sequences_behave_identically(self):
        rng = random.Random(20261121)
        pool = [event_node(n, up) for n in (1, 2, 3) for up in (True, False)]
        for trial in range(150):
            offers = []
            for _ in range(rng.randrange(1, 12)):
                offers.append((rng.randrange(0, 3), rng.randrange(0, 3),
                               rng.randrange(0, 5), rng.randrange(0, 3),
                               rng.randrange(0, 2), rng.choice(pool)))
            with self.subTest(trial=trial):
                self.assert_accept_same(offers)

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
