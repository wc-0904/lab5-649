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

from raft_agent import (EPOCH_US, REF_US_MAX, append_decode, append_encode,
                        ref_decode, ref_encode)

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

	if (argc == 4 && argv[1][0] == 'r') {
		lab5_ref_encode(data, strtoull(argv[2], NULL, 0),
				(uint16_t)strtoul(argv[3], NULL, 0));
		dump(data);
		struct lab5_ref back = lab5_ref_decode(data);
		printf("%llu %u\n", (unsigned long long)back.master_us, back.cycle);
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
        cases = [(EPOCH_US, 0), (EPOCH_US + 1, 1), (EPOCH_US + 1_000_000, 7),
                 (EPOCH_US + 0x1234567890AB, 0xBEEF),
                 (EPOCH_US + REF_US_MAX, 0xFFFF)]
        for master_us, cycle in cases:
            with self.subTest(master_us=master_us, cycle=cycle):
                encoded, decoded = self.c_run("r", master_us, cycle)
                self.assertEqual(encoded, ref_encode(master_us, cycle).hex())
                self.assertEqual(decoded, "%d %d" % (master_us, cycle))
                self.assertEqual(ref_decode(ref_encode(master_us, cycle)),
                                 (master_us, cycle))

    def test_a_real_wall_clock_survives_the_round_trip(self):
        # 2026-11-19 is about 1.8e15 us since the Unix epoch, which needs 51 bits.
        # Without the epoch offset this truncated to a 1973 date on the wire.
        now_us = 1795000000 * 1_000_000
        self.assertGreater(now_us, REF_US_MAX)
        encoded, decoded = self.c_run("r", now_us, 871)
        self.assertEqual(encoded, ref_encode(now_us, 871).hex())
        self.assertEqual(decoded, "%d %d" % (now_us, 871))
        self.assertEqual(ref_decode(ref_encode(now_us, 871)), (now_us, 871))

    def test_a_clock_before_the_epoch_clamps_rather_than_wrapping(self):
        encoded, _ = self.c_run("r", EPOCH_US - 1, 5)
        self.assertEqual(encoded, ref_encode(EPOCH_US - 1, 5).hex())
        self.assertEqual(ref_decode(ref_encode(EPOCH_US - 1, 5)), (EPOCH_US, 5))

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
        agent.cycle = 0xFFFE
        for tick in range(3):
            agent.ref_tx(tick * .020)
        cycles = [ref_decode(data)[1] for _, data in sent]
        self.assertEqual(cycles, [0xFFFE, 0xFFFF, 0])

    def test_schedule_is_absolute_so_a_late_tick_does_not_drift(self):
        agent, sent = self.agent("cockpit")
        agent.ref_tx(0)
        agent.ref_tx(.055)          # one very late tick
        agent.ref_tx(.060)
        self.assertEqual(len(sent), 3)
        self.assertAlmostEqual(agent.ref_timer.deadline(), .080, places=9)
        self.assertEqual(agent.ref_timer.skipped, 1)

    def test_empty_append_carries_no_entries(self):
        agent, sent = self.agent("cockpit")
        agent.append_tx(0)
        can_id, data = sent[0]
        header = append_decode(data)
        self.assertEqual(header["count"], 0)
        self.assertEqual(header["term"], 1)


if __name__ == "__main__":
    unittest.main()
