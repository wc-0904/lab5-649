"""Check the Lab 5 wire formats agree between the firmware and the Pi.

The C encoders in src/lab_protocol.c are compiled and run natively, and their
output is compared against pi/demo/raft_agent.py byte for byte. A protocol
change that updates only one endpoint fails here.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from raft_agent import (EPOCH_US, REF_CYCLE_MAX, REF_US_MAX, append_decode,
                        append_encode, ref_decode, ref_encode)

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

	if (argc == 5 && argv[1][0] == 'r') {
		lab5_ref_encode(data, strtoull(argv[2], NULL, 0),
				(uint16_t)strtoul(argv[3], NULL, 0), atoi(argv[4]));
		dump(data);
		struct lab5_ref back = lab5_ref_decode(data);
		printf("%llu %u %d\n", (unsigned long long)back.master_us, back.cycle,
		       back.master);
		return 0;
	}
	if (argc == 8 && argv[1][0] == 'a') {
		struct lab5_append h = {
			.term = (uint8_t)strtoul(argv[2], NULL, 0),
			.prev_index = (uint16_t)strtoul(argv[3], NULL, 0),
			.prev_term = (uint8_t)strtoul(argv[4], NULL, 0),
			.commit = (uint16_t)strtoul(argv[5], NULL, 0),
			.count = (uint8_t)strtoul(argv[6], NULL, 0),
			.event = (uint8_t)strtoul(argv[7], NULL, 0),
		};
		lab5_append_encode(data, &h);
		dump(data);
		struct lab5_append back = lab5_append_decode(data);
		printf("%u %u %u %u %u %u\n", back.term, back.prev_index, back.prev_term,
		       back.commit, back.count, back.event);
		return 0;
	}
	return 1;
}
"""


