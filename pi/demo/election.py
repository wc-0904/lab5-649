"""Deterministic election timeouts, mirroring src/election.c exactly.

Raft breaks ties between candidates with a random election timeout. A hardware
entropy source would do that correctly and make the demo unrepeatable, which is
the wrong trade here: checkoff step 8 asks for "the seed that makes the Pi
win", so the draw has to be a function of something the operator sets.

The sequence is per node. Seeding mixes the configured seed with the local node
ID, so one seed in the shared configuration file gives each node a different
series while every node still agrees on what the seed was.
"""

#: Mask for 32 bit arithmetic, which C does by overflowing and Python does not.
MASK32 = 0xFFFFFFFF
#: Multiplier applied after each xorshift step. Must match MIX in election.c.
MIX = 0x2545F491
#: Seeding constants, also from election.c.
SEED_MIX = 2654435761
NODE_MIX = 2246822519
#: Draws discarded after seeding, which still carry the seed's structure.
WARMUP = 4


class Election:
    """A repeatable source of election timeouts for one node."""

    def __init__(self, seed, node):
        """Start the per node sequence.

        :param seed: Configured seed, shared by every node.
        :param node: Local node ID, which makes the sequence differ per node.
        """
        self.state = ((seed * SEED_MIX) & MASK32) ^ ((node * NODE_MIX) & MASK32)
        self.state &= MASK32
        if self.state == 0:
            self.state = 1
        for _ in range(WARMUP):
            self._next()

    def _next(self):
        """Advance the generator.

        :return: Next value.
        """
        x = self.state
        x ^= (x << 13) & MASK32
        x ^= x >> 17
        x ^= (x << 5) & MASK32
        self.state = x & MASK32
        return (self.state * MIX) & MASK32

    def timeout_ms(self, base_ms, jitter_ms):
        """Draw the next election timeout.

        The handout's formula: missed_heartbeats * heartbeat_ms + U(0, jitter).
        Drawn fresh for every round, because a timeout reused across rounds
        would make two nodes that collided once collide again forever.

        :param base_ms: Fixed part, missed_heartbeats * heartbeat_ms.
        :param jitter_ms: Width of the random part; zero gives base_ms exactly.
        :return: Timeout in milliseconds.
        """
        if jitter_ms == 0:
            return base_ms
        return base_ms + self._next() % jitter_ms
