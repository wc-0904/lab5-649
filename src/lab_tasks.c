/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file lab_tasks.c
 * @brief Task set with simulated PID feedback and disconnected PWM test outputs.
 */
#include "lab_tasks.h"
#include "lab_can.h"
#include "lab_io.h"
#include "lab_protocol.h"
#include "raft_agent.h"
#include "time_sync.h"
#include "pps.h"
#include "cluster_config.h"
#include <zephyr/drivers/watchdog.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/printk.h>
#include <string.h>

/** @brief Per-thread stack budget; must be checked on the target. */
#define LAB_STACK_SIZE 1536
/** @brief Number of independent task entries, including maintenance/diagnostics. */
#define LAB_TASK_COUNT 11
/** @brief Error state assumes full braking, represented by zero PWM and DIR high. */
#define MOTOR_PERIOD_NS 50000U
/** @brief Interval between drift reports, milliseconds.
 *
 * One second, which lines up with the PPS edge P7 adds, so the two console
 * lines describe the same instant and can be read against each other.
 */
#define DRIFT_REPORT_MS 1000
/** @brief Standard 20 ms servo period; no servo is attached. */
#define SERVO_PERIOD_NS 20000000U

/** @brief Task slots. Anything the watchdog must see progress on comes before
 * DIAGNOSTICS, because that is the bound the health loop uses. DRIFT sits after
 * it deliberately: it runs once a second while the health loop runs ten times a
 * second, so including it would trip the watchdog on every pass.
 */
enum task_id { DISPATCH, OUTPUT, BLINK, TEST, PID, HEARTBEAT, MONITOR, FORCE,
	RELAY, DIAGNOSTICS, DRIFT };
struct shared_state {
	struct lab_control command;
	int64_t ctrl_at;
	int64_t heartbeat[3][2];
	bool seen[3][2];
	bool have_ctrl;
	bool local_fail;
	bool remote_fail[3];
	int64_t fault_at[3];
	bool error;
	bool degraded[2];
	uint32_t version;
};
static struct shared_state state = {.error = true, .degraded = {true, true}};
static struct k_spinlock state_lock;
static struct k_spinlock stats_lock;
static uint32_t maximum_us[LAB_TASK_COUNT];
static uint32_t executions[LAB_TASK_COUNT];
static atomic_t progress[LAB_TASK_COUNT];
static atomic_t fault_reason;
static atomic_t button_time;
static atomic_t button_pending;
static atomic_t malformed;
static atomic_t duplicates;
static struct k_thread threads[LAB_TASK_COUNT];
K_THREAD_STACK_ARRAY_DEFINE(stacks, LAB_TASK_COUNT, LAB_STACK_SIZE);
K_SEM_DEFINE(output_event, 0, 1);
K_SEM_DEFINE(blink_event, 0, 1);
K_SEM_DEFINE(test_event, 0, 1);
K_MUTEX_DEFINE(output_lock);
/** @brief Relay queue holds a bounded catalog burst; dispatch never waits. */
K_MSGQ_DEFINE(relay_queue, sizeof(struct lab_rx), 32, 4);
/** @brief Ordered normal blink commands; overflow becomes a latched local failure. */
K_MSGQ_DEFINE(blink_queue, 2, 16, 1);
static const struct device *const watchdog = DEVICE_DT_GET(DT_NODELABEL(iwdg));
static int watchdog_channel;
static int32_t simulated_speed;
static int32_t duty;
static int32_t integral;
static int32_t previous_error;

/** @brief Capture coherent state without blocking on a mutex.
 * @return Snapshot.
 */
static struct shared_state snapshot(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	struct shared_state result = state;
	k_spin_unlock(&state_lock, key);

	return result;
}

/** @brief Begin optional GPIO envelope and elapsed-time observation.
 * @param id Task index.
 * @return Cycle counter at entry.
 */
static uint32_t begin(enum task_id id)
{
	if (id == LAB_TRACE_TASK) {
		lab_io_set(LAB_TRACE, true);
	}
	return k_cycle_get_32();
}

/** @brief Record elapsed time including preemption, not a pure WCET measurement.
 * @param id Task index.
 * @param start Entry cycle counter.
 */
static void finish(enum task_id id, uint32_t start)
{
	uint32_t elapsed = k_cyc_to_us_ceil32(k_cycle_get_32() - start);
	if (id == LAB_TRACE_TASK) {

		lab_io_set(LAB_TRACE, false);
	}
	k_spinlock_key_t key = k_spin_lock(&stats_lock);
	maximum_us[id] = MAX(maximum_us[id], elapsed);

	executions[id]++;
	k_spin_unlock(&stats_lock, key);
	atomic_inc(&progress[id]);
}

/** @brief Latch local failure immediately and wake output/fault tasks.
 * @param reason Catalog reason.
 */
