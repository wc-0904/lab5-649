"""Failure mode and the held-event replay, checkoff steps 1, 6 and 7.

Two behaviours are checked here. First, that only a leader records whether the
cockpit is alive, which is what keeps checkoff step 4's rule of exactly one
entry per change: every node can see the cockpit go quiet but only one is
entitled to say so. Second, that events committed while the cockpit was away
appear on its display exactly once, in order, marked replayed, and that live
events follow unmarked.
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from cluster_config import load  # noqa: E402
from network_state import (COCKPIT_ERROR, COCKPIT_OK, event_link,  # noqa: E402
                           event_node)
from raft_agent import FOLLOWER, LEADER, RaftAgent, append_encode  # noqa: E402


class Harness:
    """A RaftAgent with its transmissions captured."""

    def __init__(self, node_id, config):
        """Build an agent.

        :param node_id: Numeric ID this agent runs as.
        :param config: Mapping from cluster_config.load().
        """
        self.sent = []
        self.agent = RaftAgent(config, node_id, lambda i, d: self.sent.append((i, d)))

    def events(self):
        """List the events in this agent's log, in order.

        :return: List of event bytes.
        """
        return [self.agent.log.get(i)[1]
                for i in range(1, self.agent.log.last_index() + 1)]


class CockpitLivenessTests(unittest.TestCase):
    def setUp(self):
        """Load the real cluster configuration."""
        self.config = load(ROOT / "config" / "cluster.conf")

    def agent(self, node_id=1):
        """Build a captured agent.

        :param node_id: Numeric ID.
        :return: Harness.
        """
        return Harness(node_id, self.config)

    def test_a_leader_records_the_cockpit_as_alive_once(self):
        """One entry per change, not one per observation."""
        h = self.agent()
        for _ in range(5):
            h.agent.report_cockpit(True)
        self.assertEqual(h.events().count(COCKPIT_OK), 1)

    def test_a_change_is_recorded_again(self):
        """Going down and coming back is two entries, in order."""
        h = self.agent()
        h.agent.report_cockpit(True)
        h.agent.report_cockpit(False)
        h.agent.report_cockpit(True)
        self.assertEqual([e for e in h.events() if e in (COCKPIT_OK, COCKPIT_ERROR)],
                         [COCKPIT_OK, COCKPIT_ERROR, COCKPIT_OK])

    def test_a_follower_records_nothing(self):
        """Every node sees the cockpit go quiet; only the leader may say so."""
        h = self.agent(2)
        self.assertEqual(h.agent.role, FOLLOWER)
        h.agent.report_cockpit(False)
        self.assertEqual(h.events(), [])

    def test_a_new_leader_asserts_the_state_itself(self):
        """It cannot trust what a predecessor may never have committed."""
        h = self.agent()
        h.agent.report_cockpit(True)
        before = len(h.events())
        h.agent.on_append(append_encode(9, 0, 0, 0))   # step down
        h.agent.role = LEADER                          # and win again
        h.agent.term = 10
        h.agent.report_cockpit(True)
        self.assertGreater(len(h.events()), before)

    def test_failure_mode_follows_the_committed_log(self):
        """The state machine owns the flag; nothing sets it directly."""
        h = self.agent()
        h.agent.report_cockpit(False)
        h.agent.commit_index = h.agent.log.last_index()
        h.agent.net.replay(h.agent.log, h.agent.commit_index)
        self.assertIn("mode=FAIL", h.agent.net.render())
        h.agent.report_cockpit(True)
        h.agent.commit_index = h.agent.log.last_index()
        h.agent.net.replay(h.agent.log, h.agent.commit_index)
        self.assertIn("mode=OK", h.agent.net.render())


class ReplayDisplayTests(unittest.TestCase):
    """Checkoff step 7: held events appear exactly once, in order, marked."""

    def setUp(self):
        """Load the real cluster configuration."""
        self.config = load(ROOT / "config" / "cluster.conf")

    def follower(self):
        """Build an agent that has already started following someone else.

        :return: (Harness, list of events the leader holds).
        """
        h = Harness(1, self.config)
        held = [event_node(2, True), event_link(2, 1, True), event_node(3, True)]
        for index, event in enumerate(held, start=1):
            # The leader replicates its backlog one entry per round.
            h.agent.on_append(append_encode(9, index - 1, 9 if index > 1 else 0,
                                            len(held), 1, 9, event))
        return h, held

    def capture(self, fn):
        """Run something and return what it printed.

        :param fn: Callable taking no arguments.
        :return: Captured stdout.
        """
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            fn()
        return buffer.getvalue()

    def test_held_events_are_marked_replayed(self):
        """Everything the leader already held when this node arrived."""
        h, held = self.follower()
        # on_append already showed them as they committed, so the cursor has
        # caught up and a further call prints nothing.
        self.assertEqual(h.agent.displayed, len(held))
        self.assertEqual(self.capture(h.agent.show_new_entries), "")

    def test_each_event_is_shown_exactly_once(self):
        """A cursor over committed indices cannot repeat or skip."""
        h, held = self.follower()
        first = self.capture(h.agent.show_new_entries)
        second = self.capture(h.agent.show_new_entries)
        self.assertEqual(second, "")
        del first

    def test_replayed_then_live(self):
        """The line between them is the commit index at first contact."""
        h = Harness(1, self.config)
        held = [event_node(2, True), event_link(2, 1, True)]
        output = ""
        for index, event in enumerate(held, start=1):
            output += self.capture(
                lambda i=index, e=event: h.agent.on_append(
                    append_encode(9, i - 1, 9 if i > 1 else 0, len(held), 1, 9, e)))
        self.assertEqual(h.agent.replay_until, len(held))
        self.assertEqual(output.count("replayed"), len(held))

        live = self.capture(lambda: h.agent.on_append(
            append_encode(9, len(held), 9, len(held) + 1, 1, 9, event_node(3, True))))
        self.assertIn("NODE_drivetrain_UP", live)
        self.assertNotIn("replayed", live)

    def test_the_cursor_restarts_when_this_node_loses_the_leadership(self):
        """Its own entries went with its leadership, so the log it joins is new."""
        h = Harness(1, self.config)
        h.agent.report_cockpit(True)
        h.agent.commit_index = h.agent.log.last_index()
        h.agent.show_new_entries()
        self.assertGreater(h.agent.displayed, 0)
        # Stepping down and first contact happen in the same frame, so that
        # append both clears the cursor and sets where replay ends.
        h.agent.on_append(append_encode(9, 0, 0, 5))
        self.assertEqual(h.agent.displayed, 0)
        self.assertEqual(h.agent.replay_until, 5)


class GraphVisibilityTests(unittest.TestCase):
    """Every node must be able to show getNetwork(), whatever its role.

    Checkoff step 4 and all three demo scenarios compare the committed graph
    across the three consoles. A following cockpit printed only its event
    stream and never its own graph, so there was nothing on it to compare.
    """

    def setUp(self):
        """Load the real cluster configuration."""
        self.config = load(ROOT / "config" / "cluster.conf")

    def capture(self, fn):
        """Run something and return what it printed.

        :param fn: Callable taking no arguments.
        :return: Captured stdout.
        """
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            fn()
        return buffer.getvalue()

    def test_a_following_node_prints_its_committed_graph(self):
        """The line a TA reads off the console."""
        h = Harness(1, self.config)
        output = self.capture(lambda: h.agent.on_append(
            append_encode(9, 0, 0, 1, 1, 9, event_node(2, True))))
        self.assertIn("commit idx=1", output)
        self.assertIn("net nodes=", output)

    def test_the_line_matches_the_firmware_format(self):
        """Compared character for character across three consoles."""
        h = Harness(1, self.config)
        h.agent.on_append(append_encode(9, 0, 0, 1, 1, 9, event_node(2, True)))
        self.assertRegex(h.agent.net.render(),
                         r"^net nodes=[CSD.]{3} links=[12.]{6} mode=(OK|FAIL)$")

    def test_the_graph_is_not_printed_when_nothing_commits(self):
        """An empty heartbeat append must not spam the console."""
        h = Harness(1, self.config)
        h.agent.on_append(append_encode(9, 0, 0, 0))
        output = self.capture(lambda: h.agent.on_append(
            append_encode(9, 0, 0, 0)))
        self.assertNotIn("commit idx=", output)


class CockpitLinkDetectionTests(unittest.TestCase):
    """The cockpit has to detect its own link failures, like any other node.

    D15 gives each node its own links to report. The cockpit had no detector at
    all: it announced both links up once at startup and never looked again, so
    pulling it off a bus produced no log entry and the graph stayed wrong. On
    the bench that showed as links=1212.2, with the drivetrain's pulled link
    recorded and the cockpit's not.
    """

    def setUp(self):
        """Load the real cluster configuration."""
        self.config = load(ROOT / "config" / "cluster.conf")

    def agent(self):
        """Build a captured cockpit agent with its startup reports drained.

        :return: Harness.
        """
        h = Harness(1, self.config)
        h.agent.self_events = []
        return h

    @staticmethod
    def seen(now, quiet_bus=None, since=0.0):
        """Build a last-seen pair with one bus optionally stale.

        Bus A has to keep receiving, or it is reported down as well and the
        test stops saying what it means.

        :param now: Current monotonic time.
        :param quiet_bus: Bus index that has gone silent, or None.
        :param since: When that bus was last heard.
        :return: Last-seen time per bus.
        """
        last = [now, now]
        if quiet_bus is not None:
            last[quiet_bus] = since
        return last

    def test_silence_on_a_bus_reports_it_down(self):
        """Something arrives every 20 ms on a healthy bus."""
        h = self.agent()
        self.assertIsNone(h.agent.poll_links(self.seen(10.0), 10.0))
        # Bus B goes quiet, then holds quiet past the debounce.
        self.assertIsNone(h.agent.poll_links(self.seen(10.5, 1, 10.0), 10.5))
        event = h.agent.poll_links(self.seen(10.6, 1, 10.0), 10.6)
        self.assertEqual(event, event_link(1, 2, False))

    def test_a_brief_gap_is_not_reported(self):
        """A log entry is permanent, so the threshold sits above the noise."""
        h = self.agent()
        self.assertIsNone(h.agent.poll_links([10.0, 9.95], 10.0))
        self.assertIsNone(h.agent.poll_links([10.01, 10.01], 10.01))

    def test_recovery_is_reported_after_a_longer_hold(self):
        """Asymmetric: coming up late is free, a false entry is not."""
        h = self.agent()
        h.agent.poll_links(self.seen(10.5, 1, 10.0), 10.5)
        h.agent.poll_links(self.seen(10.6, 1, 10.0), 10.6)
        self.assertIsNone(h.agent.poll_links(self.seen(11.0), 11.0))
        self.assertIsNone(h.agent.poll_links(self.seen(11.5), 11.5))
        self.assertEqual(h.agent.poll_links(self.seen(11.8), 11.8),
                         event_link(1, 2, True))

    def test_one_change_is_reported_once(self):
        """Checkoff step 4 wants exactly one entry per change."""
        h = self.agent()
        h.agent.poll_links(self.seen(10.5, 1, 10.0), 10.5)
        self.assertIsNotNone(h.agent.poll_links(self.seen(10.6, 1, 10.0), 10.6))
        self.assertIsNone(h.agent.poll_links(self.seen(10.7, 1, 10.0), 10.7))
        self.assertIsNone(h.agent.poll_links(self.seen(11.0, 1, 10.0), 11.0))

    def test_a_change_waits_when_a_request_is_in_flight(self):
        """A node carries one request at a time; the change must not be lost."""
        h = self.agent()
        h.agent.pending_event = COCKPIT_OK
        h.agent.poll_links(self.seen(10.5, 1, 10.0), 10.5)
        self.assertIsNone(h.agent.poll_links(self.seen(10.6, 1, 10.0), 10.6))
        h.agent.pending_event = None
        self.assertEqual(h.agent.poll_links(self.seen(10.7, 1, 10.0), 10.7),
                         event_link(1, 2, False))


if __name__ == "__main__":
    unittest.main()
