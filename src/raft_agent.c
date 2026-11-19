/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file raft_agent.c
 * @brief Own the Raft role state and the receive side of REF.
 */
#include "raft_agent.h"
#include "cluster_config.h"
#include "lab_io.h"
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

/** @brief Term every node adopts when a pre-defined leader is configured. */
#define BOOT_TERM 1

static struct {
	enum raft_role role;
	uint32_t term;
	int leader;
	bool synced;
	bool have_cycle;
	uint16_t cycle;
	uint32_t missed;
	struct lab5_ref last;
	uint64_t last_local_us;
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
	bool first;

	k_spinlock_key_t key = k_spin_lock(&state_lock);

	/* A gap of more than one in the counter means references were lost. */
	if (state.have_cycle) {
		uint16_t step = (uint16_t)(ref.cycle - state.cycle);

		if (step > 1) {
			state.missed += (uint32_t)(step - 1);
		}
	}
	first = !state.synced;
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
