"""The Lab 5 relay: one hop, explicit flag, no routing decision.

Checkoff step 5 pulls the cockpit off one bus and the drivetrain off the other,
so the two cannot hear each other at all and every frame between them has to
cross at the node that still holds both. Lab 3's relay only carried catalog
traffic, so consensus and time frames were dropped and that scenario could not
work.

What is pinned here is the identifier scheme, because it is what makes the hop
count provable: a relayed copy carries a flag, a flagged frame is never relayed
again, so exactly one hop happens and with three nodes one hop always suffices.
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
from network_state import event_link, event_node  # noqa: E402
from raft_agent import RaftAgent  # noqa: E402
from protocol import (APPEND, APPEND_ACK_BASE, CLIENT_REQ_BASE,  # noqa: E402
                      LAB5_FILTER_ID, LAB5_FILTER_MASK, LAB5_RELAY_FLAG, REF,
                      LAB5_RELAY_NODE_MASK, LAB5_RELAY_NODE_SHIFT,
                      REQ_VOTE_BASE, VOTE_BASE, is_consensus, is_relayed,
                      lab5_id_for, origin_id, relay_id, relay_node)

#: Nodes that may relay a frame.
RELAYERS = (1, 2, 3)

#: Every Lab 5 identifier a node may have to relay.
LAB5_IDS = [REF, APPEND] + [lab5_id_for(base, node)
                            for base in (APPEND_ACK_BASE, REQ_VOTE_BASE,
                                         VOTE_BASE, CLIENT_REQ_BASE)
                            for node in (1, 2, 3)]

#: The Lab 3 catalog, which the Lab 5 relay must leave alone.
LAB3_IDS = [0x010, 0x020, 0x100, 0x101, 0x102, 0x200, 0x300]

HARNESS = r"""
#include "lab_protocol.h"
#include <stdio.h>
#include <stdlib.h>

/* Prints "relayed origin is_relayed" for one identifier. */
int main(int argc, char **argv)
{
	uint32_t id;

	if (argc != 2) {
		return 1;
	}
	id = (uint32_t)strtoul(argv[1], NULL, 0);
	printf("%u %u %d\n", lab5_relay_id(id, 2), lab5_origin_id(id),
	       lab5_is_relayed(id) ? 1 : 0);
	return 0;
}
"""


class RelayIdentifierTests(unittest.TestCase):
    def test_the_flag_round_trips(self):
        """Setting then clearing the flag returns the original identifier."""
        for can_id in LAB5_IDS:
            for node in RELAYERS:
                with self.subTest(can_id=hex(can_id), node=node):
                    self.assertFalse(is_relayed(can_id))
                    self.assertTrue(is_relayed(relay_id(can_id, node)))
                    self.assertEqual(origin_id(relay_id(can_id, node)), can_id)
                    self.assertEqual(relay_node(relay_id(can_id, node)), node)

    def test_two_relayers_never_share_an_identifier(self):
        """The bug this exists for.

        CAN arbitrates on the identifier alone, so two frames sharing an ID
        and differing in the data field leave the node sending recessive while
        reading dominant with a bit error. Two nodes can hold both links at
        once and relay different frames at the same moment, so their copies
        must be distinguishable or they collide destructively.
        """
        for can_id in LAB5_IDS:
            ids = {relay_id(can_id, node) for node in RELAYERS}
            with self.subTest(can_id=hex(can_id)):
                self.assertEqual(len(ids), len(RELAYERS))

    def test_a_relayed_copy_loses_arbitration_to_an_original(self):
        """It is a second chance at something and must not delay live traffic."""
        for can_id in LAB5_IDS:
            for node in RELAYERS:
                with self.subTest(can_id=hex(can_id), node=node):
                    # Lower identifier wins on CAN, so the copy must be higher.
                    self.assertGreater(relay_id(can_id, node), can_id)

    def test_relayed_identifiers_do_not_collide_with_the_lab3_catalog(self):
        """Lab 3 tops out at 0x300, which is why 0x400 was free to take."""
        relayed = {relay_id(can_id, node)
                   for can_id in LAB5_IDS for node in RELAYERS}
        self.assertEqual(relayed & set(LAB3_IDS), set())
        for can_id in LAB3_IDS:
            self.assertFalse(is_relayed(can_id))

    def test_relayed_identifiers_stay_inside_eleven_bits(self):
        """A standard CAN identifier, so no frame silently becomes extended."""
        for can_id in LAB5_IDS:
            for node in RELAYERS:
                with self.subTest(can_id=hex(can_id), node=node):
                    self.assertLessEqual(relay_id(can_id, node), 0x7FF)

    def test_a_relayed_consensus_frame_is_not_mistaken_for_an_original(self):
        """The dispatcher strips the flag first; the raw range must not match."""
        for can_id in LAB5_IDS:
            if not is_consensus(can_id):
                continue
            for node in RELAYERS:
                with self.subTest(can_id=hex(can_id), node=node):
                    self.assertTrue(is_consensus(origin_id(
                        relay_id(can_id, node))))

    def test_the_consensus_mask_already_separates_the_two_ranges(self):
        """The relay flag sits inside the existing mask, so it needs no widening."""
        self.assertEqual(LAB5_RELAY_FLAG & LAB5_FILTER_MASK, LAB5_RELAY_FLAG)
        self.assertNotEqual(LAB5_FILTER_ID | LAB5_RELAY_FLAG, LAB5_FILTER_ID)

    def test_one_filter_covers_every_relayer(self):
        """Matching on the flag alone, so a relayer needs no filter of its own."""
        for can_id in LAB5_IDS:
            for node in RELAYERS:
                with self.subTest(can_id=hex(can_id), node=node):
                    self.assertEqual(relay_id(can_id, node) & LAB5_RELAY_FLAG,
                                     LAB5_RELAY_FLAG)


@unittest.skipIf(shutil.which("gcc") is None, "needs gcc to build the C reference")
class FirmwareAgreementTests(unittest.TestCase):
    """The two endpoints must agree, or a relayed frame is unreadable."""

    @classmethod
    def setUpClass(cls):
        """Build the identifier helpers natively."""
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

    def test_the_firmware_agrees_on_every_identifier(self):
        """A disagreement would make one endpoint relay into the other's blind spot."""
        for can_id in LAB5_IDS + [relay_id(i, 3) for i in LAB5_IDS]:
            with self.subTest(can_id=hex(can_id)):
                result = subprocess.run([str(self.binary), str(can_id)],
                                        capture_output=True, text=True, check=True)
                relayed, origin, flagged = (int(v) for v in result.stdout.split())
                self.assertEqual(relayed, relay_id(can_id, 2))
                self.assertEqual(origin, origin_id(can_id))
                self.assertEqual(bool(flagged), is_relayed(can_id))


