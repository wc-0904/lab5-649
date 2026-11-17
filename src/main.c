/* SPDX-License-Identifier: Apache-2.0 */
/** @file main.c
 * @brief Start the task set and report the identity and configuration of this node.
 */
#include "cluster_config.h"
#include "lab_io.h"
#include "lab_protocol.h"
#include "lab_tasks.h"
#include <zephyr/sys/printk.h>

/** @brief Milliseconds each Lab 5 output is held high during the boot pin walk. */
#define SELFTEST_STEP_MS 250

/** @brief Print identity, configuration and the Lab 5 identifier allocation.
 *
 * The configuration hash must read the same on all three consoles. A node that
 * differs was flashed or started from a stale config/cluster.conf.
 */
static void banner(void)
{
	printk("\n=== Lab 5 node %d (%s) ===\n", LAB_NODE_ID, lab_node_name(LAB_NODE_ID));
	printk("config  hash=%s N=%d majority=%d leader=%s debug=%d seed=%d\n",
	       LAB5_CFG_HASH, LAB5_CFG_N, LAB5_CFG_MAJORITY,
	       LAB5_CFG_DEFAULT_LEADER_NAME, LAB5_CFG_DEBUG, LAB5_CFG_ELECTION_SEED);
	printk("timing  heartbeat=%d ms missed=%d election=%d..%d ms log_size=%d\n",
	       LAB5_CFG_HEARTBEAT_MS, LAB5_CFG_MISSED_HEARTBEATS,
	       LAB5_CFG_ELECTION_MIN_MS, LAB5_CFG_ELECTION_MAX_MS, LAB5_CFG_LOG_SIZE);
	printk("can     REF=0x%03x APPEND=0x%03x ACK=0x%03x REQ_VOTE=0x%03x\n",
	       LAB5_REF, LAB5_APPEND, lab5_id_for(LAB5_APPEND_ACK_BASE, LAB_NODE_ID),
	       lab5_id_for(LAB5_REQ_VOTE_BASE, LAB_NODE_ID));
	printk("can     VOTE=0x%03x CLIENT=0x%03x REDIRECT=0x%03x filter=0x%03x/0x%03x\n",
	       lab5_id_for(LAB5_VOTE_BASE, LAB_NODE_ID),
	       lab5_id_for(LAB5_CLIENT_REQ_BASE, LAB_NODE_ID), LAB5_CLIENT_REDIRECT,
	       LAB5_FILTER_ID, LAB5_FILTER_MASK);
}

/** @brief Report identity, optionally walk the new pins, then start the tasks.
 * @return Initialization status from lab_tasks_start().
 */
int main(void)
{
	banner();
	printk("NO physical actuators: simulated PID, dummy FORCE\n");
#if LAB_PIN_SELFTEST
	printk("pin walk: LEADER, SYNC, REF_RX, HZ, PPS at %d ms, twice\n",
	       SELFTEST_STEP_MS);
	lab_io_selftest(SELFTEST_STEP_MS);
#endif
	int rc = lab_tasks_start();

	printk("Measurement mode: %d (0 = normal)\n", LAB_MEASURE_MODE);
	printk("Task startup: %d; relay node=%d\n", rc, LAB_NODE_ID);
	return rc;
}
