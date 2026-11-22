"""Regression scenarios found during the assignment/code audit."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pi' / 'demo'))
from cluster_config import load
from network_state import event_node, event_link, APPEND_ACCEPTED
from raft_agent import (RaftAgent, FOLLOWER, ack_encode, append_encode,
                        client_encode, reply_decode, ref_encode, EPOCH_US)
from pps import Pps


class ReviewRegressions(unittest.TestCase):
    def agent(self, node=1):
        return RaftAgent(dict(load(), Debug=False), node, lambda *_: None)

    def test_stale_ack_cannot_commit_current_term(self):
        agent = self.agent()
        agent.term = 2
        agent.client_append(event_node(1, True))
        agent.on_ack(2, ack_encode(1, 1, 0))
        self.assertEqual(agent.commit_index, 0)
        self.assertEqual(agent.match_index[2], 0)

    def test_ack_cannot_claim_entries_leader_does_not_hold(self):
        agent = self.agent()
        agent.client_append(event_node(1, True))
        agent.on_ack(2, ack_encode(1, 100, 0))
        self.assertEqual(agent.commit_index, 0)

    def test_short_append_does_not_commit_conflicting_follower_tail(self):
        agent = self.agent(2)
        agent.log.append(1, event_node(1, True))
        agent.log.append(1, event_node(3, True))
        agent.on_append(append_encode(2, 1, 1, 2))
        self.assertEqual(agent.commit_index, 1)
        self.assertFalse(agent.net.node_up(3))

    def test_rebooted_follower_progresses_past_first_recovered_entry(self):
        agent = self.agent()
        for node in (1, 2, 3):
            agent.client_append(event_node(node, True))
        agent.on_ack(2, ack_encode(1, 3, 0))
        agent.on_ack(2, ack_encode(1, 0, 2))
        agent.on_ack(2, ack_encode(1, 1, 0))
        self.assertEqual(agent.next_index[2], 2)

    def test_client_receives_no_success_without_a_majority(self):
        agent = self.agent()
        request = client_encode(event_link(2, 1, False), 7)
        for _ in range(5):
            self.assertIsNone(agent.on_client_request(2, request))
        self.assertEqual(agent.log.last_index(), 1)
        agent.on_ack(3, ack_encode(agent.term, 1, 0))
        self.assertEqual(reply_decode(agent.on_client_request(2, request)[1])['index'], 1)
        self.assertEqual(agent.log.last_index(), 1)

    def test_reused_request_counter_with_new_event_is_not_old_request(self):
        agent = self.agent()
        agent.on_client_request(2, client_encode(event_link(2, 1, False), 1))
        agent.on_client_request(2, client_encode(event_node(2, True), 1))
        self.assertEqual(agent.log.last_index(), 2)

    def test_old_master_reference_cannot_replace_known_leader(self):
        agent = self.agent(2)
        agent.leader = 3
        self.assertFalse(agent.on_ref(ref_encode(EPOCH_US + 1000000, 1, 1), 1))
        self.assertEqual(agent.leader, 3)
        self.assertFalse(agent.sync.have_last)

    def test_full_log_refuses_new_write_without_losing_history(self):
        agent = RaftAgent(dict(load(), Debug=False, log_size=2), 1, lambda *_: None)
        agent.client_append(event_node(1, True))
        agent.client_append(event_link(1, 1, True))
        self.assertIsNone(agent.on_client_request(2, client_encode(event_node(2, True), 1)))
        self.assertEqual(agent.log.last_index(), 2)
        self.assertEqual(agent.log.get(1), (1, event_node(1, True)))

    def test_pps_uses_supplied_global_timeline(self):
        class Pins:
            def __init__(self):
                self.edges = []
            def set(self, pin, value):
                if value:
                    self.edges.append(time.time_ns() // 1000 + 23000)
        pins = Pins()
        pps = Pps(pins, 0, period_us=100000, pulse_us=1000,
                  clock_us=lambda: time.time_ns() // 1000 + 23000)
        pps.start()
        time.sleep(.45)
        pps.close()
        self.assertGreaterEqual(len(pins.edges), 3)
        for edge in pins.edges:
            self.assertLess(min(edge % 100000, 100000 - edge % 100000), 15000)

    def test_pps_permanent_sleep_error_does_not_spin(self):
        pulse = Pps(None, 0)
        from unittest.mock import Mock
        pulse.libc = Mock()
        pulse.libc.clock_nanosleep.return_value = 22
        self.assertFalse(pulse._sleep_until(time.time_ns() // 1000 - 1))
        pulse.libc.clock_nanosleep.assert_called_once()

if __name__ == '__main__':
    unittest.main()
