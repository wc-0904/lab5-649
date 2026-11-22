"""Global time from the leader's reference message, mirroring src/time_sync.c.

The Pi runs the same arithmetic as the firmware so that when an STM32 becomes
the time master the Pi lands on the identical timeline, and so that
tests/test_lab5_sync.py can hold the two implementations against each other.
Integer Q16.16 throughout, not float, because the comparison has to be exact.
"""
import time

#: Fractional bits in the drift ratio.
RATIO_SHIFT = 16
#: A ratio of exactly one, meaning the local clock matches the master.
RATIO_ONE = 1 << RATIO_SHIFT
#: Shortest span a ratio may be measured over, microseconds. The receive
#: timestamp carries tens of microseconds of jitter, which over a 20 ms
#: heartbeat swamps the tens of ppm a crystal actually drifts by; a one second
#: window divides that contribution by fifty. Must match src/time_sync.c.
RATIO_WINDOW_US = 1000000
#: Longest a window may run before it is abandoned, microseconds.
RATIO_WINDOW_MAX_US = 10000000
#: Weight of a new sample in the ratio average, as a shift.
AVERAGE_SHIFT = 2
#: Ratios further from one than this are rejected as noise.
RATIO_TOLERANCE = RATIO_ONE // 100
#: One way delay from the master stamping L to a node capturing F, indexed by
#: the master's node ID. The delay belongs to the master's transmit path and
#: not to the bus, so it changes when the leader changes; holding one value
#: across an election would step every follower's clock at the handover. Must
#: match enum lab5_delay in include/lab_protocol.h.
DELAY_COCKPIT_US = 658
DELAY_STM32_US = 400
#: Used until a master is known.
DELAY_US = DELAY_COCKPIT_US


def delay_for(node_id):
    """Pick the one way delay for a given master.

    :param node_id: Numeric ID of the node currently leading.
    :return: Delay in microseconds.
    """
    return DELAY_COCKPIT_US if node_id == 1 else DELAY_STM32_US
#: The cycle counter is 32 bits on the STM32, so the mirror wraps the same way.
CYCLE_MODULO = 1 << 32
#: Backward master jump beyond which a new master has taken over, microseconds.
#: Must match MASTER_JUMP_US in src/time_sync.c.
MASTER_JUMP_US = 1000000


class TimeSync:
    """Track the master's timeline and this node's drift against it."""

    def __init__(self, hz=1000000, delay_us=DELAY_US):
        """Start unsynchronized.

        :param hz: Ticks per second of the local counter fed to on_ref.
        :param delay_us: One way delay estimate in microseconds.
        """
        self.hz = hz
        self.delay_us = delay_us
        self.reset()

    def reset(self):
        """Forget any master and return to the unsynchronized state."""
        self.have_last = False
        self.have_anchor = False
        self.locked = False
        self.base_us = 0
        self.last_master_us = 0
        self.last_cycles = 0
        self.anchor_master_us = 0
        self.anchor_cycles = 0
        self.ratio = RATIO_ONE
        self.correction_us = 0

    def cycles_to_us(self, cycles):
        """Convert a counter difference to microseconds.

        :param cycles: Difference in ticks.
        :return: Microseconds.
        """
        return cycles * 1000000 // self.hz

    def on_ref(self, master_us, local_cycles):
        """Apply one reference message.

        :param master_us: Leader clock carried by the frame.
        :param local_cycles: Local counter captured when the frame arrived.
        """
        local_cycles %= CYCLE_MODULO
        # Both buses carry every REF, so each one is seen twice with the same
        # L_k. Keeping the first copy keeps the least delayed timestamp; the
        # second would shorten the next interval by the gap between the buses
        # and read as drift that is not there.
        if self.have_last and master_us <= self.last_master_us:
            if self.last_master_us - master_us <= MASTER_JUMP_US:
                return
            # A different master: drop the timeline, keep the ratio, which
            # describes this node's own clock and is still valid.
            self.have_last = False
            self.have_anchor = False
        if not self.have_anchor:
            self.anchor_master_us = master_us
            self.anchor_cycles = local_cycles
            self.have_anchor = True
        elif master_us - self.anchor_master_us > RATIO_WINDOW_MAX_US:
            # The counter may have wrapped inside the window; start again.
            self.anchor_master_us = master_us
            self.anchor_cycles = local_cycles
        elif master_us - self.anchor_master_us >= RATIO_WINDOW_US:
            elapsed = (local_cycles - self.anchor_cycles) % CYCLE_MODULO
            local_us = self.cycles_to_us(elapsed)
            master_delta = master_us - self.anchor_master_us
            if local_us != 0:
                sample = (master_delta << RATIO_SHIFT) // local_us
                if RATIO_ONE - RATIO_TOLERANCE < sample < RATIO_ONE + RATIO_TOLERANCE:
                    if not self.locked:
                        self.ratio = sample
                        self.locked = True
                    else:
                        self.ratio += (sample - self.ratio) >> AVERAGE_SHIFT
                    # Correction over one window, so it reads as drift per second.
                    self.correction_us = ((RATIO_WINDOW_US * (self.ratio - RATIO_ONE))
                                          >> RATIO_SHIFT)
            self.anchor_master_us = master_us
            self.anchor_cycles = local_cycles
        self.base_us = master_us + self.delay_us
        self.last_master_us = master_us
        self.last_cycles = local_cycles
        self.have_last = True

    def global_us(self, now_cycles):
        """Read the current global time.

        :param now_cycles: Local counter read at the moment of interest.
        :return: Global microseconds, or 0 when no reference has arrived.
        """
        if not self.have_last:
            return 0
        since = self.cycles_to_us((now_cycles - self.last_cycles) % CYCLE_MODULO)
        return self.base_us + ((since * self.ratio) >> RATIO_SHIFT)

    def ppm(self):
        """Report the drift as parts per million, signed.

        :return: Parts per million, negative when the local clock runs slow.
        """
        return ((self.ratio - RATIO_ONE) * 1000000) >> RATIO_SHIFT


def local_cycles():
    """Read the Pi's own monotonic counter in microseconds.

    The Pi has no 32 bit cycle counter, so the monotonic clock stands in and is
    truncated to 32 bits to keep the wrap behaviour identical to the firmware.

    :return: Counter value in microseconds, modulo 2**32.
    """
    return (time.perf_counter_ns() // 1000) % CYCLE_MODULO
