"""Check the time synchronization arithmetic agrees between firmware and Pi.

src/time_sync.c is compiled natively against a small stub of the three Zephyr
facilities it uses, fed the same reference sequences as pi/demo/time_sync.py,
and the global time, ratio and correction are compared exactly. The arithmetic
is integer Q16.16 on both sides specifically so that this comparison can be an
equality rather than a tolerance.
"""
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))

from raft_agent import RaftAgent, ref_encode
from cluster_config import load as load_cluster_config
from time_sync import DELAY_COCKPIT_US as DELAY_US, RATIO_ONE, RATIO_WINDOW_US, TimeSync

#: Tick rate the harness drives the module at. Deliberately not the 1 MHz the
#: firmware uses, so that a hard coded rate in time_sync.c would fail here.
HZ = 84000000

#: Stands in for zephyr/kernel.h. time_sync.c uses a spinlock and the fixed
#: width integer types, and nothing else.
KERNEL_STUB = r"""
#ifndef KERNEL_STUB_H_
#define KERNEL_STUB_H_
#include <stdbool.h>
#include <stdint.h>
typedef int k_spinlock_key_t;
struct k_spinlock { int unused; };
static inline k_spinlock_key_t k_spin_lock(struct k_spinlock *l) { (void)l; return 0; }
static inline void k_spin_unlock(struct k_spinlock *l, k_spinlock_key_t k)
{
	(void)l;
	(void)k;
}
#endif
"""

HARNESS = ('#define TICK_HZ %dU\n' % HZ) + r"""
#include "time_sync.h"
#include <stdio.h>
#include <stdlib.h>

/*
 * Reads lines of "master_us cycles" from standard input, and after each one
 * prints the global time evaluated at the same cycle count, then the ratio,
 * then the correction. Evaluating at F_k itself makes the expected value the
 * base alone, which keeps the comparison independent of any host timing.
 */
int main(void)
{
	char line[128];

	time_sync_set_tick_hz(TICK_HZ);
	time_sync_reset();
	while (fgets(line, sizeof(line), stdin) != NULL) {
		unsigned long long master_us;
		unsigned long cycles;

		if (sscanf(line, "%llu %lu", &master_us, &cycles) != 2) {
			return 1;
		}
		time_sync_on_ref(master_us, (uint32_t)cycles);
		printf("%llu %d %d %d\n",
		       (unsigned long long)time_sync_global_us((uint32_t)cycles),
		       (int)time_sync_ratio(), (int)time_sync_correction_us(),
		       time_sync_locked() ? 1 : 0);
	}
	return 0;
}
"""


