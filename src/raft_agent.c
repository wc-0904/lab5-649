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
#include "election.h"
#include "time_sync.h"
#include "pps.h"
#include "time_sync.h"
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <time.h>

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
/** @brief Fixed part of the election timeout, milliseconds.
 *
 * The handout's formula is missed_heartbeats * heartbeat_ms + U(0, jitter), so
 * 60 to 99 ms with the defaults. It is the same bound SYNC_TIMEOUT_MS uses,
 * which is deliberate: the moment a node stops believing it has a time master
 * is the moment it should start trying to become one.
 */
#define ELECTION_BASE_MS SYNC_TIMEOUT_MS
/** @brief Idle AppendEntries period, milliseconds.
 *
 * Checkoff step 2 expects an empty APPEND every 100 ms beside REF every 20.
 * While a follower is behind the leader sends at the heartbeat rate instead,
 * so catching up takes one heartbeat per entry rather than 100 ms per entry.
 */
#define APPEND_IDLE_MS 100
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
	int64_t last_reject_report_ms;
	struct lab5_ref last;
	uint64_t last_local_us;
	struct raft_log log;
	struct network net;
	uint32_t commit_index;
	/* Client: this node's own view of its two links. */
	bool self_reported;
	bool link_down[2];
	bool link_candidate[2];
	int64_t link_changed_ms[2];
	uint8_t pending_event;
	uint16_t pending_request;
	int64_t pending_sent_ms;
	uint16_t next_request;
	/* Election: see TASKS.md D13 and D14. */
	uint32_t rng;
	uint32_t timeout_ms;
	/* Who this node voted for in the current term, or zero for nobody. */
	int voted_for;
	/* Bitmask of nodes that have granted this candidate a vote. */
	uint8_t votes;
	/* Last time anything was heard from a leader this node accepts. */
	int64_t last_contact_ms;
	/*
	 * Leader-side replication. Indexed by node ID, so slot zero is unused
	 * and the arithmetic reads the way Raft's paper writes it.
	 */
	uint32_t next_index[LAB5_CFG_N + 1];
	uint32_t match_index[LAB5_CFG_N + 1];
	int64_t last_append_ms;
	/*
	 * The last client request answered for each node, so a duplicate is
	 * answered again rather than appended again. Both buses carry every
	 * request, a relay can add more copies, and an unanswered client retries
	 * every CLIENT_RETRY_MS, so the same event reaches the leader several
	 * times as a matter of course.
	 */
	uint16_t client_request[LAB5_CFG_N + 1];
	uint16_t client_index[LAB5_CFG_N + 1];
	bool client_answered[LAB5_CFG_N + 1];
	/* Cockpit liveness, which only the leader records. */
	bool cockpit_known;
	bool cockpit_alive;
	/* Which follower the next AppendEntries is aimed at. */
	int append_peer;
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
	for (int node = 1; node <= LAB5_CFG_N; node++) {
		state.next_index[node] = 1;
		state.match_index[node] = 0;
		state.client_answered[node] = false;
	}
	raft_log_init(&state.log);
	network_reset(&state.net);
	state.commit_index = 0;
	/*
	 * Assume both links are down so the first poll reports them coming up.
	 * An empty log replays to a graph with no links, so a node that only ever
	 * reported changes would leave its own links missing from the graph until
	 * someone moved a jumper. The leader bootstraps its own links the same way.
	 */
	state.self_reported = false;
	for (int bus = 0; bus < 2; bus++) {
		state.link_down[bus] = true;
		state.link_candidate[bus] = true;
		state.link_changed_ms[bus] = 0;
	}
	election_seed(&state.rng, LAB5_CFG_ELECTION_SEED, LAB_NODE_ID);
	state.timeout_ms = election_timeout_ms(&state.rng, ELECTION_BASE_MS,
					       LAB5_CFG_JITTER_MS);
	state.voted_for = 0;
	state.votes = 0;
	/*
	 * Start the clock now rather than at zero, or the first monitor pass
	 * would find the timeout already expired and call an election before
	 * the transport has even come up.
	 */
	state.last_contact_ms = k_uptime_get();
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

/** @brief Set CLOCK_REALTIME from the master clock, once.
 *
 * Part 6 asks for the system time to be set from the adjusted leader time, and
 * also for a correcting global_gettime(). Those pull against each other: a step
 * applied every heartbeat would move CLOCK_REALTIME backwards whenever the
 * correction is negative, and every Zephyr timer scheduled across the step
 * would fire early or late. So the system clock is set exactly once, at the
 * first reference, to put it on the master's timeline, and is then left free
 * running. All subsequent correction is carried in time_sync_global_us()
 * (TASKS.md D19). The system clock's own resolution is one tick, which is far
 * coarser than the global timeline, so nothing that needs precision reads it.
 * @param master_us Leader clock from the first reference.
 */
