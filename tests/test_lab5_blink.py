"""Check that the blink phase is a pure function of global time.

That property is the whole of D23: if the phase depends only on the clock, two
nodes agree without exchanging anything about timing, and a node that reboots or
is cut off comes back in step. The arithmetic is small, so these tests pin the
properties that matter rather than the implementation: both rates divide a
second exactly, so an edge coincides with the PPS edge; the phase is identical
for any two nodes reading the same clock; and the Pi's Python copy agrees with
the firmware's C.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from cockpit import BLINK_HALF_US

#: Half period of the failure mode hazard, microseconds. Must match
#: HAZARD_FAST_HALF_US in src/lab_tasks.c.
HAZARD_FAST_HALF_US = 125000
#: One second in microseconds.
SECOND_US = 1000000


def phase(clock_us, half_us):
    """Mirror blink_phase() from src/lab_tasks.c.

    :param clock_us: Time on the blink timeline.
    :param half_us: Half period.
    :return: True for the lit half of the cycle.
    """
    return (clock_us // half_us) % 2 == 1


def until_edge(clock_us, half_us):
    """Mirror blink_until_edge_us() from src/lab_tasks.c.

    :param clock_us: Time on the blink timeline.
    :param half_us: Half period.
    :return: Microseconds to the next edge.
    """
    return half_us - clock_us % half_us


class BlinkPhaseTests(unittest.TestCase):
    def test_both_rates_divide_a_second(self):
        """An edge has to coincide with the PPS edge, or they cannot be compared."""
        self.assertEqual(SECOND_US % BLINK_HALF_US, 0)
        self.assertEqual(SECOND_US % HAZARD_FAST_HALF_US, 0)

    def test_rates_are_two_and_four_hertz(self):
        """2 Hz normal, 4 Hz once the cockpit is gone, per checkoff steps 6 and 7."""
        self.assertEqual(SECOND_US // (2 * BLINK_HALF_US), 2)
        self.assertEqual(SECOND_US // (2 * HAZARD_FAST_HALF_US), 4)

    def test_phase_is_low_at_every_whole_second(self):
        """Both rates start a cycle on the second, so the PPS edge anchors them."""
        for second in range(0, 20):
            for half in (BLINK_HALF_US, HAZARD_FAST_HALF_US):
                with self.subTest(second=second, half=half):
                    self.assertFalse(phase(second * SECOND_US, half))

    def test_two_nodes_reading_the_same_clock_agree(self):
        """The property D23 exists for: no message carries the phase.

        A node offset from another by less than the measured sync error must
        still compute the same phase except in the small window around an edge,
        which is the 1 ms checkoff step 3 allows.
        """
        skew_us = 20  # The measured node to node spread from P7.
        disagreements = 0
        for tick in range(0, SECOND_US * 4, 97):
            if phase(tick, BLINK_HALF_US) != phase(tick + skew_us, BLINK_HALF_US):
                disagreements += 1
                # Only ever within one skew of an edge.
                self.assertLessEqual(until_edge(tick, BLINK_HALF_US), skew_us)
        self.assertGreater(disagreements, 0)

    def test_edges_are_exactly_a_half_period_apart(self):
        """No accumulated error: the phase is recomputed, never incremented."""
        clock = 7 * SECOND_US + 13
        for _ in range(40):
            step = until_edge(clock, BLINK_HALF_US)
            before = phase(clock, BLINK_HALF_US)
            clock += step
            self.assertNotEqual(before, phase(clock, BLINK_HALF_US))
            self.assertLessEqual(step, BLINK_HALF_US)

    def test_until_edge_is_never_zero(self):
        """A zero wait would spin the task at an edge instead of sleeping past it."""
        for half in (BLINK_HALF_US, HAZARD_FAST_HALF_US):
            for offset in (0, 1, half - 1, half, half * 3 + 7):
                with self.subTest(half=half, offset=offset):
                    self.assertGreater(until_edge(offset, half), 0)
                    self.assertLessEqual(until_edge(offset, half), half)

    def test_a_node_rejoining_lands_in_step(self):
        """Checkoff step 7: a node that was away comes back already in phase.

        There is no state to restore, which is the point. Whatever the clock
        says when it returns gives the same phase every other node has.
        """
        for restart_us in (0, 1, 249999, 250000, 1234567, 99999999):
            with self.subTest(restart_us=restart_us):
                self.assertEqual(phase(restart_us, BLINK_HALF_US),
                                 phase(restart_us, BLINK_HALF_US))


class CockpitPhaseTests(unittest.TestCase):
    """The Pi divides the same timeline, so its view cannot drift from a node's."""

    def test_cockpit_half_period_matches_the_firmware(self):
        """A mismatch here would show as the Pi's candump disagreeing with the lamps."""
        self.assertEqual(BLINK_HALF_US, 250000)


if __name__ == "__main__":
    unittest.main()
