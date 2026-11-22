/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file election.c
 * @brief A small deterministic generator for election timeouts.
 */
#include "election.h"

/** @brief Multiplier of the 32 bit xorshift mix applied after each step.
 *
 * Plain xorshift32 has visibly poor low bits, and the timeout uses a modulus,
 * which reads exactly those bits. Multiplying by an odd constant before taking
 * the modulus spreads the entropy down, which is the standard xorshift
 * multiply construction. This is not a cryptographic generator and does not
 * need to be: it only has to give each node a different, repeatable draw.
 */
#define MIX 0x2545f491U

/** @brief Advance the generator.
 * @param state Generator state, advanced in place.
 * @return Next value.
 */
static uint32_t next(uint32_t *state)
{
	uint32_t x = *state;

	x ^= x << 13;
	x ^= x >> 17;
	x ^= x << 5;
	*state = x;
	return x * MIX;
}

/** @brief Start the per node sequence.
 * @param state Generator state to initialize.
 * @param seed Configured seed, shared by every node.
 * @param node Local node ID, which makes the sequence differ per node.
 */
void election_seed(uint32_t *state, uint32_t seed, int node)
{
	/*
	 * Mix the node in rather than adding it, so neighbouring node IDs do not
	 * produce neighbouring sequences. Never zero, which xorshift cannot
	 * leave.
	 */
	*state = (seed * 2654435761U) ^ ((uint32_t)node * 2246822519U);
	if (*state == 0) {
		*state = 1;
	}
	/* Discard the first few, which still carry the seed's structure. */
	for (int i = 0; i < 4; i++) {
		(void)next(state);
	}
}

/** @brief Draw the next election timeout.
 * @param state Generator state, advanced in place.
 * @param base_ms Fixed part, missed_heartbeats * heartbeat_ms.
 * @param jitter_ms Width of the random part; zero gives base_ms exactly.
 * @return Timeout in milliseconds.
 */
uint32_t election_timeout_ms(uint32_t *state, uint32_t base_ms, uint32_t jitter_ms)
{
	if (jitter_ms == 0) {
		return base_ms;
	}
	return base_ms + next(state) % jitter_ms;
}
