/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file raft_agent.c
 * @brief Own the Raft role state and the receive side of REF.
 */
#include "raft_agent.h"
#include "cluster_config.h"
#include "lab_can.h"
#include "lab_io.h"
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

/** @brief Term every node adopts when a pre-defined leader is configured. */
#define BOOT_TERM 1
/** @brief Shortest interval between reports of a reference gap, milliseconds.
 *
 * A gap is exceptional, so reporting it is worth the UART time, but a bus that
 * is dropping references would otherwise print on every frame. Lab 3 disables
 * periodic console output so printing cannot distort timing measurements; this
 * stays within that rule by printing only on an event, and never in a burst.
 */
#define GAP_REPORT_MS 1000
/** @brief Cycle counter step above which a jump means the master restarted.
 *
 * The counter is 16 bits and advances by one every heartbeat, so a forward step
 * of more than half its range is really a backward jump: the time master
 * restarted and began counting from zero again. Treating that as lost
 * references would report tens of thousands of them, which is what happened
 * before this check existed. The checkoff restarts the leader repeatedly, so
 * this is a normal event, not an error.
 */
#define CYCLE_RESTART_STEP 32768U
/** @brief Milliseconds without a REF after which this node is no longer synced.
 *
 * The same bound Part 2 gives the follower for declaring the leader dead, so
 * SYNC goes out at the moment an election would start rather than lingering.
 */
#define SYNC_TIMEOUT_MS (LAB5_CFG_MISSED_HEARTBEATS * LAB5_CFG_HEARTBEAT_MS)
/** @brief How long a link must look bad before it is reported down, milliseconds.
 *
 * A jumper pull is mechanically noisy and the controller crosses error passive
 * on its way to bus off, so an undebounced report puts several entries in the
 * log for one physical change. Three monitor periods, well inside the 100 ms
 * the Lab 3 recovery requirement allows (TASKS.md D16).
 */
#define LINK_DOWN_DEBOUNCE_MS 60
/** @brief How long a link must look healthy before it is reported up, milliseconds.
 *
 * Deliberately much longer than the down bound. A disconnected branch does not
 * sit quietly in bus off: the controller recovers automatically after 128 by 11
 * recessive bits, returns to error active with a cleared transmit counter, then
 * fails again as soon as it transmits. On the bench that produced an endless
 * DOWN, UP, DOWN, UP sequence at roughly 1.7 entries a second, which a
 * symmetric debounce cannot suppress because each half of the cycle outlasts
 * it. Requiring the link to look clean for most of a second outlasts the
 * recovery window instead. Coming up late costs nothing; the checkoff does not
 * time the entry, only requires one per change.
 */
#define LINK_UP_DEBOUNCE_MS 750
/** @brief Resend period for a client request the leader has not acted on. */
#define CLIENT_RETRY_MS 200

static struct {
	enum raft_role role;
	uint32_t term;
	int leader;
	bool synced;
	bool have_cycle;
	uint16_t cycle;
	uint32_t missed;
	int64_t last_ref_ms;
	int64_t last_gap_report_ms;
	struct lab5_ref last;
	uint64_t last_local_us;
	struct raft_log log;
	struct network net;
	uint32_t commit_index;
	/* Client: this node's own view of its two links. */
	bool link_down[2];
	bool link_candidate[2];
	int64_t link_changed_ms[2];
	uint8_t pending_event;
	uint16_t pending_request;
	int64_t pending_sent_ms;
	uint16_t next_request;
} state;
static struct k_spinlock state_lock;

/** @brief Adopt the configured boot role.
 * @note Holds no election: with a pre-defined leader Part 2 forbids one at boot.
 */
