/* SPDX-License-Identifier: Apache-2.0 */
/** @file raft_log.h
 * @brief Replicated log with bounded, compact storage and implicit indices.
 *
 * Pure C with no Zephyr dependency, so the same code is exercised by the host
 * tests. The index of an entry is implied by its position, so only the term and
 * the event are stored, thirteen bits per entry.
 */
#ifndef RAFT_LOG_H_
#define RAFT_LOG_H_
#include <stdbool.h>
#include <stdint.h>
/** @brief Event types a log entry can carry.
 *
 * The encoding is systematic so a node or bus can be recovered with a shift:
 * node events are 0x10 plus the node ID, 0x18 plus the node ID when down, and
 * link events are 0x20 or 0x28 plus (node - 1) * 2 plus (bus - 1). Twenty
 * values in all, which is why five bits are enough on the wire.
 */
enum raft_event {
	RAFT_EVENT_NONE = 0x00,
	RAFT_COCKPIT_OK = 0x01,
	RAFT_COCKPIT_ERROR = 0x02,
	RAFT_NODE_UP_BASE = 0x10,
	RAFT_NODE_DOWN_BASE = 0x18,
	RAFT_LINK_UP_BASE = 0x20,
	RAFT_LINK_DOWN_BASE = 0x28,
};
/** @brief Retain the configured history without discarding committed state. */
#ifdef __ZEPHYR__
#include "cluster_config.h"
#define RAFT_LOG_RING LAB5_CFG_LOG_SIZE
#else
#define RAFT_LOG_RING 30000
#endif
/** @brief Decoded log entry. */
struct raft_entry { uint8_t term; uint8_t event; };
/** @brief Eight term bits and five event bits per entry: 48,750 bytes for 30,000. */
struct raft_log {
	uint8_t terms[RAFT_LOG_RING];
	uint8_t events[(RAFT_LOG_RING * 5 + 7) / 8];
	uint32_t first;
	uint32_t count;
};
/** @brief Build an event for a node coming up or going down.
 * @param node Node ID 1..3.
 * @param up True for up, false for down.
 * @return Encoded event, or RAFT_EVENT_NONE when the node is invalid.
 */
uint8_t raft_event_node(int node, bool up);
/** @brief Build an event for a CAN link changing state.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 * @param up True for up, false for down.
 * @return Encoded event, or RAFT_EVENT_NONE when node or bus is invalid.
 */
uint8_t raft_event_link(int node, int bus, bool up);
/** @brief Name an event for the console.
 * @param event Encoded event.
 * @return Static name such as "CANLINK_steering_1_DOWN", or "?" if unknown.
 */
const char *raft_event_name(uint8_t event);
/** @brief Empty the log.
 * @param log Log to reset.
 */
void raft_log_init(struct raft_log *log);
/** @brief Append one entry.
 *
 * A full log refuses additional writes; it never discards committed history.
 * @param log Log to append to.
 * @param term Term the entry was created in.
 * @param event Encoded event.
 * @return Index assigned, or zero when full; the first entry is index 1.
 */
uint32_t raft_log_append(struct raft_log *log, uint8_t term, uint8_t event);
/** @brief Index of the newest entry.
 * @param log Log to read.
 * @return Last index, or 0 when the log is empty.
 */
uint32_t raft_log_last_index(const struct raft_log *log);
/** @brief Index of the oldest entry still retained.
 * @param log Log to read.
 * @return First retained index, or 0 when the log is empty.
 */
uint32_t raft_log_first_index(const struct raft_log *log);
/** @brief Read one entry by index.
 * @param log Log to read.
 * @param index Entry index.
 * @param entry Destination.
 * @return True when the index is retained, false when it is empty or dropped.
 */
bool raft_log_get(const struct raft_log *log, uint32_t index, struct raft_entry *entry);
/** @brief Outcome of offering an AppendEntries to a log. */
enum raft_append_result {
	/** Entries accepted, or already present and matching. */
	RAFT_APPEND_ACCEPTED,
	/** The leader's term is older than ours, so it is not the leader. */
	RAFT_APPEND_STALE_TERM,
	/** We do not have prevLogIndex yet: this follower is behind. */
	RAFT_APPEND_MISSING,
	/** We have prevLogIndex but at a different term: our tail is wrong. */
	RAFT_APPEND_MISMATCH,
	/** prevLogIndex fell out of the ring, so it cannot be checked. */
	RAFT_APPEND_FORGOTTEN,
};
/** @brief Drop every entry from an index onward.
 *
 * Used when an AppendEntries reveals that our tail disagrees with the leader's.
 * @param log Log to shorten.
 * @param from Index of the first entry to drop; 1 clears the log.
 */
void raft_log_truncate(struct raft_log *log, uint32_t from);
/** @brief Apply the Raft AppendEntries consistency check.
 *
 * Rejects a stale term, requires prevLogIndex and prevLogTerm to match, drops a
 * conflicting tail, and appends the carried entry. Re-sending the same entry is
 * harmless, which matters because every frame is transmitted on both buses and
 * may also arrive relayed.
 * @param log Log to update.
 * @param current_term Term this node believes it is in.
 * @param leader_term Term from the AppendEntries header.
 * @param prev_index Index preceding the carried entry.
 * @param prev_term Term of that preceding entry.
 * @param count Entries carried, 0 for an empty append or 1 otherwise.
 * @param entry_term Term the carried entry was created under, which is not
 *        the leader's current term once the log spans more than one.
 * @param event Event of the carried entry, when count is 1.
 * @param match_index Set to the highest index now known to match the leader.
 * @return What happened; only RAFT_APPEND_ACCEPTED changes the log.
 */
enum raft_append_result raft_log_accept(struct raft_log *log, uint8_t current_term,
					uint8_t leader_term, uint32_t prev_index,
					uint8_t prev_term, uint8_t count,
					uint8_t entry_term, uint8_t event,
					uint32_t *match_index);
/** @brief Term of a given index, for the AppendEntries consistency check.
 * @param log Log to read.
 * @param index Entry index; zero means the empty log before any entry.
 * @param term Destination for the term.
 * @return True when the term is known.
 */
bool raft_log_term_at(const struct raft_log *log, uint32_t index, uint8_t *term);
#endif
