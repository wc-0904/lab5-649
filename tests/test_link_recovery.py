"""Recovery must not lose queued reports or leave a live node absent."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pi/demo'))
from cluster_config import load
from network_state import event_node, event_link
from raft_agent import RaftAgent, FOLLOWER, append_encode, ack_encode, LINK_UP_DEBOUNCE


class LinkRecoveryTests(unittest.TestCase):
    def agent(self, node=1):
        return RaftAgent(dict(load(), Debug=False), node, lambda *_: None)

    def test_second_link_change_cannot_overwrite_waiting_report(self):
        a = self.agent()
        a.self_events = []
        a.log.append(a.term, event_node(1, True))
        a.self_index = 1  # leader's previous write still awaits a majority
        a.link_down = [True, True]
        a.link_candidate = [False, False]
        a.link_changed = [0, 0]
        now = LINK_UP_DEBOUNCE + 1
        a.link_event = a.poll_links([now, now], now)
        self.assertEqual(a.link_event, event_link(1, 1, True))
        a.client_tick(now)
        self.assertIsNotNone(a.link_event)
        self.assertIsNone(a.poll_links([now, now], now + .01))
        self.assertTrue(a.link_down[1])  # B must still be pending detection
        a.on_ack(2, ack_encode(a.term, 1, 0))
        a.client_tick(now + .02)
        self.assertIsNone(a.link_event)
        a.link_event = a.poll_links([now, now], now + .03)
        self.assertEqual(a.link_event, event_link(1, 2, True))
        a.client_tick(now + .03)
        self.assertIsNotNone(a.link_event)  # waits for A to commit
        a.on_ack(2, ack_encode(a.term, 2, 0))
        a.client_tick(now + .04)
        a.on_ack(2, ack_encode(a.term, 3, 0))
        self.assertTrue(a.net.link_up(1, 1))
        self.assertTrue(a.net.link_up(1, 2))
        self.assertEqual(a.log.last_index(), 3)

    def test_committed_self_down_requeues_presence_without_local_isolation(self):
        a = self.agent()
        a.role = FOLLOWER
        a.leader = 2
        a.self_events = []
        a.pending_event = event_link(1, 2, True)
        a.link_event = event_link(1, 1, False)
        a.log.append(1, event_node(1, True))
        a.log.append(1, event_link(1, 1, True))
        a.commit_index = 2
        a.net.replay(a.log, 2)
        # Same term: no adopt-term/reset path can accidentally repair this.
        a.on_append(append_encode(1, 2, 1, 3, 1, 1, event_node(1, False)))
        self.assertFalse(a.net.node_up(1))
        self.assertEqual(a.self_events, [event_node(1, True)])
        self.assertIsNone(a.pending_event)
        self.assertIsNone(a.link_event)
        self.assertEqual(a.link_down, [True, True])
        a.client_tick(100)
        self.assertEqual(a.pending_event, event_node(1, True))
        # An unrelated commit must not cancel/restart that repair.
        request = a.pending_request
        a.on_append(append_encode(1, 3, 1, 4, 1, 1, event_node(2, True)))
        self.assertEqual(a.pending_request, request)
        self.assertEqual(a.pending_event, event_node(1, True))

    def test_down_then_up_in_one_commit_does_not_restart_presence(self):
        a = self.agent(2)
        a.self_events = []
        for event in (event_node(2, False), event_node(2, True), event_link(2, 1, True)):
            a.log.append(1, event)
        a.on_append(append_encode(1, 3, 1, 3))
        self.assertTrue(a.net.node_up(2))
        self.assertEqual(a.self_events, [])

if __name__ == '__main__':
    unittest.main()
