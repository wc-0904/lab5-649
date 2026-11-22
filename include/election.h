/* SPDX-License-Identifier: Apache-2.0 */
/** @file election.h
 * @brief Deterministic election timeouts, seeded so a demo can be repeated.
 *
 * Raft breaks ties between candidates with a random election timeout. A
 * hardware entropy source would do that correctly and make the demo
 * unrepeatable, which is the wrong trade here: checkoff step 8 asks for "the
 * seed that makes the Pi win", so the draw has to be a function of something
 * the operator sets (TASKS.md D14).
 *
 * The sequence is per node: seeding mixes the configured seed with the local
 * node ID, so one seed in the shared configuration file gives each node a
 * different series of draws while every node still agrees on what the seed
 * was. `tools/find_seed.py` searches for a seed whose first draws elect a
 * chosen node.
 *
 * Pure C with no Zephyr dependency, so the host tests run it directly and the
 * Pi mirrors it exactly.
 */
#ifndef ELECTION_H_
#define ELECTION_H_
#include <stdint.h>
/** @brief Start the per node sequence.
 * @param state Generator state to initialize.
 * @param seed Configured seed, shared by every node.
 * @param node Local node ID, which makes the sequence differ per node.
 */
void election_seed(uint32_t *state, uint32_t seed, int node);
/** @brief Draw the next election timeout.
 *
 * The handout's formula: missed_heartbeats * heartbeat_ms + U(0, jitter), so
 * 60 to 99 ms with the defaults. Drawn fresh for every election round, because
 * a timeout reused across rounds would make two nodes that collided once
 * collide again forever.
 * @param state Generator state, advanced in place.
 * @param base_ms Fixed part, missed_heartbeats * heartbeat_ms.
 * @param jitter_ms Width of the random part; zero gives base_ms exactly.
 * @return Timeout in milliseconds.
 */
uint32_t election_timeout_ms(uint32_t *state, uint32_t base_ms, uint32_t jitter_ms);
#endif
