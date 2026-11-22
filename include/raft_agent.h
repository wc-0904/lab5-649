/* SPDX-License-Identifier: Apache-2.0 */
/** @file raft_agent.h
 * @brief Raft role state and the receive side of the leader's periodic frames.
 */
#ifndef RAFT_AGENT_H_
#define RAFT_AGENT_H_
#include "lab_protocol.h"
#include "network_state.h"
#include "raft_log.h"
#include <stddef.h>
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
 * @param local_cycles Cycle counter captured in the receive callback, which is
 *        the F_k of the sync arithmetic and is not the same instant as
 *        local_us: that one includes the queue wait.
 * @param relayed True when this copy arrived through another node's relay
 *        rather than directly, which adds a second one way delay in series
 *        with the master's own and must be compensated separately
 *        (TASKS.md D18).
 */
void raft_on_ref(const uint8_t data[8], uint64_t local_us, uint32_t local_cycles,
		bool relayed);
/** @brief Age out the time master when references stop arriving.
 *
 * Called periodically from the monitor task. SYNC must mean that this node is
 * currently synchronized, not that it once saw a REF, because checkoff step 6
 * watches it go out when the master dies and step 9 watches it stay on at a
 * node reachable only by relay.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_agent_tick(int64_t now_ms);
/** @brief Apply a received AppendEntries and acknowledge it.
 *
 * Runs in the dispatch thread. Performs the consistency check, adopts a newer
 * term, advances the commit index, replays the state machine through it, and
 * sends this node's acknowledgement. Step 5 of the client write path needs its
 * own frame because a CAN frame ack only means someone heard it.
 * @param data Eight received bytes.
 */
void raft_on_append(const uint8_t data[8]);
/** @brief Poll this node's own links and report any confirmed change.
 *
 * Owner-reports-own-links (TASKS.md D15): each node reports only its own two
 * links, so one physical change produces exactly one log entry, which is what
 * checkoff step 4 requires. A change must hold for LINK_DEBOUNCE_MS before it
 * is reported, or a jumper pull chatters several entries into the log (D16).
 * @param now_ms Current uptime in milliseconds.
 */
void raft_client_poll(int64_t now_ms);
/** @brief Apply a leader reply to a client request.
 * @param data Eight received bytes.
 */
void raft_on_reply(const uint8_t data[8]);
/** @brief Render the committed network graph for the console.
 *
 * The demo compares this line across the three consoles, so it must match the
 * Pi's rendering character for character.
 * @param out Destination buffer.
 * @param size Size of out, at least NETWORK_PRINT_SIZE.
 */
void raft_network_print(char *out, size_t size);
/** @brief Report the highest committed index.
 * @return Commit index, or 0 when nothing is committed.
 */
uint32_t raft_commit_index(void);
/** @brief Read the most recent reference, for the console and later phases.
 * @param ref Destination for the last decoded REF.
 * @param local_us Destination for the local clock at that receipt.
 * @return True when a REF has been received.
 */
bool raft_last_ref(struct lab5_ref *ref, uint64_t *local_us);
/** @brief Start an election if nothing has been heard from a leader.
 *
 * Called from the monitor task. A fresh timeout is drawn for every round, so
 * two nodes that collided once do not collide again on every retry.
 * @param now_ms Current uptime in milliseconds.
 * @param hearing True when this node heard any frame within LAB5_ISOLATION_MS.
 *        A node that hears nothing does not stand and restarts its timeout.
 */
void raft_election_tick(int64_t now_ms, bool hearing);
/** @brief Answer a vote request.
 * @param data Eight received bytes.
 * @param from Node that sent it, taken from the identifier.
 */
void raft_on_vote_request(const uint8_t data[8], int from);
/** @brief Count a vote and take the leadership when a majority is reached.
 * @param data Eight received bytes.
 * @param from Node that voted, taken from the identifier.
 */
void raft_on_vote(const uint8_t data[8], int from);
/** @brief Stamp and send one reference frame. Leader only.
 *
 * The clock stamped is this node's global estimate, not its local uptime. A
 * new leader tracked the shared timeline while it was a follower, so
 * continuing to report that is what keeps the handover free of a step.
 * @return True when a frame was sent.
 */
bool raft_ref_tx(void);
/** @brief Send one AppendEntries when leading.
 *
 * Also the only frame that makes a node which still believes it leads adopt a
 * newer term and stand down: REF carries no term, and a vote only reaches a
 * node that took part in the election.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_append_tx(int64_t now_ms);
/** @brief Apply a follower's acknowledgement. Leader only.
 * @param data Eight received bytes.
 * @param from Node that acknowledged, taken from the identifier.
 */
void raft_on_ack(const uint8_t data[8], int from);
/** @brief Record a client's event when leading, and answer it.
 * @param data Eight received bytes.
 * @param from Node that asked, taken from the identifier.
 */
void raft_on_client_request(const uint8_t data[8], int from);
/** @brief Record whether the cockpit is still heartbeating. Leader only.
 *
 * Only the leader may append COCKPIT_OK or COCKPIT_ERROR, which is what keeps
 * checkoff step 4's rule of exactly one entry per change: every node can see
 * the cockpit go quiet, but only one of them is entitled to say so.
 * @param alive True when cockpit heartbeats are fresh.
 */
void raft_report_cockpit(bool alive);
/** @brief Note that a frame from a node was just received.
 *
 * Fed by heartbeats and by every consensus frame whose identifier names its
 * sender, so a node with a latched local failure still counts as present.
 * @param node Sending node ID.
 * @param now_ms Receive time in milliseconds.
 */
void raft_heard_from(int node, int64_t now_ms);
/** @brief Leader only: record a peer silent for LAB5_PEER_DOWN_MS as down.
 *
 * Appends NODE_X_DOWN once per silence, and only for a node the committed graph
 * shows up. The node re-reports itself when it returns. The isolated node cannot
 * report itself, so this is the only way the cluster learns of the isolation.
 * @param now_ms Current uptime in milliseconds.
 * @param hearing True when this node heard any frame within LAB5_ISOLATION_MS.
 */
void raft_peer_tick(int64_t now_ms, bool hearing);
/** @brief Report whether any node is missing a link in the committed graph.
 *
 * When every node holds both links a relayed copy is redundant, because each
 * frame is already transmitted on both buses by its originator.
 * @return True when relaying is worth doing.
 */
bool raft_relay_needed(void);
/** @brief Report whether the committed log puts the vehicle in failure mode.
 * @return True when the cockpit is recorded as down.
 */
bool raft_failure_mode(void);
/** @brief Count REF frames missed, detected as cycle counter gaps.
 * @return Missed reference count since boot.
 */
uint32_t raft_missed_refs(void);
#endif
