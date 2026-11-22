/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file raft_log.c
 * @brief Ring-backed replicated log and the event encoding it stores.
 */
#include "raft_log.h"
#include <stddef.h>

/** @brief Highest node ID the cluster uses. */
#define MAX_NODE 3
/** @brief Number of CAN buses each node attaches to. */
#define BUS_COUNT 2

/** @brief Build an event for a node coming up or going down.
 * @param node Node ID 1..3.
 * @param up True for up, false for down.
 * @return Encoded event, or RAFT_EVENT_NONE when the node is invalid.
 */
uint8_t raft_event_node(int node, bool up)
{
	if (node < 1 || node > MAX_NODE) {
		return RAFT_EVENT_NONE;
	}
	return (uint8_t)((up ? RAFT_NODE_UP_BASE : RAFT_NODE_DOWN_BASE) + node);
}

/** @brief Build an event for a CAN link changing state.
 * @param node Node ID 1..3.
 * @param bus Bus number 1 or 2.
 * @param up True for up, false for down.
 * @return Encoded event, or RAFT_EVENT_NONE when node or bus is invalid.
 */
uint8_t raft_event_link(int node, int bus, bool up)
{
	if (node < 1 || node > MAX_NODE || bus < 1 || bus > BUS_COUNT) {
		return RAFT_EVENT_NONE;
	}
	uint8_t base = up ? RAFT_LINK_UP_BASE : RAFT_LINK_DOWN_BASE;

	return (uint8_t)(base + (node - 1) * BUS_COUNT + (bus - 1));
}

/** @brief Name an event for the console.
 * @param event Encoded event.
 * @return Static name, or "?" when the encoding is not recognised.
 */
const char *raft_event_name(uint8_t event)
{
	static const char *const node_up[] = {
		"NODE_cockpit_UP", "NODE_steering_UP", "NODE_drivetrain_UP"};
	static const char *const node_down[] = {
		"NODE_cockpit_DOWN", "NODE_steering_DOWN", "NODE_drivetrain_DOWN"};
	static const char *const link_up[] = {
		"CANLINK_cockpit_1_UP", "CANLINK_cockpit_2_UP",
		"CANLINK_steering_1_UP", "CANLINK_steering_2_UP",
		"CANLINK_drivetrain_1_UP", "CANLINK_drivetrain_2_UP"};
	static const char *const link_down[] = {
		"CANLINK_cockpit_1_DOWN", "CANLINK_cockpit_2_DOWN",
		"CANLINK_steering_1_DOWN", "CANLINK_steering_2_DOWN",
		"CANLINK_drivetrain_1_DOWN", "CANLINK_drivetrain_2_DOWN"};

	if (event == RAFT_COCKPIT_OK) {
		return "COCKPIT_OK";
	}
	if (event == RAFT_COCKPIT_ERROR) {
		return "COCKPIT_ERROR";
	}
	if (event > RAFT_NODE_UP_BASE && event <= RAFT_NODE_UP_BASE + MAX_NODE) {
		return node_up[event - RAFT_NODE_UP_BASE - 1];
	}
	if (event > RAFT_NODE_DOWN_BASE && event <= RAFT_NODE_DOWN_BASE + MAX_NODE) {
		return node_down[event - RAFT_NODE_DOWN_BASE - 1];
	}
	if (event >= RAFT_LINK_UP_BASE &&
	    event < RAFT_LINK_UP_BASE + MAX_NODE * BUS_COUNT) {
		return link_up[event - RAFT_LINK_UP_BASE];
	}
	if (event >= RAFT_LINK_DOWN_BASE &&
	    event < RAFT_LINK_DOWN_BASE + MAX_NODE * BUS_COUNT) {
		return link_down[event - RAFT_LINK_DOWN_BASE];
	}
	return "?";
}

/** @brief Empty the log.
 * @param log Log to reset.
 */
void raft_log_init(struct raft_log *log)
{
	log->first = 1;
	log->count = 0;
}

/** @brief Append one entry, dropping the oldest when the ring is full.
 * @param log Log to append to.
 * @param term Term the entry was created in.
 * @param event Encoded event.
 * @return Index assigned to the new entry; the first entry is index 1.
 */
uint32_t raft_log_append(struct raft_log *log, uint8_t term, uint8_t event)
{
	uint32_t index = log->first + log->count;

	log->ring[(index - 1) % RAFT_LOG_RING].term = term;
	log->ring[(index - 1) % RAFT_LOG_RING].event = event;
	if (log->count < RAFT_LOG_RING) {
		log->count++;
	} else {
		/* Ring full: the oldest entry has just been overwritten. */
		log->first++;
	}
	return index;
}