void raft_agent_init(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);

	state.leader = LAB5_CFG_DEFAULT_LEADER;
	state.term = state.leader != 0 ? BOOT_TERM : 0;
	state.role = state.leader == LAB_NODE_ID ? RAFT_LEADER : RAFT_FOLLOWER;
	raft_log_init(&state.log);
	network_reset(&state.net);
	state.commit_index = 0;
	/*
	 * Assume both links are down so the first poll reports them coming up.
	 * An empty log replays to a graph with no links, so a node that only ever
	 * reported changes would leave its own links missing from the graph until
	 * someone moved a jumper. The leader bootstraps its own links the same way.
	 */
	for (int bus = 0; bus < 2; bus++) {
		state.link_down[bus] = true;
		state.link_candidate[bus] = true;
		state.link_changed_ms[bus] = 0;
	}
	k_spin_unlock(&state_lock, key);

	lab_io_set(LAB_LEADER, state.role == RAFT_LEADER);
	lab_io_set(LAB_SYNC, false);
	if (LAB5_CFG_DEBUG) {
		printk("raft: term=%u role=%s leader=%s\n", state.term,
		       state.role == RAFT_LEADER ? "LEADER" : "FOLLOWER",
		       lab_node_name(state.leader));
	}
}

/** @brief Report the current role.
 * @return Role of this node.
 */
enum raft_role raft_role(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	enum raft_role role = state.role;

	k_spin_unlock(&state_lock, key);
	return role;
}

/** @brief Report the current term.
 * @return Term number.
 */
uint32_t raft_term(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	uint32_t term = state.term;

	k_spin_unlock(&state_lock, key);
	return term;
}

/** @brief Report the node believed to be leader.
 * @return Node ID, or zero when no leader is known.
 */
int raft_leader(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	int leader = state.leader;

	k_spin_unlock(&state_lock, key);
	return leader;
}

/** @brief Report whether a REF has been received since boot.
 * @return True once this node has a time master.
 */
bool raft_synced(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	bool synced = state.synced;

	k_spin_unlock(&state_lock, key);
	return synced;
}

/** @brief Apply a received REF frame.
 * @param data Eight received bytes.
 * @param local_us Local clock when the frame was taken off the queue.
 */
void raft_on_ref(const uint8_t data[8], uint64_t local_us)
{
	struct lab5_ref ref = lab5_ref_decode(data);
	int64_t now = k_uptime_get();
	uint32_t gap = 0;
	uint32_t missed;
	bool report_gap = false;
	bool restarted = false;
	bool first;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	/* A gap of more than one in the counter means references were lost. */
	if (state.have_cycle) {
		uint16_t step = (uint16_t)(ref.cycle - state.cycle);

		if (step >= CYCLE_RESTART_STEP) {
			restarted = true;
		} else if (step > 1) {
			gap = (uint32_t)(step - 1);
			state.missed += gap;
			report_gap = now - state.last_gap_report_ms >= GAP_REPORT_MS;
			if (report_gap) {
				state.last_gap_report_ms = now;
			}
		}
	}
	first = !state.synced;
	missed = state.missed;
	state.last_ref_ms = now;
	state.cycle = ref.cycle;
	state.have_cycle = true;
	state.synced = true;
	state.last = ref;
	state.last_local_us = local_us;
	k_spin_unlock(&state_lock, key);

	lab_io_toggle(LAB_REF_RX);
	if (first) {
		lab_io_set(LAB_SYNC, true);
	}
	if (!LAB5_CFG_DEBUG) {
		return;
	}
	/* Printing runs in the dispatch thread, so keep it to events, not a period. */
	if (first) {
		printk("raft: SYNC to %s, cycle=%u master=%llu us\n",
		       lab_node_name(raft_leader()), ref.cycle,
		       (unsigned long long)ref.master_us);
	} else if (restarted) {
		printk("raft: time master restarted, cycle=%u master=%llu us\n",
		       ref.cycle, (unsigned long long)ref.master_us);
	} else if (report_gap) {
		printk("raft: missed %u REF at cycle=%u, %u total\n", gap, ref.cycle, missed);
	}
}

