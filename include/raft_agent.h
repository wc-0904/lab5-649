/* SPDX-License-Identifier: Apache-2.0 */
/** @file raft_agent.h
 * @brief Raft role state and the receive side of the leader's periodic frames.
 */
#ifndef RAFT_AGENT_H_
#define RAFT_AGENT_H_
#include "lab_protocol.h"
#include <stdbool.h>
#include <stdint.h>
/** @brief Raft role of this node. */
enum raft_role { RAFT_FOLLOWER, RAFT_CANDIDATE, RAFT_LEADER };
/** @brief Adopt the configured boot role.
 *
 * With default_leader set to a node ID every node boots in term 1 with that
 * node as leader and holds no election, which is the Raft optimization Part 2
 * requires. With none or random, LAB5_CFG_DEFAULT_LEADER is zero and this node
 * boots as a follower in term 0 with no leader, leaving the election to P9.
 */
void raft_agent_init(void);
/** @brief Report the current role.
 * @return Role of this node.
 */
enum raft_role raft_role(void);
/** @brief Report the current term.
 * @return Term number.
 */
uint32_t raft_term(void);
/** @brief Report the node believed to be leader.
 * @return Node ID, or zero when no leader is known.
 */
int raft_leader(void);
/** @brief Report whether a REF has been received since boot.
 * @return True once this node has a time master.
 */
bool raft_synced(void);
/** @brief Apply a received REF frame.
 *
 * Called from the dispatch thread. Records the leader clock and cycle counter,
 * drives the REF_RX marker and the SYNC indicator, and counts any gap in the
 * cycle counter as a missed reference.
 * @param data Eight received bytes.
 * @param local_us Local clock when the frame was taken off the queue.
 */
void raft_on_ref(const uint8_t data[8], uint64_t local_us);
/** @brief Age out the time master when references stop arriving.
 *
 * Called periodically from the monitor task. SYNC must mean that this node is
 * currently synchronized, not that it once saw a REF, because checkoff step 6
 * watches it go out when the master dies and step 9 watches it stay on at a
 * node reachable only by relay.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_agent_tick(int64_t now_ms);
/** @brief Read the most recent reference, for the console and later phases.
 * @param ref Destination for the last decoded REF.
 * @param local_us Destination for the local clock at that receipt.
 * @return True when a REF has been received.
 */
bool raft_last_ref(struct lab5_ref *ref, uint64_t *local_us);
/** @brief Count REF frames missed, detected as cycle counter gaps.
 * @return Missed reference count since boot.
 */
uint32_t raft_missed_refs(void);
#endif
