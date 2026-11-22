"""Exercise the election state machine and the vote wire format.

The firmware's copy lives in src/raft_agent.c and cannot be run on a host, so
these tests drive pi/demo/raft_agent.py, which is written to mirror it line for
line. What is checked here is the behaviour that makes an election safe rather
than merely terminating: a node with a stale log cannot win, a vote is only
counted for the candidate it names, and a higher term always wins.

The wire format is compiled from the real C and compared byte for byte, so the
two halves of the cluster cannot disagree about what a vote looks like.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from cluster_config import load  # noqa: E402
from network_state import event_node  # noqa: E402
from protocol import REQ_VOTE_BASE, VOTE_BASE, lab5_id_for  # noqa: E402
from raft_agent import (CANDIDATE, FOLLOWER, LEADER, RaftAgent,  # noqa: E402
                        vote_decode, vote_encode, vote_request_decode,
                        vote_request_encode)

HARNESS = r"""
#include "lab_protocol.h"
#include <stdio.h>
#include <stdlib.h>

static void dump(const uint8_t d[8])
{
	for (int i = 0; i < 8; i++) {
		printf("%02x", d[i]);
	}
	printf("\n");
}

int main(int argc, char **argv)
{
	uint8_t data[8];

	if (argc == 5 && argv[1][0] == 'q') {
		struct lab5_vote_request request = {
			.term = (uint8_t)strtoul(argv[2], NULL, 0),
			.last_index = (uint16_t)strtoul(argv[3], NULL, 0),
			.last_term = (uint8_t)strtoul(argv[4], NULL, 0),
		};
		struct lab5_vote_request back;

		lab5_vote_request_encode(data, &request);
		dump(data);
		back = lab5_vote_request_decode(data);
		printf("%u %u %u\n", back.term, back.last_index, back.last_term);
		return 0;
	}
	if (argc == 5 && argv[1][0] == 'v') {
		struct lab5_vote vote = {
			.term = (uint8_t)strtoul(argv[2], NULL, 0),
			.granted = (uint8_t)strtoul(argv[3], NULL, 0),
			.candidate = (uint8_t)strtoul(argv[4], NULL, 0),
		};
		struct lab5_vote back;

		lab5_vote_encode(data, &vote);
		dump(data);
		back = lab5_vote_decode(data);
		printf("%u %u %u\n", back.term, back.granted, back.candidate);
		return 0;
	}
	return 1;
}
"""


class Harness:
    """A RaftAgent with its transmissions captured."""

    def __init__(self, node_id, config, leader=None):
        """Build an agent, optionally overriding the configured leader.

        :param node_id: Numeric ID this agent runs as.
        :param config: Mapping from cluster_config.load().
        :param leader: Node ID to start as leader, or None for the config's.
        """
        self.sent = []
        if leader is not None:
            config = dict(config, default_leader_id=leader)
        self.agent = RaftAgent(config, node_id, lambda i, d: self.sent.append((i, d)))

    def last(self):
        """Return the most recent frame sent.

        :return: (can_id, payload).
        """
        return self.sent[-1]


class VoteWireTests(unittest.TestCase):
    """The two implementations must agree on the bytes."""

    @classmethod
    def setUpClass(cls):
        """Build the C codecs natively."""
        if shutil.which("gcc") is None:
            raise unittest.SkipTest("needs gcc to build the C reference")
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name)
        (work / "cluster_config.h").write_text(
            "#define LAB5_CFG_N 3\n#define LAB5_CFG_NODE_COUNT 3\n"
            '#define LAB5_CFG_NODE_NAMES { "cockpit", "steering", "drivetrain" }\n')
        source = work / "harness.c"
        source.write_text(HARNESS)
        cls.binary = work / "harness"
        subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-std=c11",
                        "-I", str(ROOT / "include"), "-I", str(work),
                        "-o", str(cls.binary), str(source),
                        str(ROOT / "src" / "lab_protocol.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        """Drop the build directory."""
        cls.tmp.cleanup()

    def c_run(self, *args):
        """Run the C harness.

        :param args: Arguments after the program name.
        :return: (encoded bytes, decoded tuple).
        """
        result = subprocess.run([str(self.binary), *[str(a) for a in args]],
                                capture_output=True, text=True, check=True)
        encoded, decoded = result.stdout.splitlines()[:2]
        return bytes.fromhex(encoded), tuple(int(v) for v in decoded.split())

    def test_vote_request_matches_the_firmware(self):
        """A mismatch would make a candidate's log look wrong to a voter."""
        for term, index, last in ((0, 0, 0), (1, 9, 1), (255, 65535, 255), (7, 300, 3)):
            with self.subTest(term=term, index=index):
                encoded, decoded = self.c_run("q", term, index, last)
                self.assertEqual(encoded, vote_request_encode(term, index, last))
                self.assertEqual(decoded, (term, index, last))
                self.assertEqual(vote_request_decode(encoded), (term, index, last))

    def test_vote_matches_the_firmware(self):
        """The candidate field especially: a vote for the wrong node must not count."""
        for term, granted, candidate in ((1, 1, 2), (3, 0, 1), (255, 1, 3)):
            with self.subTest(term=term, granted=granted):
                encoded, decoded = self.c_run("v", term, granted, candidate)
                self.assertEqual(encoded, vote_encode(term, bool(granted), candidate))
                self.assertEqual(decoded, (term, granted, candidate))

    def test_payloads_are_eight_bytes(self):
        """A CAN frame is eight bytes and the dispatcher rejects anything else."""
        self.assertEqual(len(vote_request_encode(1, 2, 3)), 8)
        self.assertEqual(len(vote_encode(1, True, 2)), 8)