/** @brief Age out the time master when references stop arriving.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_agent_tick(int64_t now_ms)
{
	bool lost = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (state.synced && now_ms - state.last_ref_ms >= SYNC_TIMEOUT_MS) {
		state.synced = false;
		/* Keep the counter: the next REF is a new master, not a gap. */
		state.have_cycle = false;
		lost = true;
	}
	k_spin_unlock(&state_lock, key);

	if (!lost) {
		return;
	}
	lab_io_set(LAB_SYNC, false);
	if (LAB5_CFG_DEBUG) {
		printk("raft: lost SYNC, no REF for %d ms\n", SYNC_TIMEOUT_MS);
	}
}

/** @brief Apply a received AppendEntries and acknowledge it.
 * @param data Eight received bytes.
 */
void raft_on_append(const uint8_t data[8])
{
	struct lab5_append header = lab5_append_decode(data);
	struct lab5_ack ack;
	uint32_t match = 0;
	uint32_t committed = 0;
	uint32_t stored = 0;
	bool advanced = false;
	bool grew = false;
	enum raft_append_result result;
	char line[NETWORK_PRINT_SIZE];

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	/* A newer term means a new leader; adopt it and follow. */
	if (header.term > state.term) {
		state.term = header.term;
		state.role = RAFT_FOLLOWER;
		state.leader = 0;
	}
	uint32_t before = raft_log_last_index(&state.log);

	result = raft_log_accept(&state.log, (uint8_t)state.term, header.term,
				 header.prev_index, header.prev_term, header.count,
				 header.event, &match);
	stored = raft_log_last_index(&state.log);
	grew = stored > before;
	if (result == RAFT_APPEND_ACCEPTED) {
		/* Commit no further than what we actually hold. */
		uint32_t limit = raft_log_last_index(&state.log);
		uint32_t target = header.commit < limit ? header.commit : limit;

		if (target > state.commit_index) {
			state.commit_index = target;
			network_replay(&state.net, &state.log, state.commit_index);
			advanced = true;
		}
	}
	committed = state.commit_index;
	ack.term = (uint8_t)state.term;
	ack.match_index = (uint16_t)match;
	ack.result = (uint8_t)result;
	network_print(&state.net, line, sizeof(line));
	k_spin_unlock(&state_lock, key);

	uint8_t reply[8];

	lab5_ack_encode(reply, &ack);
	lab_can_send_lab5(lab5_id_for(LAB5_APPEND_ACK_BASE, LAB_NODE_ID), reply);

	if (!LAB5_CFG_DEBUG) {
		return;
	}
	/* Required by the Debug config key, and only on a change, so the 100 ms
	 * empty append does not print forever.
	 */
	if (grew) {
		/* Checkoff step 4 compares one entry per change at the same index
		 * on every console, so print each stored entry, not just commits.
		 */
		printk("raft: store idx=%u term=%u %s\n", stored, header.term,
		       raft_event_name(header.event));
	}
	if (advanced) {
		printk("raft: commit idx=%u %s\n", committed, line);
	} else if (result != RAFT_APPEND_ACCEPTED) {
		printk("raft: append rejected result=%d prev=%u match=%u\n",
		       (int)result, header.prev_index, match);
	}
}

/** @brief Send the pending client request to the leader.
 *
 * Called with the lock released. The leader appends and replicates; the client
 * learns the outcome either from the reply or from seeing its own entry commit.
 * @param event Event to record.
 * @param request Request id to match the reply.
 */
static void send_client_request(uint8_t event, uint16_t request)
{
	struct lab5_client payload = {.event = event, .request = request};
	uint8_t data[8];

	lab5_client_encode(data, &payload);
	lab_can_send_lab5(lab5_id_for(LAB5_CLIENT_REQ_BASE, LAB_NODE_ID), data);
	if (LAB5_CFG_DEBUG) {
		printk("raft: client req=%u %s\n", request, raft_event_name(event));
	}
}