static void fail(enum lab_reason reason)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	if (state.local_fail) {
		k_spin_unlock(&state_lock, key);
		return;
	}
	state.local_fail = true;

	state.error = true;
	state.version++;
	k_spin_unlock(&state_lock, key);
	atomic_cas(&fault_reason, 0, reason);
	k_sem_give(&output_event);
	k_sem_give(&blink_event);
	k_sem_give(&test_event);
	if (LAB5_CFG_DEBUG) {
		static const char *const names[] = {"?", "TEST", "RANGE",
			"QUEUE", "IO"};

		/*
		 * A latched local failure takes this node off the air: it stops
		 * heartbeating on both buses and holds the error state for
		 * good. Until now nothing said so, and the ERR pin is only
		 * wired on N2, so on the other node it was invisible. Printed
		 * once, because fail() returns early when already latched.
		 */
		printk("raft: LOCAL FAIL reason=%s drops=%u bad=%ld\n",
		       names[reason <= LAB_IO ? reason : 0], lab_can_drops(),
		       (long)atomic_get(&malformed));
	}
}

/** @brief Debounce button edges without sleeping in the interrupt. */
static void button_pressed(void)
{
	uint32_t now = k_uptime_get_32();
	uint32_t last = (uint32_t)atomic_get(&button_time);
	if (last == 0 || now - last >= 50) {

		atomic_set(&button_time, now);
		atomic_set(&button_pending, 1);
		k_sem_give(&test_event);
	}
}

/** @brief Suppress identical direct/relay copies on either bus within 200 ms.
 * @param rx Validated frame.
 * @return True for a recent identical copy.
 */
static bool duplicate(const struct lab_rx *rx)
{
	static struct {
		int64_t at;
		uint8_t bus;
		bool valid;
		uint8_t data[8];
	} cache[10][16];
	int slot = lab_is_fault(rx->frame.id) ? 6 + rx->frame.data[1] :
		lab_slot(rx->frame.id);
	int seq = rx->frame.data[0] & 15;
	bool result = cache[slot][seq].valid &&
		rx->time_ms - cache[slot][seq].at < LAB_DEDUP_MS &&
		memcmp(cache[slot][seq].data, rx->frame.data, 8) == 0;
	if (!result) {
		cache[slot][seq].at = rx->time_ms;
		cache[slot][seq].bus = rx->bus;
		cache[slot][seq].valid = true;
		memcpy(cache[slot][seq].data, rx->frame.data, 8);
	}
	return result;
}

/** @brief Put one relayed copy of a Lab 5 frame on the other bus.
 *
 * Checkoff step 5 pulls the cockpit off one bus and the drivetrain off the
 * other, so the two cannot hear each other at all and every frame between them
 * has to cross at the node that still holds both. Lab 3's relay only ever
 * carried catalog traffic; consensus and time frames were dropped, which is why
 * that scenario could not work before.
 *
 * Relaying here is a **reflex, not a routing decision** (TASKS.md D25). A node
 * holding both links forwards one copy and does not consult its routing table
 * to decide whether anyone needs it. That is deliberate: the handout's section
 * 5.2 warns that after a second link failure the tables take several rounds to
 * settle, and traffic has to survive that window. A reflex has no window,
 * because there is nothing to converge. The routing table still exists and is
 * still built from `getNetwork()`, but it informs the client's view of
 * reachability rather than gating delivery.
 *
 * The cost is one duplicate per frame while every link is healthy. Every Lab 5
 * handler is already idempotent, because each frame arrives twice over the two
 * buses in any case, so a third copy changes nothing but bus load. At 500
 * kbit/s a reference is about 256 microseconds on the wire against a 20 ms
 * period, so there is room.
 * @param rx Received frame, with the bus it arrived on.
 * @param id The frame's original identifier, relay flag already cleared.
 */
static void relay_lab5(const struct lab_rx *rx, uint32_t id)
{
	struct lab_rx copy;

	/* One hop only: a copy is never copied again. */
	if (lab5_is_relayed(rx->frame.id)) {
		return;
	}
	/* Only a node still holding both links can bridge them. */
	if (!lab_can_link_healthy(0) || !lab_can_link_healthy(1)) {
		return;
	}
	/*
	 * Nothing to bridge while every node holds both links: the originator
	 * already put the frame on both buses, so a copy reaches nobody new and
	 * only consumes bus and transmit queue capacity. On the bench that
	 * waste pushed Lab 3's CTRL out of the queues and left a node in the
	 * error state while consensus reported everything healthy.
	 */
	if (!raft_relay_needed()) {
		return;
	}
	copy = *rx;
	copy.frame.id = lab5_relay_id(id, LAB_NODE_ID);
	if (k_msgq_put(&relay_queue, &copy, K_NO_WAIT) != 0) {
		atomic_inc(&duplicates);
	}
}