static void set_system_clock(uint64_t master_us)
{
	struct timespec ts = {
		.tv_sec = (time_t)(master_us / 1000000U),
		.tv_nsec = (long)(master_us % 1000000U) * 1000,
	};

	if (clock_settime(CLOCK_REALTIME, &ts) != 0 && LAB5_CFG_DEBUG) {
		printk("raft: clock_settime failed\n");
	}
}

/** @brief Apply a received REF frame.
 * @param data Eight received bytes.
 * @param local_us Local clock when the frame was taken off the queue.
 */
void raft_on_ref(const uint8_t data[8], uint64_t local_us, uint32_t local_cycles)
{
	struct lab5_ref ref = lab5_ref_decode(data);
	int64_t now = k_uptime_get();
	uint32_t gap = 0;
	/*
	 * A leader is its own time master and must never apply its own
	 * reference. The two buses do not hear each other directly, but a relay
	 * hop puts a leader's REF back on its other bus, and base = L + d would
	 * then add the delay again on every pass and run away.
	 */
	if (raft_role() == RAFT_LEADER) {
		return;
	}
	uint32_t missed;
	bool report_gap = false;
	bool restarted = false;
	bool first;
	bool copy = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	/* A gap of more than one in the counter means references were lost. */
	if (state.have_cycle) {
		uint16_t step = (uint16_t)(ref.cycle - state.cycle);

		if (step == 0) {
			/*
			 * The same reference arriving on the other bus. The
			 * liveness timer is still refreshed below, since the
			 * node did hear from the master, but REF_RX must not
			 * toggle twice: the marker has to read as one edge per
			 * reference whether one bus is up or both, or pulling a
			 * jumper appears to halve the sync rate.
			 */
			copy = true;
		} else if (step >= CYCLE_RESTART_STEP) {
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
	/*
	 * A reference is contact from a live leader, so it holds the election
	 * off. This is the link between Part 2's heartbeat and Part 8's
	 * election: the moment references stop is the moment a node starts
	 * trying to become the master itself.
	 */
	state.last_contact_ms = now;
	/*
	 * The frame names its master, which is the only way a node learns who
	 * leads when default_leader is random: REF and APPEND each use one
	 * shared identifier and neither otherwise says who sent it.
	 */
	if (state.role != RAFT_LEADER && ref.master >= 1 && ref.master <= LAB5_CFG_N) {
		state.leader = ref.master;
	}
	state.cycle = ref.cycle;
	state.have_cycle = true;
	state.synced = true;
	state.last = ref;
	state.last_local_us = local_us;
	k_spin_unlock(&state_lock, key);

	/*
	 * Feed the global timeline before anything that can block or print. The
	 * cycle count, not local_us, is the sync instant: local_us is read here
	 * in the dispatch thread and so carries the queue wait, while the cycle
	 * count was taken in the receive callback (TASKS.md D17).
	 */
	time_sync_on_ref(ref.master_us, local_cycles);
	if (copy) {
		return;
	}
	lab_io_toggle(LAB_REF_RX);
	if (first) {
		lab_io_set(LAB_SYNC, true);
		set_system_clock(ref.master_us);
	}
	/*
	 * The delay belongs to the master's transmit path, so it has to follow
	 * the master. Setting it on every reference rather than only on a
	 * change costs one store and cannot be forgotten at a handover.
	 */
	time_sync_set_delay_us(raft_leader() == LAB_NODE_COCKPIT ?
			       LAB5_DELAY_COCKPIT_US : LAB5_DELAY_STM32_US);
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

/** @brief Step down to follower in a newer term.
 *
 * Raft's one unconditional rule: any message carrying a higher term makes this
 * node a follower in that term, whatever it was doing. Called with the lock
 * held.
 * @param term The newer term.
 */
static void adopt_term(uint32_t term)
{
	state.term = term;
	state.role = RAFT_FOLLOWER;
	state.leader = 0;
	state.voted_for = 0;
	state.votes = 0;
	/*
	 * Anything this node put in the log under the old term may be gone: a
	 * new leader's log wins, and entries written under an earlier term are
	 * overwritten. Re-assert this node's own existence and links through
	 * whoever leads now. Without this a node that was leading when it lost
	 * the term never reappears in the graph, which showed on the bench as a
	 * committed `nodes=CS.` with the drivetrain missing for good.
	 *
	 * Re-reporting is free when nothing changed, because the leader answers
	 * a repeated request without appending a second copy.
	 */
	state.self_reported = false;
	for (int bus = 0; bus < 2; bus++) {
		state.link_down[bus] = true;
	}
}

/** @brief Report whether a candidate's log is at least as current as ours.
 *
 * The up to date test from Raft section 5.4.1, and the reason an election
 * cannot lose a committed entry: a candidate missing entries cannot gather a
 * majority, because every node holding those entries refuses it. On this
 * vehicle that is concrete. An STM32 that was isolated has a short log, and
 * electing it would drop link state the rest of the cluster had agreed on.
 * Called with the lock held.
 * @param last_index Candidate's last log index.
 * @param last_term Term of that entry.
 * @return True when the candidate may be voted for.
 */
static bool log_is_current(uint16_t last_index, uint8_t last_term)
{
	uint32_t mine_index = raft_log_last_index(&state.log);
	uint8_t mine_term = 0;

	if (mine_index != 0 && !raft_log_term_at(&state.log, mine_index, &mine_term)) {
		/* The entry has aged out of the ring; treat our log as unknown
		 * rather than claim to be ahead on evidence we no longer hold.
		 */
		return true;
	}
	if (last_term != mine_term) {
		return last_term > mine_term;
	}
	return last_index >= mine_index;
}

/** @brief Take the leadership, resetting what is known about the followers.
 *
 * A new leader knows nothing about which entries its followers hold, so Raft
 * starts every nextIndex at its own end of the log and walks back on refusal.
 * Called with the lock held.
 */
static void become_leader(void)
{
	state.role = RAFT_LEADER;
	state.leader = LAB_NODE_ID;
	state.last_contact_ms = k_uptime_get();
	state.last_append_ms = 0;
	for (int node = 1; node <= LAB5_CFG_N; node++) {
		state.next_index[node] = raft_log_last_index(&state.log) + 1;
		state.match_index[node] = 0;
		/*
		 * Forget what the previous leader answered. A request still
		 * outstanding when the leadership moved has to be appended once
		 * in the new term, and the old leader's index for it means
		 * nothing here.
		 */
		state.client_answered[node] = false;
	}
	/*
	 * A new leader asserts the cockpit's state itself rather than trusting
	 * what the previous one recorded, which it may never have had time to
	 * commit.
	 */
	state.cockpit_known = false;
}

/** @brief Commit the highest index a majority has stored, in the leader's term.
 *
 * Raft only commits an entry from the leader's own term by counting replicas,
 * which keeps a later leader from committing a predecessor's entry on a count
 * that was never a majority at the time. Called with the lock held.
 * @return True when the commit index advanced.
 */
static bool advance_commit(void)
{
	uint32_t before = state.commit_index;

	for (uint32_t index = raft_log_last_index(&state.log);
	     index > state.commit_index; index--) {
		uint8_t term = 0;
		int stored = 1;

		if (!raft_log_term_at(&state.log, index, &term) ||
		    term != (uint8_t)state.term) {
			continue;
		}
		for (int node = 1; node <= LAB5_CFG_N; node++) {
			if (node != LAB_NODE_ID && state.match_index[node] >= index) {
				stored++;
			}
		}
		if (stored >= LAB5_CFG_MAJORITY) {
			state.commit_index = index;
			break;
		}
	}
	if (state.commit_index == before) {
		return false;
	}
	network_replay(&state.net, &state.log, state.commit_index);
	return true;
}

/** @brief Record whether the cockpit is still heartbeating. Leader only.
 *
 * Part 3 gives COCKPIT_OK and COCKPIT_ERROR to the leader: it appends one when
 * the cockpit's heartbeats resume or stop, and the state machine turns that
 * into the vehicle's failure mode. Only the leader may do it, which is what
 * keeps checkoff step 4's rule of exactly one entry per change: every node can
 * see the cockpit go quiet, but only one of them is entitled to say so.
 *
 * The cockpit cannot report its own death, so when it leads the answer is
 * trivially yes, and when it dies the entry waits for whoever wins the
 * election. That is why checkoff step 6 allows a second for the failure mode to
 * appear but only 100 ms for the motor to brake: braking is local and does not
 * wait for consensus.
 * @param alive True when cockpit heartbeats are fresh.
 */
void raft_report_cockpit(bool alive)
{
	uint8_t event = RAFT_EVENT_NONE;
	uint32_t index = 0;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (state.role == RAFT_LEADER &&
	    (!state.cockpit_known || state.cockpit_alive != alive)) {
		state.cockpit_known = true;
		state.cockpit_alive = alive;
		event = alive ? RAFT_COCKPIT_OK : RAFT_COCKPIT_ERROR;
		index = raft_log_append(&state.log, (uint8_t)state.term, event);
		state.match_index[LAB_NODE_ID] = raft_log_last_index(&state.log);
	}
	k_spin_unlock(&state_lock, key);

	if (event != RAFT_EVENT_NONE && LAB5_CFG_DEBUG) {
		printk("raft: append idx=%u term=%u %s\n", index, raft_term(),
		       raft_event_name(event));
	}
}

/** @brief Report whether the committed log puts the vehicle in failure mode.
 * @return True when the cockpit is recorded as down.
 */
bool raft_failure_mode(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	bool failed = network_failure_mode(&state.net);

	k_spin_unlock(&state_lock, key);
	return failed;
}

/** @brief Send one AppendEntries when leading.
 *
 * One identifier is broadcast to every follower, so the header is built from
 * the **furthest behind** follower's nextIndex. A follower that already holds
 * that entry accepts it idempotently, which is why raft_log_accept() was
 * written that way: on this bus each append arrives two to four times anyway,
 * once per bus plus relayed copies.
 *
 * This is also what lets a stale leader discover it has been replaced. REF
 * carries no term, and a vote only reaches a node that took part in the
 * election, so AppendEntries is the only frame that makes a node that still
 * believes it leads adopt a newer term and stand down.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_append_tx(int64_t now_ms)
{
	struct lab5_append header = {0};
	uint8_t data[8];
	bool send = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (state.role == RAFT_LEADER) {
		uint32_t last = raft_log_last_index(&state.log);
		uint32_t nxt;
		bool behind;

		/*
		 * Aim each append at one follower in turn, not at whichever is
		 * furthest behind. One identifier is broadcast to all of them,
		 * so a header built from the minimum nextIndex is pinned by any
		 * follower that never acknowledges: an unreachable node holds
		 * its position for ever and nothing past it ever replicates. On
		 * the bench, with the cockpit down, the commit index sat at 1
		 * while the log held seven entries.
		 *
		 * Taking turns costs a factor of N in catch-up rate and wastes
		 * the dead node's turns, which is the right trade: the alive
		 * followers keep moving. Every follower accepts an entry it
		 * already holds idempotently, so a turn spent on one does no
		 * harm to the others.
		 */
		do {
			state.append_peer = state.append_peer % LAB5_CFG_N + 1;
		} while (state.append_peer == LAB_NODE_ID);
		nxt = state.next_index[state.append_peer];
		if (nxt < 1) {
			nxt = 1;
		}
		if (nxt > last + 1) {
			nxt = last + 1;
		}
		behind = nxt <= last;
		/* Idle at 100 ms, at the heartbeat rate while replicating. */
		if (now_ms - state.last_append_ms >=
		    (behind ? LAB5_CFG_HEARTBEAT_MS : APPEND_IDLE_MS)) {
			struct raft_entry entry;

			header.term = (uint8_t)state.term;
			header.prev_index = (uint16_t)(nxt - 1);
			(void)raft_log_term_at(&state.log, nxt - 1, &header.prev_term);
			header.commit = (uint16_t)state.commit_index;
			if (raft_log_get(&state.log, nxt, &entry)) {
				header.count = 1;
				/* The entry's own term travels with it. */
				header.entry_term = entry.term;
				header.event = entry.event;
			}
			state.last_append_ms = now_ms;
			send = true;
		}
	}
	k_spin_unlock(&state_lock, key);

	if (!send) {
		return;
	}
	lab5_append_encode(data, &header);
	lab_can_send_lab5(LAB5_APPEND, data);
}

/** @brief Apply a follower's acknowledgement. Leader only.
 * @param data Eight received bytes.
 * @param from Node that acknowledged, taken from the identifier.
 */
void raft_on_ack(const uint8_t data[8], int from)
{
	struct lab5_ack ack = lab5_ack_decode(data);
	char line[NETWORK_PRINT_SIZE];
	bool advanced = false;
	uint32_t committed = 0;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (ack.term > state.term) {
		adopt_term(ack.term);
		k_spin_unlock(&state_lock, key);
		return;
	}
	if (state.role != RAFT_LEADER || from < 1 || from > LAB5_CFG_N ||
	    from == LAB_NODE_ID) {
		k_spin_unlock(&state_lock, key);
		return;
	}
	if (ack.result == RAFT_APPEND_ACCEPTED) {
		if (ack.match_index > state.match_index[from]) {
			state.match_index[from] = ack.match_index;
		}
		state.next_index[from] = state.match_index[from] + 1;
		advanced = advance_commit();
		committed = state.commit_index;
		if (advanced) {
			network_print(&state.net, line, sizeof(line));
		}
	} else if (state.next_index[from] > 1) {
		/* Walk back one entry and try again; the follower is behind. */
		state.next_index[from]--;
	}
	k_spin_unlock(&state_lock, key);

	if (advanced && LAB5_CFG_DEBUG) {
		printk("raft: commit idx=%u %s\n", committed, line);
	}
}

/** @brief Record a client's event when leading, and answer it.
 *
 * Steps 2 and 6 of the handout's write path. Without this an STM32 leader is a
 * time master only: link changes reported while it leads would be answered by
 * nobody and retried for ever.
 * @param data Eight received bytes.
 * @param from Node that asked, taken from the identifier.
 */
void raft_on_client_request(const uint8_t data[8], int from)
{
	struct lab5_client request = lab5_client_decode(data);
	struct lab5_reply reply = {0};
	uint8_t out[8];
	bool answer = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	reply.request = request.request;
	reply.node = (uint8_t)from;
	if (state.role == RAFT_LEADER && from >= 1 && from <= LAB5_CFG_N) {
		uint32_t index;

		if (state.client_answered[from] &&
		    state.client_request[from] == request.request) {
			/*
			 * Already recorded. Answer again with the same index
			 * rather than appending a second copy: appending it
			 * would grow the log without bound, since every
			 * follower then truncates and re-stores on each round.
			 */
			index = state.client_index[from];
		} else {
			index = raft_log_append(&state.log, (uint8_t)state.term,
						request.event);
			state.client_request[from] = request.request;
			state.client_index[from] = (uint16_t)index;
			state.client_answered[from] = true;
		}
		state.match_index[LAB_NODE_ID] = raft_log_last_index(&state.log);
		reply.result = LAB5_REPLY_COMMITTED;
		reply.leader = (uint8_t)LAB_NODE_ID;
		reply.index = (uint16_t)index;
		answer = true;
	} else if (state.role != RAFT_LEADER && state.leader != 0) {
		/* A client that reached a follower is redirected, not ignored. */
		reply.result = LAB5_REPLY_REDIRECT;
		reply.leader = (uint8_t)state.leader;
		answer = true;
	}
	k_spin_unlock(&state_lock, key);

	if (!answer) {
		return;
	}
	lab5_reply_encode(out, &reply);
	lab_can_send_lab5(LAB5_CLIENT_REDIRECT, out);
}

/** @brief Start an election if nothing has been heard from a leader.
 *
 * Called from the monitor task. The timeout is drawn fresh for every round
 * rather than reused, because two nodes that collided once would otherwise
 * collide again on every retry.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_election_tick(int64_t now_ms)
{
	struct lab5_vote_request request = {0};
	uint8_t data[8];
	uint32_t timeout = 0;
	bool stand = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (state.role != RAFT_LEADER &&
	    now_ms - state.last_contact_ms >= (int64_t)state.timeout_ms) {
		state.term++;
		state.role = RAFT_CANDIDATE;
		state.leader = 0;
		state.voted_for = LAB_NODE_ID;
		/* A candidate votes for itself, which is the first of the majority. */
		state.votes = (uint8_t)(1U << (LAB_NODE_ID - 1));
		state.last_contact_ms = now_ms;
		state.timeout_ms = election_timeout_ms(&state.rng, ELECTION_BASE_MS,
						       LAB5_CFG_JITTER_MS);
		request.term = (uint8_t)state.term;
		request.last_index = (uint16_t)raft_log_last_index(&state.log);
		(void)raft_log_term_at(&state.log, request.last_index,
				       &request.last_term);
		timeout = state.timeout_ms;
		stand = true;
	}
	k_spin_unlock(&state_lock, key);

	if (!stand) {
		return;
	}
	lab5_vote_request_encode(data, &request);
	lab_can_send_lab5(lab5_id_for(LAB5_REQ_VOTE_BASE, LAB_NODE_ID), data);
	if (LAB5_CFG_DEBUG) {
		printk("raft: standing term=%u last=%u/%u next timeout=%u ms\n",
		       request.term, request.last_index, request.last_term, timeout);
	}
}