class ElectionStateTests(unittest.TestCase):
    """What makes an election safe, not merely terminating."""

    def setUp(self):
        """Load the real cluster configuration."""
        self.config = load(ROOT / "config" / "cluster.conf")

    def agent(self, node_id, leader=None):
        """Build a captured agent.

        :param node_id: Numeric ID.
        :param leader: Starting leader, or None for the configured one.
        :return: Harness.
        """
        return Harness(node_id, self.config, leader)

    def test_a_follower_stands_when_contact_stops(self):
        """The timeout fires, the term rises, and REQ_VOTE goes out once."""
        h = self.agent(2)
        self.assertFalse(h.agent.election_tick(h.agent.last_contact))
        before = h.agent.term
        self.assertTrue(h.agent.election_tick(h.agent.last_contact + 1.0))
        self.assertEqual(h.agent.role, CANDIDATE)
        self.assertEqual(h.agent.term, before + 1)
        self.assertEqual(h.last()[0], lab5_id_for(REQ_VOTE_BASE, 2))

    def test_a_leader_never_stands(self):
        """A leader has nothing to hear from, so it must not time itself out."""
        h = self.agent(1)
        self.assertEqual(h.agent.role, LEADER)
        self.assertFalse(h.agent.election_tick(h.agent.last_contact + 10.0))
        self.assertEqual(h.agent.role, LEADER)

    def test_a_candidate_votes_for_itself(self):
        """Its own vote is the first of the majority."""
        h = self.agent(2)
        h.agent.election_tick(h.agent.last_contact + 1.0)
        self.assertEqual(h.agent.votes, {2})
        self.assertEqual(h.agent.voted_for, 2)

    def test_one_more_vote_wins_in_a_cluster_of_three(self):
        """Majority is 2 of 3, so a single peer decides it."""
        h = self.agent(2)
        h.agent.election_tick(h.agent.last_contact + 1.0)
        self.assertTrue(h.agent.on_vote(3, vote_encode(h.agent.term, True, 2)))
        self.assertEqual(h.agent.role, LEADER)
        self.assertEqual(h.agent.leader, 2)

    def test_a_vote_for_another_candidate_does_not_count(self):
        """Two nodes can stand in one term; the votes must not be pooled.

        This is the same class of bug that lost a log entry in P5, where two
        followers both used request id 1 and one reply cleared the other's
        pending write.
        """
        h = self.agent(2)
        h.agent.election_tick(h.agent.last_contact + 1.0)
        self.assertFalse(h.agent.on_vote(3, vote_encode(h.agent.term, True, 3)))
        self.assertEqual(h.agent.role, CANDIDATE)

    def test_a_refused_vote_does_not_count(self):
        """Granted is a field, not an implication of the frame arriving."""
        h = self.agent(2)
        h.agent.election_tick(h.agent.last_contact + 1.0)
        self.assertFalse(h.agent.on_vote(3, vote_encode(h.agent.term, False, 2)))
        self.assertEqual(h.agent.role, CANDIDATE)

    def test_a_stale_vote_does_not_count(self):
        """A vote from a previous round must not elect a later candidate."""
        h = self.agent(2)
        h.agent.election_tick(h.agent.last_contact + 1.0)
        self.assertFalse(h.agent.on_vote(3, vote_encode(h.agent.term - 1, True, 2)))
        self.assertEqual(h.agent.role, CANDIDATE)

    def test_a_higher_term_makes_a_candidate_step_down(self):
        """Raft's one unconditional rule, on the vote path."""
        h = self.agent(2)
        h.agent.election_tick(h.agent.last_contact + 1.0)
        standing = h.agent.term
        self.assertFalse(h.agent.on_vote(3, vote_encode(standing + 5, False, 3)))
        self.assertEqual(h.agent.role, FOLLOWER)
        self.assertEqual(h.agent.term, standing + 5)
        self.assertEqual(h.agent.votes, set())
        self.assertEqual(h.agent.voted_for, 0)

    def test_a_node_votes_once_per_term(self):
        """Otherwise two candidates could each collect a majority in one term."""
        h = self.agent(2)
        h.agent.on_vote_request(1, vote_request_encode(9, 0, 0))
        self.assertEqual(h.agent.voted_for, 1)
        _, payload = h.agent.on_vote_request(3, vote_request_encode(9, 0, 0))
        self.assertFalse(vote_decode(payload)[1])

    def test_a_repeated_request_from_the_same_candidate_is_granted_again(self):
        """A lost vote must be recoverable by the candidate asking again."""
        h = self.agent(2)
        h.agent.on_vote_request(1, vote_request_encode(9, 0, 0))
        _, payload = h.agent.on_vote_request(1, vote_request_encode(9, 0, 0))
        self.assertTrue(vote_decode(payload)[1])

    def test_a_stale_log_cannot_win(self):
        """Raft 5.4.1, and the reason an election cannot lose a committed entry.

        Concretely here: an STM32 that was isolated has a short log, and
        electing it would drop link state the rest of the cluster agreed on.
        """
        h = self.agent(2)
        for _ in range(5):
            h.agent.log.append(1, event_node(3, True))
        _, payload = h.agent.on_vote_request(3, vote_request_encode(9, 2, 1))
        self.assertFalse(vote_decode(payload)[1])

    def test_an_equal_log_can_win(self):
        """At least as up to date, not strictly ahead."""
        h = self.agent(2)
        for _ in range(5):
            h.agent.log.append(1, event_node(3, True))
        _, payload = h.agent.on_vote_request(3, vote_request_encode(9, 5, 1))
        self.assertTrue(vote_decode(payload)[1])

    def test_a_higher_last_term_beats_a_longer_log(self):
        """Term dominates length, which is what makes the test a safety property."""
        h = self.agent(2)
        for _ in range(9):
            h.agent.log.append(1, event_node(3, True))
        _, payload = h.agent.on_vote_request(3, vote_request_encode(9, 2, 4))
        self.assertTrue(vote_decode(payload)[1])

    def test_granting_a_vote_holds_off_this_node_s_own_election(self):
        """Without it every voter stands a moment later and the term runs away."""
        h = self.agent(2)
        h.agent.last_contact = 0.0
        h.agent.on_vote_request(1, vote_request_encode(9, 0, 0))
        self.assertGreater(h.agent.last_contact, 0.0)

    def test_a_reference_holds_off_an_election(self):
        """A live master must not be challenged every 60 to 99 ms."""
        from raft_agent import ref_encode
        h = self.agent(2, leader=3)
        h.agent.last_contact = 0.0
        h.agent.on_ref(ref_encode(2000000000000000, 0), 0)
        self.assertGreater(h.agent.last_contact, 0.0)

    def test_each_round_draws_a_fresh_timeout(self):
        """Two nodes that collide once must not collide on every retry."""
        h = self.agent(2)
        seen = set()
        for _ in range(12):
            seen.add(h.agent.election_timeout)
            h.agent.election_tick(h.agent.last_contact + 10.0)
        self.assertGreater(len(seen), 4)

    def test_timeouts_stay_in_the_handout_range(self):
        """missed_heartbeats * heartbeat_ms + U(0, jitter), so 60 to 99 ms."""
        h = self.agent(2)
        base = self.config["missed_heartbeats"] * self.config["heartbeat_ms"]
        jitter = self.config["election_timeout_jitter_ms"]
        for _ in range(200):
            ms = h.agent.draw_timeout() * 1000
            self.assertGreaterEqual(round(ms), base)
            self.assertLess(round(ms), base + jitter)

    def test_winning_resets_replication_state(self):
        """A new leader knows nothing about what followers hold until they ack."""
        h = self.agent(2)
        for _ in range(4):
            h.agent.log.append(1, event_node(3, True))
        h.agent.election_tick(h.agent.last_contact + 1.0)
        h.agent.on_vote(3, vote_encode(h.agent.term, True, 2))
        self.assertEqual(set(h.agent.match_index.values()), {0})
        self.assertEqual(set(h.agent.next_index.values()),
                         {h.agent.log.last_index() + 1})


if __name__ == "__main__":
    unittest.main()
