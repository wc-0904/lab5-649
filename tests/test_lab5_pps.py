"""Check the Pi's pulse per second thread emits edges on absolute deadlines.

The STM32 edge is produced by a hardware comparator and needs no test; this one
is produced by a thread that sleeps and then writes a GPIO, so what is worth
checking is that it lands on whole multiples of its period, that it records its
own error, and that it stops cleanly. The period is shortened here so the suite
does not spend seconds waiting for real edges.
"""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from pps import Pps

#: Short enough to keep the suite quick, long enough that scheduling noise on a
#: loaded build machine does not dominate the deadline.
PERIOD_US = 50000


class FakePins:
    """Record every level change with the time it happened."""

    def __init__(self):
        self.events = []

    def set(self, pin, level):
        """Record one change.

        :param pin: Ignored.
        :param level: Requested level.
        """
        del pin
        self.events.append((time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000,
                            bool(level)))


class PpsTests(unittest.TestCase):
    def run_for(self, seconds, **kwargs):
        """Run a pulse thread briefly and return it with its pins.

        :param seconds: How long to let it run.
        :param kwargs: Passed to Pps.
        :return: (Pps, FakePins).
        """
        pins = FakePins()
        pulse = Pps(pins, 0, period_us=PERIOD_US, pulse_us=PERIOD_US // 5, **kwargs)
        pulse.start()
        time.sleep(seconds)
        pulse.close()
        return pulse, pins

    def test_disabled_emits_nothing(self):
        """The no-pps flag must leave the pin entirely alone."""
        pulse, pins = self.run_for(0.2, enabled=False)
        self.assertEqual(pins.events, [])
        self.assertEqual(pulse.stats()[0], 0)

    def test_edges_land_on_whole_periods(self):
        """Each rising edge is at a multiple of the period, not a relative delay.

        A relative sleep would accumulate the time spent in the loop body, so
        the edges would walk away from the period boundary. An absolute
        deadline cannot, which is the property being checked.
        """
        _, pins = self.run_for(0.4)
        rising = [at for at, level in pins.events if level]
        self.assertGreaterEqual(len(rising), 3)
        for at in rising:
            offset = at % PERIOD_US
            # Distance to the nearest boundary, in either direction.
            error = min(offset, PERIOD_US - offset)
            self.assertLess(error, PERIOD_US // 4)

    def test_every_rise_is_followed_by_a_fall(self):
        """The pulse must not latch high, or the next edge is invisible."""
        _, pins = self.run_for(0.4)
        # close() drops the pin unconditionally, so the last event may repeat
        # the thread's own final fall. That is deliberate and not alternation.
        levels = [level for _, level in pins.events][:-1]
        self.assertGreaterEqual(len(levels), 4)
        for first, second in zip(levels, levels[1:]):
            self.assertNotEqual(first, second)

    def test_error_is_recorded_for_every_edge(self):
        """stats() is the measurement this module exists to produce."""
        pulse, pins = self.run_for(0.4)
        count, _, worst_us, _ = pulse.stats()
        rising = [at for at, level in pins.events if level]
        self.assertEqual(count, len(rising))
        # A thread scheduled by Linux is late, never early.
        self.assertGreater(worst_us, 0)

    def test_close_is_idempotent(self):
        """Closing twice must not raise; the cockpit closes in a finally block."""
        pulse, _ = self.run_for(0.15)
        pulse.close()
        self.assertIsNone(pulse.thread)


if __name__ == "__main__":
    unittest.main()