/** @brief Answer a vote request.
 * @param data Eight received bytes.
 * @param from Node that sent it, taken from the identifier.
 */
void raft_on_vote_request(const uint8_t data[8], int from)
{
	struct lab5_vote_request request = lab5_vote_request_decode(data);
	struct lab5_vote vote = {0};
	uint8_t out[8];
	bool current;
	bool stale;
	bool taken;
	int already;
	uint32_t my_term;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (request.term > state.term) {
		adopt_term(request.term);
	}
	current = log_is_current(request.last_index, request.last_term);
	stale = request.term < state.term;
	taken = state.voted_for != 0 && state.voted_for != from;
	already = state.voted_for;
	my_term = state.term;
	vote.term = (uint8_t)state.term;
	vote.candidate = (uint8_t)from;
	vote.granted = !stale && current && !taken;
	if (vote.granted) {
		state.voted_for = from;
		/*
		 * Granting restarts the timer. Without this every node that
		 * voted would stand itself a moment later and the term would
		 * climb on every round without anyone winning.
		 */
		state.last_contact_ms = k_uptime_get();
	}
	k_spin_unlock(&state_lock, key);

	lab5_vote_encode(out, &vote);
	lab_can_send_lab5(lab5_id_for(LAB5_VOTE_BASE, LAB_NODE_ID), out);
	if (LAB5_CFG_DEBUG) {
		/*
		 * Say why a vote was refused. A term that climbs without anyone
		 * winning looks identical from the outside whether the cause is
		 * a split vote, a stale candidate or a log that is behind, and
		 * those want three different fixes.
		 */
		printk("raft: vote %s for %s term=%u mine=%u%s%s%s\n",
		       vote.granted ? "granted" : "refused", lab_node_name(from),
		       request.term, my_term, stale ? " stale" : "",
		       !current ? " log-behind" : "",
		       taken ? " already-voted" : "");
		if (taken) {
			printk("raft:   held by %s\n", lab_node_name(already));
		}
	}
}