/** @brief Validate, track per-bus health before dedup, and publish events.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void dispatch_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	struct lab_rx rx;
	while (true) {

		if (lab_can_receive(&rx, K_MSEC(20)) != 0) {
			atomic_inc(&progress[DISPATCH]);
			continue;
		}
		uint32_t start = begin(DISPATCH);
		lab_io_toggle(rx.bus == 0 ? LAB_RX_A : LAB_RX_B);

		/*
		 * Strip the relay flag and relayer only when it is actually
		 * set. The relay node field shares bits 8 and 9 with the Lab 3
		 * catalog's own numbering: HB1/HB2/HB3 (0x100..0x102) and FORCE
		 * (0x300) use exactly those bits to mean something else.
		 * Stripping them unconditionally from every received identifier
		 * silently collapsed HB1, BLINK and FORCE onto the same value,
		 * 0x0, which made every heartbeat and FORCE frame invisible to
		 * dispatch: peer freshness never updated, DEG latched solid, and
		 * every node sat in the error state while the consensus layer
		 * kept reporting a complete, healthy graph. Lab 3 traffic never
		 * carries the relay flag, bit 10, so gating on it leaves catalog
		 * identifiers untouched.
		 */
		uint32_t id = lab5_is_relayed(rx.frame.id) ?
			lab5_origin_id(rx.frame.id) : rx.frame.id;
		/*
		 * Lab 5 traffic is not Lab 3 catalog traffic: lab_valid() would
		 * reject it and inflate the malformed counter. REF goes to the
		 * agent, which owns the REF_RX marker and SYNC. rx.cycles, not
		 * the uptime, is the sync instant: it was captured in the
		 * receive callback, before the queue wait this thread has just
		 * finished (TASKS.md D17).
		 *
		 * A relayed copy dispatches on its original identifier, so every
		 * handler below is unaware of how the frame arrived. Only the
		 * decision to relay it onward looks at the flag.
		 */
		if (id == LAB5_REF || lab5_is_consensus(id)) {
			relay_lab5(&rx, id);
		}
		if (id == LAB5_REF) {
			/*
			 * A relayed copy carries extra, real transit delay the
			 * per-master constant was never calibrated to include;
			 * raft_on_ref() adds for it separately (TASKS.md D18).
			 */
			raft_on_ref(rx.frame.data, (uint64_t)k_uptime_get() * 1000U,
				    rx.cycles, lab5_is_relayed(rx.frame.id));
			finish(DISPATCH, start);
			continue;
		}
		if (id == LAB5_APPEND) {
			raft_on_append(rx.frame.data);
			finish(DISPATCH, start);
			continue;
		}
		if (id == LAB5_CLIENT_REDIRECT) {
			raft_on_reply(rx.frame.data);
			finish(DISPATCH, start);
			continue;
		}
		if (lab5_is_consensus(id)) {
			/*
			 * Votes are addressed by sender, so the identifier
			 * carries who is standing or who answered. Everything
			 * else in the range belongs to another node.
			 */
			for (int node = 1; node <= LAB5_CFG_N; node++) {
				if (node == LAB_NODE_ID) {
					continue;
				}
				if (id == lab5_id_for(LAB5_REQ_VOTE_BASE, node)) {
					raft_heard_from(node, rx.time_ms);
					raft_on_vote_request(rx.frame.data, node);
					break;
				}
				if (id == lab5_id_for(LAB5_VOTE_BASE, node)) {
					raft_heard_from(node, rx.time_ms);
					raft_on_vote(rx.frame.data, node);
					break;
				}
				if (id == lab5_id_for(LAB5_APPEND_ACK_BASE, node)) {
					raft_heard_from(node, rx.time_ms);
					raft_on_ack(rx.frame.data, node);
					break;
				}
				if (id == lab5_id_for(LAB5_CLIENT_REQ_BASE, node)) {
					raft_heard_from(node, rx.time_ms);
					raft_on_client_request(rx.frame.data, node);
					break;
				}
			}
			finish(DISPATCH, start);
			continue;
		}
		if (rx.frame.dlc != 8 || rx.frame.flags != 0 || !lab_valid(id, rx.frame.data)) {

			atomic_inc(&malformed);
			if (id == LAB_CTRL) {
				fail(LAB_RANGE);
			}
			finish(DISPATCH, start);
			continue;
		}
		/* A remote bridge may return our own frame: never consume or re-forward it. */
		if (lab_origin(id, rx.frame.data) == LAB_NODE_ID) {
			finish(DISPATCH, start);
			continue;
		}
		if (id >= LAB_HB1 && id <= LAB_HB3) {
			int peer = id - LAB_HB1;
			k_spinlock_key_t key = k_spin_lock(&state_lock);
			state.heartbeat[peer][rx.bus] = rx.time_ms;

			state.seen[peer][rx.bus] = true;
			k_spin_unlock(&state_lock, key);
			/* After the unlock: the agent has its own lock, never nested. */
			raft_heard_from(peer + 1, rx.time_ms);
		}
		if (duplicate(&rx)) {
			atomic_inc(&duplicates);
			finish(DISPATCH, start);
			continue;
		}
		if (LAB_MEASURE_MODE != 0) {
			if (id == LAB_CTRL && LAB_MEASURE_MODE == 2) {
				k_spinlock_key_t key = k_spin_lock(&state_lock);
				state.command = lab_decode(rx.frame.data);
				state.error = false;
				state.version++;
				k_spin_unlock(&state_lock, key);
				k_sem_give(&output_event);
			} else if (id == LAB_CTRL && LAB_MEASURE_MODE == 3) {
				(void)k_msgq_put(&relay_queue, &rx, K_NO_WAIT);
			}
			finish(DISPATCH, start);
			continue;
		}
		/* Every non-origin node forwards a first-seen message once. */
		if (k_msgq_put(&relay_queue, &rx, K_NO_WAIT) != 0) {
			fail(LAB_QUEUE);
		}
		if (id == LAB_CTRL) {
			k_spinlock_key_t key = k_spin_lock(&state_lock);
			state.command = lab_decode(rx.frame.data);

			state.ctrl_at = rx.time_ms;
			state.have_ctrl = true;
			state.version++;
			k_spin_unlock(&state_lock, key);
			k_sem_give(&output_event);
		} else if (id == LAB_BLINK) {
			if (k_msgq_put(&blink_queue, &rx.frame.data[1], K_NO_WAIT) != 0) {
				fail(LAB_QUEUE);
			}
			k_sem_give(&blink_event);
		} else if (lab_is_fault(id)) {
			k_spinlock_key_t key = k_spin_lock(&state_lock);
			state.remote_fail[rx.frame.data[1] - 1] = true;

			state.fault_at[rx.frame.data[1] - 1] = rx.time_ms;
			state.error = true;
			state.version++;
			k_spin_unlock(&state_lock, key);
			k_sem_give(&output_event);
			k_sem_give(&blink_event);
		}
		finish(DISPATCH, start);
	}
}