@unittest.skipIf(shutil.which("gcc") is None, "needs gcc to build the C reference")
class TimeSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Build time_sync.c natively against the Zephyr stub."""
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name)
        (work / "zephyr").mkdir()
        (work / "zephyr" / "kernel.h").write_text(KERNEL_STUB)
        source = work / "harness.c"
        source.write_text(HARNESS)
        cls.binary = work / "harness"
        subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-std=c11",
                        "-I", str(ROOT / "include"), "-I", str(work),
                        "-o", str(cls.binary), str(source),
                        str(ROOT / "src" / "time_sync.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        """Drop the build directory."""
        cls.tmp.cleanup()

    def c_run(self, refs):
        """Feed a reference sequence to the C implementation.

        :param refs: Sequence of (master_us, cycles) pairs.
        :return: List of (global_us, ratio, correction_us, locked) per reference.
        """
        feed = "".join("%d %d\n" % pair for pair in refs)
        result = subprocess.run([str(self.binary)], input=feed, capture_output=True,
                                text=True, check=True)
        rows = []
        for line in result.stdout.splitlines():
            values = [int(value) for value in line.split()]
            rows.append((values[0], values[1], values[2], bool(values[3])))
        return rows

    def py_run(self, refs):
        """Feed the same sequence to the Pi implementation.

        :param refs: Sequence of (master_us, cycles) pairs.
        :return: List of (global_us, ratio, correction_us, locked) per reference.
        """
        sync = TimeSync(hz=HZ)
        rows = []
        for master_us, cycles in refs:
            sync.on_ref(master_us, cycles)
            rows.append((sync.global_us(cycles), sync.ratio, sync.correction_us,
                         sync.locked))
        return rows

    @staticmethod
    def sequence(count, ppm, jitter, seed, start_cycles=0):
        """Build a reference sequence for a local clock drifting by ppm.

        :param count: Number of references.
        :param ppm: Local clock error in parts per million, positive when fast.
        :param jitter: Peak timestamp jitter in local ticks.
        :param seed: Random seed.
        :param start_cycles: Initial counter value.
        :return: List of (master_us, cycles) pairs.
        """
        rng = random.Random(seed)
        period_cycles = HZ // 50
        refs = []
        master = 1000000
        cycles = start_cycles
        for index in range(count):
            refs.append((master, cycles % (1 << 32)))
            master += 20000
            cycles += period_cycles + period_cycles * ppm // 1000000
            cycles += rng.randint(-jitter, jitter) if jitter else 0
            del index
        return refs

    def test_matches_python_on_a_clean_clock(self):
        """With no drift and no jitter both sides agree and the ratio is one."""
        refs = self.sequence(40, 0, 0, seed=1)
        rows = self.c_run(refs)
        self.assertEqual(rows, self.py_run(refs))
        self.assertEqual(rows[-1][1], RATIO_ONE)
        self.assertEqual(rows[-1][2], 0)

    def test_matches_python_across_drift_and_jitter(self):
        """Random drift and jitter still produce identical integers."""
        for seed in range(12):
            ppm = random.Random(seed).randint(-200, 200)
            jitter = random.Random(seed + 100).choice((0, 5, 50, 400))
            refs = self.sequence(60, ppm, jitter, seed=seed)
            with self.subTest(ppm=ppm, jitter=jitter):
                self.assertEqual(self.c_run(refs), self.py_run(refs))

    def test_matches_python_across_a_counter_wrap(self):
        """The 32 bit counter wrap is handled identically on both sides."""
        refs = self.sequence(40, 120, 10, seed=7, start_cycles=(1 << 32) - 5 * (HZ // 50))
        self.assertEqual(self.c_run(refs), self.py_run(refs))

    def test_first_reference_does_not_lock(self):
        """One reference gives a base but no ratio, since a ratio needs two."""
        rows = self.c_run([(1000000, 0)])
        self.assertEqual(rows, [(1000000 + DELAY_US, RATIO_ONE, 0, False)])

    def test_global_time_at_receipt_is_the_delayed_master_stamp(self):
        """Evaluated at F_k, global time is exactly L_k plus the delay."""
        refs = self.sequence(10, 90, 0, seed=3)
        for row, (master_us, _) in zip(self.c_run(refs), refs):
            self.assertEqual(row[0], master_us + DELAY_US)

    def test_ratio_tracks_a_fast_local_clock(self):
        """A local clock 100 ppm fast is corrected by a ratio below one."""
        refs = self.sequence(80, 100, 0, seed=5)
        ratio = self.c_run(refs)[-1][1]
        ppm = ((ratio - RATIO_ONE) * 1000000) >> 16
        self.assertLess(ppm, -80)
        self.assertGreater(ppm, -120)

    def test_ratio_tracks_a_slow_local_clock(self):
        """A local clock 100 ppm slow is corrected by a ratio above one."""
        refs = self.sequence(80, -100, 0, seed=5)
        ratio = self.c_run(refs)[-1][1]
        ppm = ((ratio - RATIO_ONE) * 1000000) >> 16
        self.assertGreater(ppm, 80)
        self.assertLess(ppm, 120)

    def test_no_ratio_before_a_window_closes(self):
        """A ratio needs a full window, not merely two references."""
        short = self.sequence(int(RATIO_WINDOW_US / 20000) - 2, 0, 0, seed=20)
        rows = self.c_run(short)
        self.assertFalse(any(row[3] for row in rows))
        self.assertEqual(rows, self.py_run(short))

    def test_bench_jitter_does_not_swamp_the_ratio(self):
        """50 microseconds of receive jitter must not read as drift.

        This is the measurement the window exists for. On the bench the receive
        timestamp carries about 50 microseconds of jitter, which across a 20 ms
        heartbeat is 2500 ppm: forty times a real crystal error. Across a one
        second window it is fifty times smaller, and the average brings it down
        again. With no drift present at all, the reported ratio has to stay
        near unity rather than wander by thousands of ppm.
        """
        jitter = HZ // 20000  # 50 microseconds, as measured on the bench.
        refs = self.sequence(500, 0, jitter, seed=21)
        rows = self.c_run(refs)
        self.assertTrue(rows[-1][3])
        settled = [((row[1] - RATIO_ONE) * 1000000) >> 16 for row in rows[-100:]]
        self.assertLess(max(abs(ppm) for ppm in settled), 300)
        self.assertEqual(rows, self.py_run(refs))

    def test_the_second_bus_copy_does_not_shift_the_interval(self):
        """Every REF arrives twice; the duplicate must not move F_k.

        This is the bench failure that produced 7800 ppm of drift: the second
        copy carries the same L_k but a later F_k, so the next interval came
        out short by the gap between the buses. With the duplicate ignored the
        answer is identical to the single bus case.
        """
        refs = self.sequence(40, 0, 0, seed=11)
        gap = HZ // 10000  # 100 microseconds between the two buses.
        doubled = []
        for master_us, cycles in refs:
            doubled.append((master_us, cycles))
            doubled.append((master_us, (cycles + gap) % (1 << 32)))
        single = self.c_run(refs)
        both = self.c_run(doubled)
        self.assertEqual(single[-1][1], RATIO_ONE)
        self.assertEqual(both[-1][1], RATIO_ONE)
        self.assertEqual(both[-1][2], 0)
        # Every second row is the ignored duplicate, so the kept rows match.
        self.assertEqual([row for row in both[::2]], single)
        self.assertEqual(both, self.py_run(doubled))

    def test_a_new_master_well_behind_resets_the_timeline(self):
        """A master whose clock is a second behind is a handover, not a copy."""
        # A realistic master clock, so stepping back a second stays positive.
        base = 1795237500000000
        refs = [(base + master, cycles) for master, cycles in self.sequence(10, 0, 0, seed=12)]
        last_master, last_cycles = refs[-1]
        moved = refs + [(last_master - 5000000, (last_cycles + HZ // 50) % (1 << 32))]
        rows = self.c_run(moved)
        self.assertEqual(rows[-1][0], last_master - 5000000 + DELAY_US)
        self.assertEqual(rows, self.py_run(moved))

    def test_absurd_interval_is_rejected(self):
        """A sample more than one percent off is noise, not drift, so it is dropped."""
        refs = self.sequence(30, 0, 0, seed=2)
        broken = list(refs)
        master_us, cycles = broken[20]
        broken[20] = (master_us, (cycles + HZ // 10) % (1 << 32))
        rows = self.c_run(broken)
        self.assertEqual(rows[-1][1], RATIO_ONE)
        self.assertEqual(rows, self.py_run(broken))

    def test_averaging_smooths_a_single_bad_sample(self):
        """One jittered interval moves the ratio by a fraction of its own error."""
        refs = self.sequence(30, 0, 0, seed=4)
        clean = self.c_run(refs)[-1][1]
        nudged = list(refs)
        for index in range(20, len(nudged)):
            master_us, cycles = nudged[index]
            nudged[index] = (master_us, (cycles + 1000) % (1 << 32))
        moved = self.c_run(nudged)[-1][1]
        single = abs(((RATIO_ONE * 1000) // (HZ // 50)))
        self.assertEqual(clean, RATIO_ONE)
        self.assertLess(abs(moved - RATIO_ONE), single)


class AgentSyncTests(unittest.TestCase):
    """Check the Pi agent only synchronizes when it is not the master."""

    def agent(self, node_id):
        """Build an agent that records what it would transmit.

        :param node_id: Numeric ID this agent runs as.
        :return: The agent.
        """
        config = load_cluster_config(ROOT / "config" / "cluster.conf")
        return RaftAgent(config, node_id, lambda can_id, data: None)

    def test_leader_ignores_its_own_reference(self):
        """A leader hears its REF back on the other bus and must not sync to it."""
        agent = self.agent(1)
        self.assertTrue(agent.is_leader())
        self.assertFalse(agent.on_ref(ref_encode(2000000000000000, 0)))
        self.assertFalse(agent.sync.have_last)

    def test_follower_adopts_the_master_timeline(self):
        """A following Pi reports the master's clock, not its own."""
        agent = self.agent(2)
        self.assertFalse(agent.is_leader())
        master = 2000000000000000
        self.assertTrue(agent.on_ref(ref_encode(master, 0), 0))
        self.assertEqual(agent.global_us(0), master + DELAY_US)

    def test_handover_does_not_step_the_timeline(self):
        """Taking the master role keeps the estimate this node already held."""
        agent = self.agent(2)
        master = 2000000000000000
        agent.on_ref(ref_encode(master, 0), 0)
        agent.on_ref(ref_encode(master + 20000, 20000), 20000)
        before = agent.global_us()
        agent.adopt_master_role()
        agent.role = "LEADER"
        after = agent.global_us()
        # Both readings are taken a moment apart on the same wall clock, so the
        # bound here is real elapsed time, well under the 100 us the checkoff
        # allows for the handover step.
        self.assertLess(abs(after - before), 1000)

    def test_global_time_before_any_reference_is_the_local_clock(self):
        """With no master yet, a follower reports its own clock rather than zero."""
        agent = self.agent(2)
        self.assertGreater(agent.global_us(), 0)
        self.assertEqual(agent.master_offset_us, 0)


if __name__ == "__main__":
    unittest.main()