/** @brief Count a vote and take the leadership when a majority is reached.
 * @param data Eight received bytes.
 * @param from Node that voted, taken from the identifier.
 */
void raft_on_vote(const uint8_t data[8], int from)
{
	struct lab5_vote vote = lab5_vote_decode(data);
	bool won = false;
	uint32_t term = 0;
	int count = 0;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (vote.term > state.term) {
		adopt_term(vote.term);
		k_spin_unlock(&state_lock, key);
		return;
	}
	/*
	 * Three tests, and each one has failed in a real system: the vote must
	 * be for this term, it must be addressed to this candidate because two
	 * can stand at once, and this node must still be standing.
	 */
	if (state.role == RAFT_CANDIDATE && vote.term == state.term &&
	    vote.candidate == LAB_NODE_ID && vote.granted) {
		state.votes |= (uint8_t)(1U << (from - 1));
		for (int node = 0; node < LAB5_CFG_N; node++) {
			count += (state.votes >> node) & 1U;
		}
		if (count >= LAB5_CFG_MAJORITY) {
			become_leader();
			won = true;
			term = state.term;
		}
	}
	k_spin_unlock(&state_lock, key);

	if (!won) {
		return;
	}
	/*
	 * The time master moves with the leadership. Nothing is reset for a
	 * node that was already synchronized: the estimate it has maintained
	 * all along is what it now stamps into REF, which is why a handover
	 * does not step the timeline. A node elected without ever having
	 * synchronized has no estimate to keep, so it declares itself the
	 * origin instead; otherwise the lock never happens and it emits no
	 * pulse per second at all.
	 */
	time_sync_assume_master(LAB5_EPOCH_US + (uint64_t)k_uptime_get() * 1000U,
				pps_now());
	if (LAB5_CFG_DEBUG) {
		printk("raft: LEADER term=%u with %d of %d votes\n", term, count,
		       LAB5_CFG_N);
	}
}