/** @brief Forward each first-seen logical frame once to its opposite bus.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void relay_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	struct lab_rx rx;

	while (true) {
		if (k_msgq_get(&relay_queue, &rx, K_MSEC(20)) != 0) {
			atomic_inc(&progress[RELAY]);
			continue;
		}
		uint32_t start = begin(RELAY);

		if (!snapshot().local_fail && k_uptime_get() - rx.time_ms < 10) {
			(void)lab_can_forward(&rx.frame, 1 - rx.bus, rx.time_ms);
		}
		finish(RELAY, start);
	}
}

/** @brief React to CTRL/faults and own immediate PWM updates.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void output_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	while (true) {
		if (k_sem_take(&output_event, K_MSEC(20)) != 0) {
			atomic_inc(&progress[OUTPUT]);
			continue;
		}
		uint32_t start = begin(OUTPUT);
		k_mutex_lock(&output_lock, K_FOREVER);

		struct shared_state s = snapshot();
		if (LAB_NODE_ID == 2) {

			uint32_t pulse = 1500000 + (s.error ? 0 : s.command.steer * 500);
			(void)lab_io_pwm(SERVO_PERIOD_NS, pulse);
		} else {
			bool braking = s.error || s.command.brake > 0;
			duty = braking ? 0 : s.command.throttle * 10;

			if (braking) {
				integral = 0;
				previous_error = 0;
			}
			lab_io_set(LAB_DIR, braking);
			(void)lab_io_pwm(MOTOR_PERIOD_NS, MOTOR_PERIOD_NS * duty / 1000);
		}
		lab_io_set(LAB_ERR, s.error);
		k_mutex_unlock(&output_lock);
		finish(OUTPUT, start);
	}
}

/** @brief Half period of the normal blinker, microseconds.
 *
 * 2 Hz means the lamp completes two on-off cycles a second, so the period is
 * 500 ms and an edge falls every 250 ms. Lab 3's LAB_BLINK_MS of 500 is the
 * same figure expressed as a period.
 */
#define BLINK_HALF_US 250000U
/** @brief Half period of the failure mode hazard, microseconds.
 *
 * 4 Hz, which checkoff step 6 asks for once the cockpit is gone. The rate is
 * the signal: 2 Hz means the vehicle is in the error state with the cockpit
 * still alive, 4 Hz means the cockpit itself is down and an STM32 is leading.
 * Step 7 watches it return to 2 Hz when the Pi rejoins.
 */
#define HAZARD_FAST_HALF_US 125000U
/** @brief Longest the blinker may sleep when no edge is near, milliseconds.
 *
 * Far from an edge the task waits on the event semaphore so an error state
 * change is acted on promptly; near one it sleeps to the deadline instead.
 */
#define BLINK_COARSE_MS 20

/** @brief Read the timeline the blinkers are scheduled on.
 *
 * Falls back to local uptime when no master has been seen, so a node on the
 * bench on its own still blinks; it simply will not agree with anyone.
 * @return Microseconds on the chosen timeline.
 */
static uint64_t blink_clock_us(void)
{
	if (time_sync_locked()) {
		uint64_t global = time_sync_global_us(pps_now());

		if (global != 0) {
			return global;
		}
	}
	return (uint64_t)k_uptime_get() * 1000U;
}

/** @brief Derive the blink phase from the clock alone.
 *
 * This is the whole of D23. The phase is a pure function of global time, so
 * every node computes the same value at the same instant with no message and no
 * agreement protocol: a node can join, reboot or be cut off and still come up
 * in step. Both half periods divide a second exactly, so an edge coincides with
 * the PPS edge and the two can be scoped against each other.
 * @param clock_us Current time on the blink timeline.
 * @param half_us Half period.
 * @return True for the lit half of the cycle.
 */
static bool blink_phase(uint64_t clock_us, uint32_t half_us)
{
	return ((clock_us / half_us) & 1U) != 0;
}

