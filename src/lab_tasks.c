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
#include <zephyr/drivers/watchdog.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/printk.h>
#include <string.h>

/** @brief Per-thread stack budget; must be checked on the target. */
#define LAB_STACK_SIZE 1536
/** @brief Number of independent task entries, including maintenance/diagnostics. */
#define LAB_TASK_COUNT 10
/** @brief Error state assumes full braking, represented by zero PWM and DIR high. */
#define MOTOR_PERIOD_NS 50000U
/** @brief Standard 20 ms servo period; no servo is attached. */
#define SERVO_PERIOD_NS 20000000U

enum task_id { DISPATCH, OUTPUT, BLINK, TEST, PID, HEARTBEAT, MONITOR, FORCE,
	RELAY, DIAGNOSTICS };
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

		uint32_t id = rx.frame.id;
		/*
		 * Lab 5 traffic is not Lab 3 catalog traffic: lab_valid() would
		 * reject it and inflate the malformed counter. REF goes to the
		 * agent, which owns the REF_RX marker and SYNC. The rest of the
		 * consensus range is accepted by the filter and dropped until the
		 * phase that owns it. The millisecond uptime is a placeholder
		 * timestamp: P6 replaces it with a capture at the chosen point.
		 */
		if (id == LAB5_REF) {
			raft_on_ref(rx.frame.data, (uint64_t)k_uptime_get() * 1000U);
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
			/* Acks and votes belong to other nodes or later phases. */
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
	int64_t hazard_edge = 0;
	bool phase = true;
	bool was_error = false;
	while (true) {

		(void)k_sem_take(&blink_event, K_MSEC(20));
		uint32_t start = begin(BLINK);
		struct shared_state s = snapshot();
		while (k_msgq_get(&blink_queue, value, K_NO_WAIT) == 0) {

			if (!s.error) {
				lab_io_set(LAB_LEFT, (value[0] == 1 || value[0] == 3) && value[1]);
				lab_io_set(LAB_RIGHT, (value[0] == 2 || value[0] == 3) && value[1]);
			}
		}
		if (s.error) {
			int64_t now = k_uptime_get();
			if (!was_error) {

				phase = true;
				hazard_edge = now + 250;
			} else if (now >= hazard_edge) {
				phase = !phase;
				hazard_edge = now + 250;
			}
			lab_io_set(LAB_LEFT, phase);
			lab_io_set(LAB_RIGHT, phase);
		} else if (was_error) {
			lab_io_set(LAB_LEFT, (value[0] == 1 || value[0] == 3) && value[1]);
			lab_io_set(LAB_RIGHT, (value[0] == 2 || value[0] == 3) && value[1]);
		}
		was_error = s.error;
		finish(BLINK, start);
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

		/* SYNC tracks liveness, so it has to age out here, not only on RX. */
		raft_agent_tick(now);
		raft_client_poll(now);
		k_spinlock_key_t key = k_spin_lock(&state_lock);
		bool error = state.local_fail || !state.have_ctrl || now - state.ctrl_at >= 60;
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
	spawn(DIAGNOSTICS, diagnostics_task, 10, NULL, NULL, "DIAGNOSTICS");
	k_sem_give(&output_event);
	k_sem_give(&blink_event);
	return 0;
}
