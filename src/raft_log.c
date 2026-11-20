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