/** @brief Microseconds remaining until the phase next changes.
 * @param clock_us Current time on the blink timeline.
 * @param half_us Half period.
 * @return Microseconds to the next edge, never zero.
 */
static uint32_t blink_until_edge_us(uint64_t clock_us, uint32_t half_us)
{
	return half_us - (uint32_t)(clock_us % half_us);
}

/** @brief Apply master edges; error hazards use an explicit 250 ms half-period.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void blink_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	uint8_t value[2] = {0};

	while (true) {
		uint32_t start = begin(BLINK);
		struct shared_state s = snapshot();
		uint64_t clock_us = blink_clock_us();
		/*
		 * Hazards run fast only when the cockpit itself is gone, which
		 * is what the leader identity tells us. An error state with the
		 * cockpit still leading stays at 2 Hz.
		 */
		uint32_t half_us = (s.error && raft_leader() != LAB_NODE_COCKPIT) ?
			HAZARD_FAST_HALF_US : BLINK_HALF_US;
		bool phase = blink_phase(clock_us, half_us);

		while (k_msgq_get(&blink_queue, value, K_NO_WAIT) == 0) {
			/* Direction only. The phase is the clock's, not the Pi's. */
		}
		if (s.error) {
			/* Hazard: both sides together, and HZ marks the phase. */
			lab_io_set(LAB_LEFT, phase);
			lab_io_set(LAB_RIGHT, phase);
		} else {
			bool left = value[0] == 1 || value[0] == 3;
			bool right = value[0] == 2 || value[0] == 3;

			lab_io_set(LAB_LEFT, left && phase);
			lab_io_set(LAB_RIGHT, right && phase);
		}
		/*
		 * HZ means the hazard phase, not the blink phase. Driving it
		 * whenever the lamps toggle would leave it flashing on a
		 * healthy vehicle, which reads as a fault that is not there.
		 * Checkoff step 1 scopes it beside FL and RL during hazards,
		 * and it stays dark otherwise.
		 */
		lab_io_set(LAB_HZ, s.error && phase);
		finish(BLINK, start);

		uint32_t wait_us = blink_until_edge_us(clock_us, half_us);

		if (wait_us > BLINK_COARSE_MS * 1000U) {
			/* Far from an edge: stay responsive to an error change. */
			(void)k_sem_take(&blink_event, K_MSEC(BLINK_COARSE_MS));
		} else {
			/*
			 * Near one: sleep to the deadline. Checkoff step 3
			 * allows 1 ms between FL and RL, and the two nodes only
			 * agree to the extent that each lands on its own
			 * deadline rather than on a polling boundary.
			 */
			k_sleep(K_USEC(wait_us));
		}
	}
}

/** @brief Latch self-test/fault and send FAULT no faster than once per 20 ms.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void test_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	int64_t last_send = -LAB_HB_MS;
	int64_t recovery_press = -1;
	while (true) {

		if (k_sem_take(&test_event, K_MSEC(20)) != 0) {
			atomic_inc(&progress[TEST]);
			continue;
		}
		uint32_t start = begin(TEST);
		int reason = atomic_set(&fault_reason, 0);
		if (atomic_set(&button_pending, 0) != 0) {
			int64_t pressed = (uint32_t)atomic_get(&button_time);
			if (!snapshot().local_fail) {
				reason = LAB_TEST;
				recovery_press = -1;
			} else if (recovery_press >= 0 && pressed - recovery_press <= 500) {
				k_spinlock_key_t key = k_spin_lock(&state_lock);
				state.local_fail = false;
				/* HB_MON clears error only with fresh CTRL and peer heartbeats. */
				state.version++;
				k_spin_unlock(&state_lock, key);
				recovery_press = -1;
				reason = 0;
			} else {
				recovery_press = pressed;
			}
		}
		if (reason != 0) {

			k_spinlock_key_t key = k_spin_lock(&state_lock);
			state.local_fail = true;

			state.error = true;
			state.version++;
			k_spin_unlock(&state_lock, key);
			k_sem_give(&output_event);
			k_sem_give(&blink_event);
			int64_t remaining = LAB_HB_MS - (k_uptime_get() - last_send);
			/* Safe outputs have already been released before this rate limit. */
			if (remaining > 0) {
				k_msleep((int32_t)remaining);
			}
			uint8_t data[8] = {0, LAB_NODE_ID, reason};
			lab_can_send(LAB_FAULT, data);

			last_send = k_uptime_get();
		}
		finish(TEST, start);
	}
}