/** @brief Poll this node's own links and report any confirmed change.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_client_poll(int64_t now_ms)
{
	uint8_t event = RAFT_EVENT_NONE;
	uint16_t request = 0;
	bool retry = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	for (int bus = 0; bus < 2; bus++) {
		bool down = !lab_can_link_healthy(bus);
		int64_t needed = down ? LINK_DOWN_DEBOUNCE_MS : LINK_UP_DEBOUNCE_MS;

		if (down != state.link_candidate[bus]) {
			state.link_candidate[bus] = down;
			state.link_changed_ms[bus] = now_ms;
			continue;
		}
		if (down == state.link_down[bus] ||
		    now_ms - state.link_changed_ms[bus] < needed) {
			continue;
		}
		/* Settled and different: this is one real change to report. */
		state.link_down[bus] = down;
		if (state.pending_event == RAFT_EVENT_NONE) {
			state.pending_event = raft_event_link(LAB_NODE_ID, bus + 1, !down);
			state.pending_request = ++state.next_request;
			state.pending_sent_ms = now_ms;
			event = state.pending_event;
			request = state.pending_request;
		}
	}
	if (event == RAFT_EVENT_NONE && state.pending_event != RAFT_EVENT_NONE &&
	    now_ms - state.pending_sent_ms >= CLIENT_RETRY_MS) {
		/* The leader never acted on it: ask again rather than drop it. */
		state.pending_sent_ms = now_ms;
		event = state.pending_event;
		request = state.pending_request;
		retry = true;
	}
	k_spin_unlock(&state_lock, key);

	if (event != RAFT_EVENT_NONE) {
		ARG_UNUSED(retry);
		send_client_request(event, request);
	}
}

/** @brief Apply a leader reply to a client request.
 * @param data Eight received bytes.
 */
void raft_on_reply(const uint8_t data[8])
{
	struct lab5_reply reply = lab5_reply_decode(data);
	bool matched = false;
	bool redirect = reply.result == LAB5_REPLY_REDIRECT;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (state.pending_event != RAFT_EVENT_NONE &&
	    reply.request == state.pending_request) {
		matched = true;
		if (!redirect) {
			/* Step 6: the leader acknowledged the write. */
			state.pending_event = RAFT_EVENT_NONE;
		}
	}
	if (redirect && reply.leader != 0) {
		state.leader = reply.leader;
	}
	k_spin_unlock(&state_lock, key);

	if (matched && LAB5_CFG_DEBUG) {
		if (redirect) {
			printk("raft: redirected to %s, retrying req=%u\n",
			       lab_node_name(reply.leader), reply.request);
		} else {
			printk("raft: client ack req=%u idx=%u\n", reply.request,
			       reply.index);
		}
	}
}

/** @brief Render the committed network graph for the console.
 * @param out Destination buffer.
 * @param size Size of out, at least NETWORK_PRINT_SIZE.
 */
void raft_network_print(char *out, size_t size)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);

	network_print(&state.net, out, size);
	k_spin_unlock(&state_lock, key);
}

/** @brief Report the highest committed index.
 * @return Commit index, or 0 when nothing is committed.
 */
uint32_t raft_commit_index(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	uint32_t index = state.commit_index;

	k_spin_unlock(&state_lock, key);
	return index;
}

/** @brief Read the most recent reference.
 * @param ref Destination for the last decoded REF.
 * @param local_us Destination for the local clock at that receipt.
 * @return True when a REF has been received.
 */
bool raft_last_ref(struct lab5_ref *ref, uint64_t *local_us)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	bool synced = state.synced;

	*ref = state.last;
	*local_us = state.last_local_us;
	k_spin_unlock(&state_lock, key);
	return synced;
}

/** @brief Count REF frames missed, detected as cycle counter gaps.
 * @return Missed reference count since boot.
 */
uint32_t raft_missed_refs(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	uint32_t missed = state.missed;

	k_spin_unlock(&state_lock, key);
	return missed;
}