class RelayGateTests(unittest.TestCase):
    """Relaying only when something actually needs it.

    With every node holding both links a relayed copy is provably redundant:
    the originator already transmits on both buses, so the copy reaches nobody
    new and only consumes bus and transmit queue capacity. On the bench that
    waste pushed Lab 3's CTRL out of the queues, and the node that stopped
    receiving it sat in the error state flashing hazards while consensus
    reported everything healthy.
    """

    def setUp(self):
        """Load the real cluster configuration."""
        self.config = load(ROOT / "config" / "cluster.conf")

    def agent(self):
        """Build an agent with a fully healthy committed graph.

        :return: The agent.
        """
        agent = RaftAgent(self.config, 1, lambda i, d: None)
        for node in (1, 2, 3):
            agent.log.append(1, event_node(node, True))
            for bus in (1, 2):
                agent.log.append(1, event_link(node, bus, True))
        agent.commit_index = agent.log.last_index()
        agent.net.replay(agent.log, agent.commit_index)
        return agent

    def test_a_healthy_cluster_does_not_relay(self):
        """Every frame already reaches every node on one bus or the other."""
        self.assertFalse(self.agent().relay_needed())

    def test_a_missing_link_turns_relaying_on(self):
        """Exactly the topology checkoff step 5 builds."""
        agent = self.agent()
        agent.log.append(1, event_link(3, 1, False))
        agent.commit_index = agent.log.last_index()
        agent.net.replay(agent.log, agent.commit_index)
        self.assertTrue(agent.relay_needed())

    def test_a_node_that_is_down_does_not_demand_relaying(self):
        """Its links go with it; there is nobody left to reach."""
        agent = self.agent()
        agent.log.append(1, event_node(3, False))
        agent.commit_index = agent.log.last_index()
        agent.net.replay(agent.log, agent.commit_index)
        self.assertFalse(agent.relay_needed())

    def test_an_empty_graph_does_not_demand_relaying(self):
        """At boot nothing is known, and nothing needs bridging yet."""
        agent = RaftAgent(self.config, 1, lambda i, d: None)
        self.assertFalse(agent.relay_needed())


if __name__ == "__main__":
    unittest.main()