/** @brief Run a deterministic first-order plant and integer PID every 10 ms.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void pid_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	int64_t next = k_uptime_get();
	while (true) {

		next += LAB_PID_MS;
		uint32_t start = begin(PID);
		struct shared_state s = snapshot();
		k_mutex_lock(&output_lock, K_FOREVER);

		int32_t old_duty = duty;
		int32_t old_integral = integral;
		int32_t old_error = previous_error;
		k_mutex_unlock(&output_lock);

		/* Normalized speed 0..1000, Euler plant time constant 100 ms. */
		simulated_speed += (old_duty - simulated_speed) / 10;
		int32_t target = s.command.throttle * 10;
		int32_t error = target - simulated_speed;
		int32_t sum = CLAMP(old_integral + error, -20000, 20000);
		int32_t candidate = CLAMP(target + error / 4 + sum / 100 +
			(error - old_error) / 8, 0, 1000);
		k_mutex_lock(&output_lock, K_FOREVER);
		struct shared_state current = snapshot();
		/* A newer command or fault invalidates this calculation. */
		if (current.version == s.version && !current.error && current.command.brake == 0) {
			integral = sum;
			previous_error = error;
			duty = candidate;
			(void)lab_io_pwm(MOTOR_PERIOD_NS, MOTOR_PERIOD_NS * duty / 1000);
		}
		k_mutex_unlock(&output_lock);
		finish(PID, start);
		k_sleep(K_TIMEOUT_ABS_MS(next));
		if (k_uptime_get() > next + LAB_PID_MS) {
			next = k_uptime_get();
		}
	}
}

/** @brief Periodic HB_TX or FORCE_TX, one producer per CAN ID.
 * @param a Task index.
 * @param b Period milliseconds.
 * @param c Unused.
 */
static void tx_task(void *a, void *b, void *c)
{
	ARG_UNUSED(c);
	enum task_id id = (enum task_id)(uintptr_t)a;
	int period = (int)(uintptr_t)b;
	int64_t next = k_uptime_get();
	uint8_t counter = 0;
	while (true) {

		next += period;
		uint32_t start = begin(id);
		struct shared_state s = snapshot();
		if (!s.local_fail) {

			uint8_t data[8] = {0};
			if (id == HEARTBEAT) {

				/*
				 * REF shares this 20 ms slot because Part 2
				 * gives the reference and the heartbeat the
				 * same period. It goes first: REF holds the
				 * lowest identifier in the catalog and winning
				 * arbitration is wasted if it is queued behind
				 * a frame already behind the heartbeat.
				 */
				(void)raft_ref_tx();
				data[1] = s.error;
				data[2] = (!s.degraded[0]) | ((!s.degraded[1]) << 1);
				lab_can_send(LAB_HB1 + LAB_NODE_ID - 1, data);
			} else {
				data[1] = counter++;
				lab_can_send(LAB_FORCE, data);
			}
		}
		finish(id, start);
		k_sleep(K_TIMEOUT_ABS_MS(next));
		if (k_uptime_get() > next + period) {
			next = k_uptime_get();
		}
	}
}

/** @brief Recompute peer/bus health every 20 ms and release safe outputs.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void monitor_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	int64_t next = k_uptime_get();
	while (true) {

		next += LAB_HB_MS;
		uint32_t start = begin(MONITOR);
		if (lab_can_drops() > 0 || lab_io_failed()) {

			if (!snapshot().local_fail) {
				fail(lab_can_drops() > 0 ? LAB_QUEUE : LAB_IO);
			}
		}
		int64_t now = k_uptime_get();

		/*
		 * Re-aim the pulse from the newest reference. The edge is armed
		 * nearly a second ahead, which multiplies any ratio error by
		 * that whole second; doing this every 20 ms cuts the lever arm
		 * by about forty five times.
		 */
		pps_refine();
		/* SYNC tracks liveness, so it has to age out here, not only on RX. */
		raft_agent_tick(now);
		raft_election_tick(now, lab_can_heard_within(LAB5_ISOLATION_MS));
		raft_append_tx(now);
		raft_client_poll(now);
		/*
		 * Cockpit liveness, on either bus. Reported to the agent before
		 * the state lock is taken, because the agent holds its own and
		 * the two must not nest.
		 */
		struct shared_state peek = snapshot();
		bool cockpit = false;

		for (int bus = 0; bus < 2; bus++) {
			cockpit |= peek.seen[0][bus] &&
				now - peek.heartbeat[0][bus] < LAB_TIMEOUT_MS;
		}
		raft_report_cockpit(cockpit);
		raft_peer_tick(now, lab_can_heard_within(LAB5_ISOLATION_MS));
		/*
		 * The committed log is the cluster's agreed view, so failure
		 * mode holds the vehicle in the error state even when this
		 * node's own inputs look fine.
		 */
		bool failure = raft_failure_mode();

		k_spinlock_key_t key = k_spin_lock(&state_lock);
		bool error = failure || state.local_fail || !state.have_ctrl ||
			now - state.ctrl_at >= 60;
		state.degraded[0] = false;

		state.degraded[1] = false;
		for (int peer = 0; peer < 3; peer++) {
			if (peer == LAB_NODE_ID - 1) {
				continue;
			}
			bool fresh[2];
			for (int bus = 0; bus < 2; bus++) {

				fresh[bus] = state.seen[peer][bus] && now - state.heartbeat[peer][bus] < 60;
				/* 40 ms + up to 20 ms poll meets a nominal 60 ms degradation target. */
				state.degraded[bus] |= !state.seen[peer][bus] ||
					now - state.heartbeat[peer][bus] >= 40;
			}
			/* Explicit FAULT clears only when the failed peer resumes heartbeats. */
			if ((fresh[0] && state.heartbeat[peer][0] > state.fault_at[peer]) ||
			    (fresh[1] && state.heartbeat[peer][1] > state.fault_at[peer])) {
				state.remote_fail[peer] = false;
			}
			error |= (!fresh[0] && !fresh[1]) || state.remote_fail[peer];
		}
		bool changed = error != state.error;
		state.error = error;

		if (changed) {
			state.version++;
		}
		bool degraded_a = state.degraded[0];
		bool degraded_b = state.degraded[1];
		k_spin_unlock(&state_lock, key);

		lab_io_set(LAB_DEG_A, degraded_a);
		lab_io_set(LAB_DEG_B, degraded_b);
		lab_io_set(LAB_ERR, error);
		if (changed) {
			k_sem_give(&output_event);
			k_sem_give(&blink_event);
		}
		finish(MONITOR, start);
		k_sleep(K_TIMEOUT_ABS_MS(next));
		if (k_uptime_get() > next + LAB_HB_MS) {
			next = k_uptime_get();
		}
	}
}

