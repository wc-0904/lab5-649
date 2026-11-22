"""Exercise how the cluster learns that a node has gone and come back.

The handout's event table has the leader append NODE_X_DOWN when node X goes
down. A node cut off from both buses cannot report itself, so before this the
other two nodes kept showing it alive for the whole isolation, and the report it
was holding landed late on reconnection. These tests drive pi/demo/raft_agent.py,
which mirrors src/raft_agent.c.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from cluster_config import load  # noqa: E402
from network_state import event_link, event_node  # noqa: E402
from raft_agent import (ISOLATED_RESET, LINK_UP_DEBOUNCE, PEER_DOWN,  # noqa: E402
                        RaftAgent)


def agent(node_id, leader):
    """Build an agent with a given leader that records what it transmits.

    :param node_id: Numeric ID this agent runs as.
    :param leader: Node ID that leads at boot.
    :return: The agent.
    """
    config = dict(load(ROOT / "config" / "cluster.conf"), default_leader_id=leader)
    return RaftAgent(config, node_id, lambda can_id, data: None)


class LeaderRecordsSilentPeers(unittest.TestCase):
    """The leader is the only node that can say an isolated node has gone."""

    def leader_with_drivetrain_up(self):
        """A cockpit leader whose committed graph shows the drivetrain up.

        :return: (agent, a time at which every peer was last heard).
        """
        a = agent(1, 1)
        a.net.add_node(3)
        a.net.add_link(3, 1)
        t = 1000.0
        for peer in a.peers:
            a.heard_from(peer, t)
        return a, t

    def test_a_silent_peer_is_recorded_down_once(self):
        a, t = self.leader_with_drivetrain_up()
        a.heard_from(2, t + PEER_DOWN)
        self.assertIsNone(a.peer_tick(t + PEER_DOWN - 0.01))
        self.assertEqual(a.peer_tick(t + PEER_DOWN + 0.01), event_node(3, False))
        self.assertEqual(a.log.get(a.log.last_index())[1], event_node(3, False))
        before = a.log.last_index()
        for step in range(50):
            a.heard_from(2, t + 1 + step * 0.02)
            self.assertIsNone(a.peer_tick(t + 1 + step * 0.02))
        self.assertEqual(a.log.last_index(), before)

    def test_a_node_absent_from_the_graph_is_not_recorded(self):
        """Steering is silent but was never in the graph: nothing to record."""
        a, t = self.leader_with_drivetrain_up()
        a.heard_from(3, t + 5.0)
        self.assertIsNone(a.peer_tick(t + 5.0))

    def test_a_leader_that_hears_nothing_records_nobody(self):
        """The leader is the one cut off; its entries could never commit."""
        a, t = self.leader_with_drivetrain_up()
        self.assertIsNone(a.peer_tick(t + 5.0, hearing=False))

    def test_a_follower_never_records_a_peer(self):
        f = agent(2, 1)
        f.net.add_node(3)
        f.heard_from(3, 1000.0)
        self.assertIsNone(f.peer_tick(1010.0))

    def test_hearing_again_rearms_for_the_next_silence(self):
        a, t = self.leader_with_drivetrain_up()
        a.heard_from(2, t + 1.0)
        self.assertIsNotNone(a.peer_tick(t + 1.0))
        a.heard_from(3, t + 2.0)               # back
        a.net.add_node(3)                       # its NODE_UP committed
        a.heard_from(2, t + 3.0)
        self.assertEqual(a.peer_tick(t + 3.0), event_node(3, False))

    def test_a_node_that_still_acknowledges_is_not_dropped(self):
        """A latched local failure stops heartbeats, not acknowledgements."""
        a, t = self.leader_with_drivetrain_up()
        for step in range(1, 20):
            now = t + step * 0.2
            a.heard_from(2, now)
            a.heard_from(3, now)                # an ack every 200 ms
            self.assertIsNone(a.peer_tick(now))


class IsolatedNodeReReports(unittest.TestCase):
    """A cut-off node forgets what it held and reports afresh on return."""

    def test_a_held_report_is_dropped_and_nothing_is_sent_while_cut_off(self):
        f = agent(3, 1)
        f.self_events = []
        f.pending_event = event_link(3, 1, False)
        t = 1000.0
        last_rx = [t, t]
        self.assertIsNone(f.poll_links(last_rx, t + ISOLATED_RESET + 0.01))
        self.assertTrue(f.isolated)
        self.assertIsNone(f.pending_event)
        f.client_tick(t + ISOLATED_RESET + 0.01)
        self.assertIsNone(f.pending_event)

    def test_on_reconnection_it_reports_itself_then_only_the_links_it_hears(self):
        f = agent(3, 1)
        t = 1000.0
        f.poll_links([t, t], t + 1.0)               # cut off
        back = t + 2.0
        last_rx = [back, t]                         # bus A back, B still out
        self.assertIsNone(f.poll_links(last_rx, back))
        self.assertFalse(f.isolated)
        f.client_tick(back)
        self.assertEqual(f.pending_event, event_node(3, True))
        f.pending_event = None                      # leader answered
        events = []
        now = back
        while now < back + LINK_UP_DEBOUNCE + 0.1:
            now += 0.02
            last_rx[0] = now
            event = f.poll_links(last_rx, now)
            if event is not None:
                events.append(event)
        self.assertEqual(events, [event_link(3, 1, True)])


if __name__ == "__main__":
    unittest.main()