/** @brief Index of the newest entry.
 * @param log Log to read.
 * @return Last index, or 0 when the log is empty.
 */
uint32_t raft_log_last_index(const struct raft_log *log)
{
	return log->count == 0 ? 0 : log->first + log->count - 1;
}

/** @brief Index of the oldest entry still retained.
 * @param log Log to read.
 * @return First retained index, or 0 when the log is empty.
 */
uint32_t raft_log_first_index(const struct raft_log *log)
{
	return log->count == 0 ? 0 : log->first;
}

/** @brief Read one entry by index.
 * @param log Log to read.
 * @param index Entry index.
 * @param entry Destination.
 * @return True when the index is retained.
 */
bool raft_log_get(const struct raft_log *log, uint32_t index, struct raft_entry *entry)
{
	if (log->count == 0 || index < log->first || index > raft_log_last_index(log)) {
		return false;
	}
	*entry = log->ring[(index - 1) % RAFT_LOG_RING];
	return true;
}

/** @brief Drop every entry from an index onward.
 * @param log Log to shorten.
 * @param from Index of the first entry to drop; 1 clears the log.
 */
void raft_log_truncate(struct raft_log *log, uint32_t from)
{
	if (log->count == 0 || from > raft_log_last_index(log)) {
		return;
	}
	if (from <= log->first) {
		log->count = 0;
		return;
	}
	log->count = from - log->first;
}

/** @brief Apply the Raft AppendEntries consistency check.
 * @param log Log to update.
 * @param current_term Term this node believes it is in.
 * @param leader_term Term from the AppendEntries header.
 * @param prev_index Index preceding the carried entry.
 * @param prev_term Term of that preceding entry.
 * @param count Entries carried, 0 or 1.
 * @param event Event of the carried entry, when count is 1.
 * @param match_index Highest index now known to match the leader.
 * @return What happened; only RAFT_APPEND_ACCEPTED changes the log.
 */
enum raft_append_result raft_log_accept(struct raft_log *log, uint8_t current_term,
					uint8_t leader_term, uint32_t prev_index,
					uint8_t prev_term, uint8_t count,
					uint8_t entry_term, uint8_t event,
					uint32_t *match_index)
{
	uint8_t have;

	*match_index = 0;
	if (leader_term < current_term) {
		return RAFT_APPEND_STALE_TERM;
	}
	if (prev_index > raft_log_last_index(log)) {
		*match_index = raft_log_last_index(log);
		return RAFT_APPEND_MISSING;
	}
	if (prev_index != 0 && log->count != 0 && prev_index < log->first) {
		/* The ring dropped it, so the check cannot be made at all. */
		return RAFT_APPEND_FORGOTTEN;
	}
	if (!raft_log_term_at(log, prev_index, &have)) {
		return RAFT_APPEND_FORGOTTEN;
	}
	if (have != prev_term) {
		/* Our tail disagrees: drop it so the leader can back up. */
		raft_log_truncate(log, prev_index);
		*match_index = raft_log_last_index(log);
		return RAFT_APPEND_MISMATCH;
	}
	if (count != 0) {
		uint32_t next = prev_index + 1;
		struct raft_entry existing;

		if (raft_log_get(log, next, &existing)) {
			if (existing.term == entry_term && existing.event == event) {
				/* Already stored: a duplicate copy, not a conflict. */
				*match_index = next;
				return RAFT_APPEND_ACCEPTED;
			}
			raft_log_truncate(log, next);
		}
		/*
		 * The entry's own term, not the leader's. Storing the leader's
		 * term here rewrites history under a new leader and makes the
		 * next consistency check on this index fail for ever.
		 */
		raft_log_append(log, entry_term, event);
	}
	*match_index = prev_index + (count != 0 ? 1 : 0);
	return RAFT_APPEND_ACCEPTED;
}

/** @brief Term of a given index, for the AppendEntries consistency check.
 * @param log Log to read.
 * @param index Entry index; zero means the empty log before any entry.
 * @param term Destination for the term.
 * @return True when the term is known.
 */
bool raft_log_term_at(const struct raft_log *log, uint32_t index, uint8_t *term)
{
	struct raft_entry entry;

	if (index == 0) {
		*term = 0;
		return true;
	}
	if (!raft_log_get(log, index, &entry)) {
		return false;
	}
	*term = entry.term;
	return true;
}