/** @brief Print elapsed envelopes and feed watchdog only if required tasks progress.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void diagnostics_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	atomic_val_t previous[LAB_TASK_COUNT] = {0};
	int ticks = 0;
	while (true) {

		bool healthy = lab_can_workers_alive();
		for (int i = 0; i < DIAGNOSTICS; i++) {

			if (LAB_MEASURE_MODE != 0 && i != DISPATCH &&
			    !(i == OUTPUT && LAB_MEASURE_MODE == 2) &&
			    !(i == RELAY && LAB_MEASURE_MODE == 3)) {
				continue;
			}
			if ((i == PID && LAB_NODE_ID == 2) || (i == FORCE && LAB_NODE_ID == 3)) {
				continue;
			}
			atomic_val_t current = atomic_get(&progress[i]);
			healthy &= current != previous[i];

			previous[i] = current;
		}
		if (healthy && wdt_feed(watchdog, watchdog_channel) != 0) {
			fail(LAB_IO);
		}
		if (++ticks >= 20 && LAB_DIAGNOSTICS && LAB_MEASURE_MODE == 0) {
			uint32_t maxima[LAB_TASK_COUNT];
			uint32_t counts[LAB_TASK_COUNT];
			k_spinlock_key_t key = k_spin_lock(&stats_lock);
			memcpy(maxima, maximum_us, sizeof(maxima));

			memcpy(counts, executions, sizeof(counts));
			k_spin_unlock(&stats_lock, key);
			struct shared_state s = snapshot();
			printk("N%d error=%d local=%d degraded=%d/%d drops=%u bad=%ld dup=%ld\n",

				LAB_NODE_ID, s.error, s.local_fail, s.degraded[0], s.degraded[1],
				lab_can_drops(), (long)atomic_get(&malformed), (long)atomic_get(&duplicates));
			printk(" TX failed A/B=%u/%u\n", lab_can_tx_errors(0), lab_can_tx_errors(1));
			for (int i = 0; i < DIAGNOSTICS; i++) {
				printk(" task=%d jobs=%u max_elapsed_us=%u (includes preemption)\n",
					i, counts[i], maxima[i]);
			}
			ticks = 0;
		}
		k_msleep(100);
	}
}

/** @brief Print the drift the global timeline is currently correcting for.
 *
 * Part 6 requires a periodic task that reads global_gettime() and reports the
 * drift error. One line a second, which is slow enough not to distort the
 * timing the rest of the system is measured on. The ratio is printed in parts
 * per million rather than as a Q16.16 integer, because that is the unit a
 * crystal's specification uses and so the unit the report compares against.
 * @param a Unused.
 * @param b Unused.
 * @param c Unused.
 */
static void drift_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	int64_t next = k_uptime_get();

	while (true) {
		next += DRIFT_REPORT_MS;
		uint32_t start = begin(DRIFT);

		/*
		 * The edges rearm each other in the timer interrupt, so this
		 * only has to get the first one going once a timeline exists.
		 */
		pps_start();
		struct pps_report edge;

		/*
		 * The edge line is the report's jitter data, so it is printed
		 * here rather than in the timer interrupt that produced it.
		 */
		while (LAB5_CFG_DEBUG && pps_take_report(&edge)) {
			printk("pps: edge=%u global=%llu.%06llu local=%u ratio=%d ppm "
			       "correction=%d us\n", edge.edge,
			       (unsigned long long)(edge.global_us / 1000000U),
			       (unsigned long long)(edge.global_us % 1000000U),
			       edge.local_ticks,
			       (int)(((int64_t)(edge.ratio - TIME_SYNC_RATIO_ONE) *
				      1000000) >> TIME_SYNC_RATIO_SHIFT),
			       (int)edge.correction_us);
		}
		if (LAB5_CFG_DEBUG && !time_sync_locked()) {
			/*
			 * Without this the module fails silently: an unlocked
			 * timeline prints nothing at all, which is exactly what
			 * a wrong tick rate looks like from the console.
			 */
			printk("sync: no lock yet, ref=%d missed=%u\n",
			       raft_synced(), raft_missed_refs());
		}
		if (LAB5_CFG_DEBUG && time_sync_locked()) {
			uint64_t global = time_sync_global_us(pps_now());
			int32_t ratio = time_sync_ratio();
			/* Signed, so a slow local clock reads as a negative ppm. */
			int32_t ppm = (int32_t)(((int64_t)(ratio - TIME_SYNC_RATIO_ONE) *
						 1000000) >> TIME_SYNC_RATIO_SHIFT);

			printk("sync: global=%llu.%06llu ratio=%d ppm correction=%d us\n",
			       (unsigned long long)(global / 1000000U),
			       (unsigned long long)(global % 1000000U),
			       ppm, time_sync_correction_us());
		}
		finish(DRIFT, start);
		k_sleep(K_TIMEOUT_ABS_MS(next));
		if (k_uptime_get() > next + DRIFT_REPORT_MS) {
			next = k_uptime_get();
		}
	}
}

