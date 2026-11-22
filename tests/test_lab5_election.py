"""Check the election timeout generator agrees between firmware and Pi.

The seed is the demo's only handle on which node wins, so the two
implementations have to draw identically or tools/find_seed.py would name a
seed that works on the Pi and not on the Nucleos. src/election.c is compiled
natively and its draws are compared with pi/demo/election.py exactly.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "demo"))
sys.path.insert(0, str(ROOT / "tools"))

from cluster_config import load  # noqa: E402
from election import Election  # noqa: E402
from find_seed import first_timeouts  # noqa: E402

HARNESS = r"""
#include "election.h"
#include <stdio.h>
#include <stdlib.h>

/* Prints `count` timeouts for one node and seed, one per line. */
int main(int argc, char **argv)
{
	uint32_t state;

	if (argc != 6) {
		return 1;
	}
	election_seed(&state, (uint32_t)strtoul(argv[1], NULL, 0), atoi(argv[2]));
	for (int i = atoi(argv[5]); i > 0; i--) {
		printf("%u\n", election_timeout_ms(&state,
						   (uint32_t)strtoul(argv[3], NULL, 0),
						   (uint32_t)strtoul(argv[4], NULL, 0)));
	}
	return 0;
}
"""


@unittest.skipIf(shutil.which("gcc") is None, "needs gcc to build the C reference")
class ElectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Build election.c natively; it has no Zephyr dependency."""
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name)
        source = work / "harness.c"
        source.write_text(HARNESS)
        cls.binary = work / "harness"
        subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-std=c11",
                        "-I", str(ROOT / "include"), "-o", str(cls.binary),
                        str(source), str(ROOT / "src" / "election.c")], check=True)
        cls.config = load(ROOT / "config" / "cluster.conf")

    @classmethod
    def tearDownClass(cls):
        """Drop the build directory."""
        cls.tmp.cleanup()

    def c_draws(self, seed, node, base, jitter, count):
        """Draw timeouts from the C implementation.

        :param seed: Configured seed.
        :param node: Node ID.
        :param base: Fixed part in milliseconds.
        :param jitter: Random width in milliseconds.
        :param count: How many draws.
        :return: List of timeouts.
        """
        result = subprocess.run(
            [str(self.binary), str(seed), str(node), str(base), str(jitter),
             str(count)], capture_output=True, text=True, check=True)
        return [int(line) for line in result.stdout.split()]

    def py_draws(self, seed, node, base, jitter, count):
        """Draw the same sequence from the Pi implementation.

        :param seed: Configured seed.
        :param node: Node ID.
        :param base: Fixed part in milliseconds.
        :param jitter: Random width in milliseconds.
        :param count: How many draws.
        :return: List of timeouts.
        """
        rng = Election(seed, node)
        return [rng.timeout_ms(base, jitter) for _ in range(count)]

    def test_both_implementations_draw_the_same_sequence(self):
        """A disagreement would make find_seed.py name a seed that fails on hardware."""
        for seed in (0, 1, 2, 7, 42, 65535, 2 ** 31, 2 ** 32 - 1):
            for node in (1, 2, 3):
                with self.subTest(seed=seed, node=node):
                    self.assertEqual(self.c_draws(seed, node, 60, 39, 16),
                                     self.py_draws(seed, node, 60, 39, 16))

    def test_timeouts_stay_in_the_handout_range(self):
        """missed_heartbeats * heartbeat_ms + U(0, jitter), so 60 to 99 ms."""
        base = self.config["missed_heartbeats"] * self.config["heartbeat_ms"]
        jitter = self.config["election_timeout_jitter_ms"]
        self.assertEqual(base, 60)
        for node in (1, 2, 3):
            for value in self.c_draws(11, node, base, jitter, 500):
                self.assertGreaterEqual(value, base)
                self.assertLess(value, base + jitter)

    def test_nodes_draw_differently_from_one_seed(self):
        """One shared seed must still split the cluster, or every node collides."""
        draws = [tuple(self.c_draws(9, node, 60, 39, 8)) for node in (1, 2, 3)]
        self.assertEqual(len(set(draws)), 3)

    def test_neighbouring_seeds_do_not_give_neighbouring_draws(self):
        """Seeding mixes rather than adds, so seed+1 is not a small perturbation."""
        first = self.c_draws(1000, 1, 60, 39, 8)
        second = self.c_draws(1001, 1, 60, 39, 8)
        self.assertNotEqual(first, second)

    def test_zero_jitter_is_deterministic(self):
        """Needed to turn randomness off entirely while debugging an election."""
        self.assertEqual(self.c_draws(5, 2, 60, 0, 4), [60] * 4)

    def test_a_round_is_not_reused(self):
        """Successive draws differ, or two nodes that collide once collide forever."""
        draws = self.c_draws(13, 3, 60, 39, 32)
        self.assertGreater(len(set(draws)), 8)

    def test_find_seed_agrees_with_the_generator(self):
        """The tool's prediction has to match what a node will actually draw."""
        timeouts = first_timeouts(6, self.config)
        base = self.config["missed_heartbeats"] * self.config["heartbeat_ms"]
        jitter = self.config["election_timeout_jitter_ms"]
        for index, name in enumerate(self.config["nodes"]):
            with self.subTest(node=name):
                self.assertEqual(timeouts[name],
                                 self.c_draws(6, index + 1, base, jitter, 1)[0])

    def test_a_seed_exists_that_elects_the_cockpit(self):
        """Checkoff step 8 asks for one by name, so one has to be findable."""
        timeouts = first_timeouts(6, self.config)
        self.assertLess(timeouts["cockpit"],
                        min(ms for name, ms in timeouts.items() if name != "cockpit"))


if __name__ == "__main__":
    unittest.main()
