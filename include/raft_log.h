/* SPDX-License-Identifier: Apache-2.0 */
/** @file raft_log.h
 * @brief Replicated log: entries of (term, index, event), stored as a ring.
 *
 * Pure C with no Zephyr dependency, so the same code is exercised by the host
 * tests. The index of an entry is implied by its position, so only the term and
 * the event are stored, two bytes per entry.
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
/** @brief Entries each node keeps in RAM.
 *
 * The configured log_size is 30000, which at any sane encoding exceeds the
 * F401RE's 96 KB of SRAM, so the STM32 keeps a bounded window of the most
 * recent entries and the Pi keeps the whole log. See TASKS.md D7. At two bytes
 * per entry this ring costs 8 KB.
 */
#define RAFT_LOG_RING 4096
/** @brief One stored entry; its index is its position in the log. */
struct raft_entry { uint8_t term; uint8_t event; };
/** @brief Log state. Treat as opaque; use the functions below. */
struct raft_log {
	struct raft_entry ring[RAFT_LOG_RING];
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
 * When the ring is full the oldest entry is dropped and the first retained
 * index advances, which is the documented STM32 deviation from log_size.
 * @param log Log to append to.
 * @param term Term the entry was created in.
 * @param event Encoded event.
 * @return Index assigned to the new entry; the first entry is index 1.
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
/** @brief Term of a given index, for the AppendEntries consistency check.
 * @param log Log to read.
 * @param index Entry index; zero means the empty log before any entry.
 * @param term Destination for the term.
 * @return True when the term is known.
 */
bool raft_log_term_at(const struct raft_log *log, uint32_t index, uint8_t *term);
#endif