/** @brief Create a named preemptive task.
 * @param id Index.
 * @param entry Thread body.
 * @param priority Zephyr preemptive priority.
 * @param a First argument.
 * @param b Second argument.
 * @param name Diagnostic name.
 */
static void spawn(enum task_id id, k_thread_entry_t entry, int priority,
		  void *a, void *b, const char *name)
{
	k_tid_t tid = k_thread_create(&threads[id], stacks[id], LAB_STACK_SIZE,
		entry, a, b, NULL, K_PRIO_PREEMPT(priority), 0, K_NO_WAIT);
	(void)k_thread_name_set(tid, name);
}

/** @brief Start scope-only outputs, transport, independent watchdog and workers.
 * @return Zero or initialization error.
 */
int lab_tasks_start(void)
{
	int rc = lab_io_init(button_pressed);
	if (rc != 0) {

		return rc;
	}
	/* Adopt the configured role before any REF can arrive. */
	raft_agent_init();
	lab_io_set(LAB_ERR, true);
	lab_io_set(LAB_DIR, LAB_NODE_ID == 3);
	rc = lab_io_pwm(LAB_NODE_ID == 2 ? SERVO_PERIOD_NS : MOTOR_PERIOD_NS,
		LAB_NODE_ID == 2 ? 1500000 : 0);
	if (rc != 0) {
		return rc;
	}
	/* Before the transport: the receive callback timestamps from this counter. */
	rc = pps_init();
	if (rc != 0) {
		return rc;
	}
	time_sync_set_tick_hz(PPS_TICK_HZ);
	rc = lab_can_init();
	if (rc != 0) {
		return rc;
	}
	if (!device_is_ready(watchdog)) {
		return -ENODEV;
	}
	struct wdt_timeout_cfg timeout = {.window = {.min = 0, .max = 1000},
		.flags = WDT_FLAG_RESET_SOC};
	watchdog_channel = wdt_install_timeout(watchdog, &timeout);
	if (watchdog_channel < 0) {
		return watchdog_channel;
	}
	rc = wdt_setup(watchdog, 0);
	if (rc != 0) {
		return rc;
	}
	spawn(DISPATCH, dispatch_task, 1, NULL, NULL, "dispatch");
	if (LAB_MEASURE_MODE != 0) {
		if (LAB_MEASURE_MODE == 2) {
			spawn(OUTPUT, output_task, LAB_NODE_ID == 2 ? 4 : 3,
			      NULL, NULL, "DRIVE_MEASURE");
		} else if (LAB_MEASURE_MODE == 3) {
			spawn(RELAY, relay_task, 2, NULL, NULL, "RELAY_MEASURE");
		}
		spawn(DIAGNOSTICS, diagnostics_task, 10, NULL, NULL, "WATCHDOG");
		return 0;
	}
	spawn(RELAY, relay_task, 2, NULL, NULL, "RELAY");
	spawn(OUTPUT, output_task, LAB_NODE_ID == 2 ? 4 : 3, NULL, NULL,
		LAB_NODE_ID == 2 ? "STEER" : "DRIVE");
	spawn(BLINK, blink_task, LAB_NODE_ID == 2 ? 3 : 4, NULL, NULL, "BLINK");
	spawn(TEST, test_task, 5, NULL, NULL, "TEST_BTN_FAULT");
	if (LAB_NODE_ID == 3) {
		spawn(PID, pid_task, 6, NULL, NULL, "PID_SIM");
	}
	spawn(HEARTBEAT, tx_task, LAB_NODE_ID == 2 ? 6 : 7,
		(void *)(uintptr_t)HEARTBEAT, (void *)(uintptr_t)20, "HB_TX");
	spawn(MONITOR, monitor_task, LAB_NODE_ID == 2 ? 7 : 8, NULL, NULL, "HB_MON");
	if (LAB_NODE_ID == 2) {
		spawn(FORCE, tx_task, 8, (void *)(uintptr_t)FORCE, (void *)(uintptr_t)50, "FORCE_TX");
	}
	spawn(DRIFT, drift_task, 9, NULL, NULL, "DRIFT");
	spawn(DIAGNOSTICS, diagnostics_task, 10, NULL, NULL, "DIAGNOSTICS");
	k_sem_give(&output_event);
	k_sem_give(&blink_event);
	return 0;
}