/** @brief Stamp and send one reference frame. Leader only.
 *
 * The clock stamped here is this node's global estimate, not its local uptime.
 * A new leader has been tracking the shared timeline as a follower, so
 * continuing to report it is what keeps the handover free of a step; checkoff
 * step 6 allows no more than 100 microseconds.
 * @return True when a frame was sent.
 */
bool raft_ref_tx(void)
{
	static uint16_t cycle;
	uint8_t data[8];
	uint64_t master_us;

	if (raft_role() != RAFT_LEADER) {
		return false;
	}
	master_us = time_sync_global_us(pps_now());
	if (master_us == 0) {
		/* Never synchronized: fall back to local time so the cluster
		 * still gets a timeline, just not a continuous one.
		 */
		master_us = LAB5_EPOCH_US + (uint64_t)k_uptime_get() * 1000U;
	}
	lab5_ref_encode(data, master_us, cycle++, LAB_NODE_ID);
	lab_can_send_lab5(LAB5_REF, data);
	return true;
}

/** @brief Age out the time master when references stop arriving.
 * @param now_ms Current uptime in milliseconds.
 */
void raft_agent_tick(int64_t now_ms)
{
	bool lost = false;
	bool leading;
	bool synced;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	if (state.synced && state.role != RAFT_LEADER &&
	    now_ms - state.last_ref_ms >= SYNC_TIMEOUT_MS) {
		state.synced = false;
		/* Keep the counter: the next REF is a new master, not a gap. */
		state.have_cycle = false;
		lost = true;
	}
	leading = state.role == RAFT_LEADER;
	synced = state.synced;
	k_spin_unlock(&state_lock, key);

	/*
	 * Drive both indicators from the role on every pass rather than at each
	 * transition. Role changes happen on five different paths now, and one
	 * that forgot an indicator would leave LEADER lit on a node that had
	 * stepped down, which is exactly what checkoff steps 6 and 8 read.
	 *
	 * A leader is its own time master, so SYNC is lit for it by definition;
	 * it receives no reference to keep the flag fresh.
	 */
	lab_io_set(LAB_LEADER, leading);
	lab_io_set(LAB_SYNC, leading || synced);
	if (!lost) {
		return;
	}
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
	bool report_reject = false;
	enum raft_append_result result;
	char line[NETWORK_PRINT_SIZE];
	int64_t now = k_uptime_get();

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	/*
	 * A leader must ignore an append at or below its own term. Both buses
	 * carry every frame and a relay hop returns a leader's own append on
	 * its other bus, so this runs constantly. Applying it means a leader
	 * running the consistency check against its own log: once nextIndex has
	 * walked down to 1 the append carries the leader's own first entry
	 * stamped with the current term, which does not match the older term
	 * actually stored there, so the leader truncates its own log to empty
	 * and re-appends that one entry. Every follower then re-stores index 1
	 * for ever. A *higher* term is a different matter and falls through,
	 * since standing down is the whole point of that case.
	 */
	if (state.role == RAFT_LEADER && header.term <= state.term) {
		k_spin_unlock(&state_lock, key);
		return;
	}
	/* A newer term means a new leader; adopt it and follow. */
	if (header.term > state.term) {
		adopt_term(header.term);
	} else if (header.term == state.term && state.role == RAFT_CANDIDATE) {
		/* Someone else won this term. Stop standing. */
		state.role = RAFT_FOLLOWER;
		state.votes = 0;
	}
	/* Contact from a leader at or above our term holds the election off. */
	if (header.term >= state.term) {
		state.last_contact_ms = now;
	}
	uint32_t before = raft_log_last_index(&state.log);

	result = raft_log_accept(&state.log, (uint8_t)state.term, header.term,
				 header.prev_index, header.prev_term, header.count,
				 header.entry_term, header.event, &match);
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
	/*
	 * Rate limit the refusal line. This runs in the dispatch thread, and a
	 * console line at 115200 baud takes about four milliseconds, so a
	 * refusal printed on every append leaves that thread unable to drain
	 * the receive queue. A leader whose log has diverged refuses on every
	 * round, which is exactly when the queue most needs draining.
	 */
	if (result != RAFT_APPEND_ACCEPTED) {
		report_reject = now - state.last_reject_report_ms >= GAP_REPORT_MS;
		if (report_reject) {
			state.last_reject_report_ms = now;
		}
	}
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
	} else if (result != RAFT_APPEND_ACCEPTED && report_reject) {
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
 * @param retry True when this repeats an unanswered request.
 */
static void send_client_request(uint8_t event, uint16_t request, bool retry)
{
	struct lab5_client payload = {.event = event, .request = request};
	uint8_t data[8];

	lab5_client_encode(data, &payload);
	lab_can_send_lab5(lab5_id_for(LAB5_CLIENT_REQ_BASE, LAB_NODE_ID), data);
	/* Retries repeat every CLIENT_RETRY_MS until a leader answers, so only
	 * the first attempt prints; otherwise a cluster with no leader fills the
	 * console.
	 */
	if (LAB5_CFG_DEBUG && !retry) {
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
	uint32_t local = 0;
	uint8_t recorded = RAFT_EVENT_NONE;
	int reported_bus = -1;
	bool reported_down = false;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	/*
	 * Self healing: if the committed graph does not show what this node
	 * knows about itself, say it again. Entries this node wrote can vanish
	 * without any term change, because a plain consistency mismatch
	 * truncates the tail, so catching each path that invalidates them one
	 * at a time does not converge. Comparing against the committed graph
	 * does: whatever the cause, the disagreement is visible here and
	 * repairs itself within one debounce. On the bench a node that had been
	 * truncated to a single entry led the cluster while absent from its own
	 * graph, as nodes=CS. with the drivetrain leading.
	 *
	 * Re-reporting costs nothing when nothing changed, because the leader
	 * answers a repeated request without appending a second copy.
	 */
	if (!network_node_up(&state.net, LAB_NODE_ID)) {
		state.self_reported = false;
	}
	for (int bus = 0; bus < 2; bus++) {
		if (!state.link_down[bus] &&
		    !network_link_up(&state.net, LAB_NODE_ID, bus + 1)) {
			state.link_down[bus] = true;
		}
	}
	/*
	 * This node's own existence, reported before its links. D15 says the
	 * owner reports its own links; the same argument applies to the node
	 * itself, and nothing else can do it: node-up entries used to come only
	 * from the cockpit appending them directly while it led, so under any
	 * other leader no node was ever marked up. A link whose node is not up
	 * does not render, by design, so the whole graph stayed empty.
	 */
	if (!state.self_reported && state.pending_event == RAFT_EVENT_NONE) {
		state.self_reported = true;
		state.pending_event = raft_event_node(LAB_NODE_ID, true);
		state.pending_request = ++state.next_request;
		state.pending_sent_ms = now_ms;
		event = state.pending_event;
		request = state.pending_request;
	}
	for (int bus = 0; bus < 2 && event == RAFT_EVENT_NONE; bus++) {
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
		/*
		 * Settled and different: one real change to report. Only record it
		 * as reported once it has actually been queued. A node carries one
		 * pending request at a time, and both links settle together at
		 * boot, so marking the change handled regardless would drop the
		 * second link's report and never retry it.
		 */
		if (state.pending_event != RAFT_EVENT_NONE) {
			continue;
		}
		state.link_down[bus] = down;
		reported_bus = bus;
		reported_down = down;
		state.pending_event = raft_event_link(LAB_NODE_ID, bus + 1, !down);
		state.pending_request = ++state.next_request;
		state.pending_sent_ms = now_ms;
		event = state.pending_event;
		request = state.pending_request;
	}
	if (event == RAFT_EVENT_NONE && state.pending_event != RAFT_EVENT_NONE &&
	    now_ms - state.pending_sent_ms >= CLIENT_RETRY_MS) {
		/* The leader never acted on it: ask again rather than drop it. */
		state.pending_sent_ms = now_ms;
		event = state.pending_event;
		request = state.pending_request;
		retry = true;
	}
	if (event != RAFT_EVENT_NONE && state.role == RAFT_LEADER) {
		/*
		 * This node is the leader, so it records its own report rather
		 * than transmitting it. A node does not receive its own frames,
		 * so a request put on the bus by the leader never reaches the
		 * leader's own handler: a follower picks it up, sees it is not
		 * leading, and correctly redirects to the node that sent it.
		 * On the bench that produced an endless "redirected to
		 * steering, retrying req=1" on steering's own console while
		 * steering was the leader.
		 */
		local = raft_log_append(&state.log, (uint8_t)state.term, event);
		recorded = event;
		state.match_index[LAB_NODE_ID] = raft_log_last_index(&state.log);
		state.pending_event = RAFT_EVENT_NONE;
		event = RAFT_EVENT_NONE;
	}
	k_spin_unlock(&state_lock, key);

	if (reported_bus >= 0 && LAB5_CFG_DEBUG) {
		int can_state;
		int tx_errors;
		int rx_errors;

		/*
		 * Print what the controller actually saw. A link reported down
		 * is either a real electrical fault or a threshold set too
		 * tight, and the counters separate the two without a scope: the
		 * transmit counter rises by eight per failed transmission, so a
		 * value near the limit is a dozen consecutive failures rather
		 * than noise.
		 */
		lab_can_link_state(reported_bus, &can_state, &tx_errors, &rx_errors);
		printk("raft: link %c %s state=%d tec=%d rec=%d\n",
		       reported_bus == 0 ? 'A' : 'B', reported_down ? "DOWN" : "UP",
		       can_state, tx_errors, rx_errors);
	}
	if (local != 0 && LAB5_CFG_DEBUG) {
		printk("raft: append idx=%u term=%u %s\n", local, raft_term(),
		       raft_event_name(recorded));
	}
	if (event != RAFT_EVENT_NONE) {
		send_client_request(event, request, retry);
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

	/* The target node matters: every reply shares one identifier and clients
	 * number requests independently, so ids collide across nodes routinely.
	 */
	if (reply.node == LAB_NODE_ID && state.pending_event != RAFT_EVENT_NONE &&
	    reply.request == state.pending_request) {
		matched = true;
		if (!redirect) {
			/* Step 6: the leader acknowledged the write. */
			state.pending_event = RAFT_EVENT_NONE;
		}
	}
	if (reply.node == LAB_NODE_ID && redirect && reply.leader != 0) {
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