@unittest.skipIf(shutil.which("gcc") is None, "needs gcc to build the C reference")
class WireFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Generate the config header and build the C encoders natively."""
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name)
        header = work / "gen" / "cluster_config.h"
        subprocess.run([sys.executable, str(ROOT / "tools" / "gen_config.py"),
                        str(ROOT / "config" / "cluster.conf"), str(header)], check=True)
        source = work / "harness.c"
        source.write_text(HARNESS)
        cls.binary = work / "harness"
        subprocess.run(["gcc", "-Wall", "-Wextra", "-std=c11",
                        "-I", str(ROOT / "include"), "-I", str(header.parent),
                        "-o", str(cls.binary), str(source),
                        str(ROOT / "src" / "lab_protocol.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        """Drop the build directory."""
        cls.tmp.cleanup()

    def c_run(self, *args):
        """Run the C harness and return its two output lines.

        :param args: Arguments passed to the harness.
        :return: (encoded hex string, decoded field string).
        """
        result = subprocess.run([str(self.binary), *(str(a) for a in args)],
                                capture_output=True, text=True, check=True)
        encoded, decoded = result.stdout.split("\n")[:2]
        return encoded, decoded

    def test_ref_matches_firmware(self):
        cases = [(EPOCH_US, 0, 1), (EPOCH_US + 1, 1, 2), (EPOCH_US + 1_000_000, 7, 3),
                 (EPOCH_US + 0x1234567890AB, 0x2EEF, 2),
                 (EPOCH_US + REF_US_MAX, REF_CYCLE_MAX, 3)]
        for master_us, cycle, master in cases:
            with self.subTest(master_us=master_us, cycle=cycle, master=master):
                encoded, decoded = self.c_run("r", master_us, cycle, master)
                self.assertEqual(encoded, ref_encode(master_us, cycle, master).hex())
                self.assertEqual(decoded, "%d %d %d" % (master_us, cycle, master))
                self.assertEqual(ref_decode(ref_encode(master_us, cycle, master)),
                                 (master_us, cycle, master))

    def test_the_master_identity_survives_a_full_cycle_counter(self):
        """The two fields share byte 7, so a saturated counter must not corrupt it.

        Without masking, a cycle at the old 16 bit width would overwrite the
        identity bits and every follower would believe node 3 was master.
        """
        for master in (1, 2, 3):
            with self.subTest(master=master):
                clock, cycle, back = ref_decode(
                    ref_encode(EPOCH_US, REF_CYCLE_MAX, master))
                self.assertEqual((clock, cycle, back),
                                 (EPOCH_US, REF_CYCLE_MAX, master))

    def test_the_cycle_counter_still_outlasts_any_gap_it_detects(self):
        """14 bits wraps every 16384 heartbeats, five and a half minutes."""
        from cluster_config import load as load_config
        heartbeat_ms = load_config(ROOT / "config" / "cluster.conf")["heartbeat_ms"]
        self.assertGreater((REF_CYCLE_MAX + 1) * heartbeat_ms / 1000, 300)

    def test_a_real_wall_clock_survives_the_round_trip(self):
        # 2026-11-19 is about 1.8e15 us since the Unix epoch, which needs 51 bits.
        # Without the epoch offset this truncated to a 1973 date on the wire.
        now_us = 1795000000 * 1_000_000
        self.assertGreater(now_us, REF_US_MAX)
        encoded, decoded = self.c_run("r", now_us, 871, 1)
        self.assertEqual(encoded, ref_encode(now_us, 871, 1).hex())
        self.assertEqual(decoded, "%d %d %d" % (now_us, 871, 1))
        self.assertEqual(ref_decode(ref_encode(now_us, 871, 1)), (now_us, 871, 1))

    def test_a_clock_before_the_epoch_clamps_rather_than_wrapping(self):
        encoded, _ = self.c_run("r", EPOCH_US - 1, 5, 1)
        self.assertEqual(encoded, ref_encode(EPOCH_US - 1, 5, 1).hex())
        self.assertEqual(ref_decode(ref_encode(EPOCH_US - 1, 5, 1)), (EPOCH_US, 5, 1))

    def test_epoch_constant_matches_the_firmware(self):
        header = (ROOT / "include" / "lab_protocol.h").read_text()
        import re
        value = re.search(r"LAB5_EPOCH_US (\d+)ULL", header).group(1)
        self.assertEqual(int(value), EPOCH_US)

    def test_append_matches_firmware(self):
        cases = [(0, 0, 0, 0, 0, 0), (1, 0, 0, 0, 0, 0),
                 (1, 30000, 1, 29999, 1, 20), (255, 65535, 255, 65535, 1, 255)]
        for term, prev_index, prev_term, commit, count, event in cases:
            with self.subTest(prev_index=prev_index):
                encoded, decoded = self.c_run("a", term, prev_index, prev_term,
                                              commit, count, event)
                mine = append_encode(term, prev_index, prev_term, commit, count, event)
                self.assertEqual(encoded, mine.hex())
                self.assertEqual(decoded, "%d %d %d %d %d %d"
                                 % (term, prev_index, prev_term, commit, count, event))
                self.assertEqual(append_decode(mine)["prev_index"], prev_index)

    def test_log_size_fits_the_index_field(self):
        # 16 bits of index must cover the configured log_size of 30000.
        from cluster_config import load
        self.assertLessEqual(load(ROOT / "config" / "cluster.conf")["log_size"], 0xFFFF)

    def test_empty_append_is_all_zero_payload_in_term_zero(self):
        self.assertEqual(append_encode(0, 0, 0, 0), bytes(8))


class AgentTests(unittest.TestCase):
    def agent(self, node):
        """Build an agent for one node with a capturing send.

        :param node: Node name from cluster.conf.
        :return: (agent, list that receives (can_id, data) tuples).
        """
        from cluster_config import load, node_id
        from raft_agent import RaftAgent
        config = load(ROOT / "config" / "cluster.conf")
        sent = []
        return RaftAgent(config, node_id(config, node), lambda i, d: sent.append((i, d))), sent

    def test_predefined_leader_boots_in_term_one_without_an_election(self):
        for node, role in (("cockpit", "LEADER"), ("steering", "FOLLOWER"),
                           ("drivetrain", "FOLLOWER")):
            with self.subTest(node):
                agent, _ = self.agent(node)
                self.assertEqual(agent.role, role)
                self.assertEqual(agent.term, 1)
                self.assertEqual(agent.leader, 1)

    def test_only_the_leader_transmits(self):
        from protocol import APPEND, REF
        agent, sent = self.agent("steering")
        for tick in range(10):
            agent.ref_tx(tick * .020)
            agent.append_tx(tick * .020)
        self.assertEqual(sent, [])

        agent, sent = self.agent("cockpit")
        for tick in range(11):
            agent.ref_tx(tick * .020)
            agent.append_tx(tick * .020)
        # 200 ms of ticks: REF every 20 ms, APPEND every 100 ms.
        self.assertEqual(sum(1 for can_id, _ in sent if can_id == REF), 11)
        self.assertEqual(sum(1 for can_id, _ in sent if can_id == APPEND), 3)

    def test_ref_cycle_counter_increments_and_wraps(self):
        agent, sent = self.agent("cockpit")
        agent.cycle = REF_CYCLE_MAX - 1
        for tick in range(3):
            agent.ref_tx(tick * .020)
        cycles = [ref_decode(data)[1] for _, data in sent]
        self.assertEqual(cycles, [REF_CYCLE_MAX - 1, REF_CYCLE_MAX, 0])

    def test_schedule_is_absolute_so_a_late_tick_does_not_drift(self):
        agent, sent = self.agent("cockpit")
        agent.ref_tx(0)
        agent.ref_tx(.055)          # one very late tick
        agent.ref_tx(.060)
        self.assertEqual(len(sent), 3)
        self.assertAlmostEqual(agent.ref_timer.deadline(), .080, places=9)
        self.assertEqual(agent.ref_timer.skipped, 1)

    def test_a_majority_commits_and_a_minority_does_not(self):
        from network_state import RaftLog, event_node
        from raft_agent import ack_encode
        agent, _ = self.agent("cockpit")
        index = agent.client_append(event_node(1, True))
        self.assertEqual(index, 1)
        self.assertEqual(agent.commit_index, 0)      # step 2 only; not committed
        # One follower stores it. With N=3 a majority is 2, counting the leader.
        agent.on_ack(2, ack_encode(1, 1, 0))
        self.assertEqual(agent.commit_index, 1)      # commit, between steps 5 and 6

    def test_one_ack_from_a_three_node_cluster_is_not_a_majority_without_the_leader(self):
        from network_state import event_node
        from raft_agent import ack_encode
        agent, _ = self.agent("cockpit")
        agent.client_append(event_node(1, True))
        agent.client_append(event_node(2, True))
        # The leader holds both, one follower holds only the first.
        agent.on_ack(2, ack_encode(1, 1, 0))
        self.assertEqual(agent.commit_index, 1)
        agent.on_ack(3, ack_encode(1, 2, 0))
        self.assertEqual(agent.commit_index, 2)

    def test_a_rejection_backs_the_follower_up(self):
        from network_state import event_node
        from raft_agent import ack_encode
        agent, _ = self.agent("cockpit")
        for node in (1, 2, 3):
            agent.client_append(event_node(node, True))
        agent.next_index[2] = 4
        agent.on_ack(2, ack_encode(1, 0, 2))         # MISSING
        self.assertEqual(agent.next_index[2], 3)
        self.assertEqual(agent.commit_index, 0)

    def test_the_header_is_built_for_the_furthest_behind_follower(self):
        from network_state import event_node
        from raft_agent import append_decode
        agent, _ = self.agent("cockpit")
        for node in (1, 2, 3):
            agent.client_append(event_node(node, True))
        agent.next_index[2] = 3
        agent.next_index[3] = 1
        header = append_decode(agent.append_payload())
        # Node 3 is furthest behind, so prevLogIndex is 0 and entry 1 is carried.
        self.assertEqual(header["prev_index"], 0)
        self.assertEqual(header["count"], 1)

    def test_a_follower_catches_up_and_both_reach_the_same_graph(self):
        from network_state import (APPEND_ACCEPTED, Network, RaftLog, event_link,
                                   event_node)
        from raft_agent import append_decode
        agent, _ = self.agent("cockpit")
        for event in (event_node(1, True), event_link(1, 1, True),
                      event_link(1, 2, True), event_node(2, True)):
            agent.client_append(event)

        follower = RaftLog()
        # Replicate one entry per round, as the 100 ms append does on the wire.
        for _ in range(10):
            header = append_decode(agent.append_payload())
            outcome, match = follower.accept(
                1, header["term"], header["prev_index"], header["prev_term"],
                header["count"], header["event"])
            self.assertEqual(outcome, APPEND_ACCEPTED)
            agent.next_index[2] = match + 1
            agent.match_index[2] = match
            agent.next_index[3] = match + 1
            agent.match_index[3] = match
            agent.advance_commit()
        self.assertEqual(follower.last_index(), 4)
        self.assertEqual(agent.commit_index, 4)
        mine, theirs = Network(), Network()
        mine.replay(agent.log, agent.commit_index)
        theirs.replay(follower, agent.commit_index)
        self.assertEqual(mine.render(), theirs.render())
        self.assertEqual(mine.render(), "net nodes=CS. links=12.... mode=OK")

    def test_a_follower_redirects_a_client_to_the_leader(self):
        from network_state import event_link
        from raft_agent import LAB5_CLIENT_REDIRECT, REPLY_REDIRECT, client_encode, reply_decode
        agent, _ = self.agent("steering")
        reply = agent.on_client_request(3, client_encode(event_link(3, 1, False), 7))
        self.assertIsNotNone(reply)
        can_id, data = reply
        self.assertEqual(can_id, LAB5_CLIENT_REDIRECT)
        decoded = reply_decode(data)
        self.assertEqual(decoded["result"], REPLY_REDIRECT)
        self.assertEqual(decoded["leader"], 1)
        self.assertEqual(decoded["request"], 7)
        self.assertEqual(decoded["node"], 3)
        # A follower must not append on a client's behalf.
        self.assertEqual(agent.log.last_index(), 0)

    def test_the_leader_appends_a_client_request_and_acknowledges_it(self):
        from network_state import event_link, event_name
        from raft_agent import REPLY_COMMITTED, client_encode, reply_decode
        agent, _ = self.agent("cockpit")
        _, data = agent.on_client_request(2, client_encode(event_link(2, 1, False), 4))
        decoded = reply_decode(data)
        self.assertEqual(decoded["result"], REPLY_COMMITTED)
        self.assertEqual((decoded["request"], decoded["index"]), (4, 1))
        self.assertEqual(agent.log.last_index(), 1)
        self.assertEqual(event_name(agent.log.get(1)[1]), "CANLINK_steering_1_DOWN")

    def test_a_duplicate_client_request_does_not_append_twice(self):
        # This bus relays, so the leader sees each request two to four times.
        # Checkoff step 4 wants exactly one entry per change.
        from network_state import event_link
        from raft_agent import client_encode, reply_decode
        agent, _ = self.agent("cockpit")
        request = client_encode(event_link(2, 1, False), 4)
        first = reply_decode(agent.on_client_request(2, request)[1])
        second = reply_decode(agent.on_client_request(2, request)[1])
        self.assertEqual(first["index"], second["index"])
        self.assertEqual(agent.log.last_index(), 1)

    def test_replies_name_the_node_so_colliding_request_ids_do_not_cross(self):
        # Clients number requests independently, so two followers routinely have
        # request id 1 outstanding. Without the node, one ack clears the other.
        from network_state import event_link
        from raft_agent import client_encode, reply_decode
        agent, _ = self.agent("cockpit")
        a = reply_decode(agent.on_client_request(2, client_encode(event_link(2, 1, True), 1))[1])
        b = reply_decode(agent.on_client_request(3, client_encode(event_link(3, 1, True), 1))[1])
        self.assertEqual((a["node"], a["request"]), (2, 1))
        self.assertEqual((b["node"], b["request"]), (3, 1))
        self.assertNotEqual(a["index"], b["index"])

    def test_two_nodes_reporting_different_links_both_get_entries(self):
        from network_state import event_link
        from raft_agent import client_encode
        agent, _ = self.agent("cockpit")
        agent.on_client_request(2, client_encode(event_link(2, 1, False), 1))
        agent.on_client_request(3, client_encode(event_link(3, 2, False), 1))
        # Same request id from different nodes must not collide.
        self.assertEqual(agent.log.last_index(), 2)

    def test_client_and_reply_round_trip(self):
        from raft_agent import client_decode, client_encode, reply_decode, reply_encode
        self.assertEqual(client_decode(client_encode(0x2A, 1234)),
                         {"event": 0x2A, "request": 1234})
        self.assertEqual(reply_decode(reply_encode(1, 3, 1234, 30000, 2)),
                         {"result": 1, "leader": 3, "request": 1234,
                          "index": 30000, "node": 2})

    def test_empty_append_carries_no_entries(self):
        agent, sent = self.agent("cockpit")
        agent.append_tx(0)
        can_id, data = sent[0]
        header = append_decode(data)
        self.assertEqual(header["count"], 0)
        self.assertEqual(header["term"], 1)


if __name__ == "__main__":
    unittest.main()
