/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file lab_can.c
 * @brief Independent priority-queued CAN transmit workers and bounded RX delivery.
 */
#include "lab_can.h"
#include "pps.h"
#include "lab_io.h"
#include "lab_protocol.h"
#include <zephyr/sys/atomic.h>
#include <string.h>

/** @brief One queue per bus holds several bursts without dynamic allocation. */
#define TX_DEPTH 32
/** @brief Discard unsent traffic after 5 ms; prevents a stale recovery backlog. */
#define TX_AGE_MS 5
/** @brief TX worker initial stack budget; verify on the target. */
#define TX_STACK 1536

K_MSGQ_DEFINE(rx_queue, sizeof(struct lab_rx), 64, 4);
static atomic_t drops;
static uint8_t sequence[7];
struct tx_item {
	struct can_frame frame;
	int64_t origin;
	bool relay;
};
struct bus_state {
	const struct device *dev;
	struct k_spinlock lock;
	struct tx_item queue[TX_DEPTH];
	int count;
	struct k_sem wake;
	struct k_sem done;
	struct k_thread thread;
	atomic_t errors;
	atomic_t progress;
	atomic_t completed_status;
	bool started;
};
static struct bus_state buses[2] = {
	{.dev = DEVICE_DT_GET(DT_ALIAS(can_a))},
	{.dev = DEVICE_DT_GET(DT_ALIAS(can_b))},
};
K_THREAD_STACK_ARRAY_DEFINE(tx_stacks, 2, TX_STACK);

/** @brief Copy RX data without blocking the controller worker.
 * @param dev Controller.
 * @param frame Incoming frame.
 * @param user Physical bus index.
 */
static void receive_cb(const struct device *dev, struct can_frame *frame, void *user)
{
	/* First statement: this is the sync timestamp, so nothing precedes it. */
	uint32_t cycles = pps_now();

	ARG_UNUSED(dev);
	struct lab_rx rx = {.frame = *frame, .time_ms = k_uptime_get(),
		.cycles = cycles, .bus = (uint8_t)(uintptr_t)user};

	if (k_msgq_put(&rx_queue, &rx, K_NO_WAIT) != 0) {
		atomic_inc(&drops);
	}
}

/** @brief Signal completion of the sole outstanding hardware frame.
 * @param dev Controller.
 * @param status Result.
 * @param user Bus state.
 */
static void transmit_cb(const struct device *dev, int status, void *user)
{
	ARG_UNUSED(dev);
	struct bus_state *bus = user;

	atomic_set(&bus->completed_status, status);
	k_sem_give(&bus->done);
}

/** @brief Select the smallest ID; equal-ID frames keep FIFO order.
 * @param bus Queue owner.
 * @param item Destination.
 * @return Whether a fresh frame was found.
 */
static bool take_next(struct bus_state *bus, struct tx_item *item)
{
	k_spinlock_key_t key = k_spin_lock(&bus->lock);
	int64_t now = k_uptime_get();

	for (int i = 0; i < bus->count;) {
		if (now - bus->queue[i].origin >= TX_AGE_MS) {
			memmove(&bus->queue[i], &bus->queue[i + 1],
				(bus->count - i - 1) * sizeof(bus->queue[0]));
			bus->count--;
			atomic_inc(&bus->errors);
		} else {
			i++;
		}
	}
	int best = -1;

	for (int i = 0; i < bus->count; i++) {
		if (best < 0 || bus->queue[i].frame.id < bus->queue[best].frame.id) {
			best = i;
		}
	}
	if (best >= 0) {
		*item = bus->queue[best];
		memmove(&bus->queue[best], &bus->queue[best + 1],
			(bus->count - best - 1) * sizeof(bus->queue[0]));
		bus->count--;
	}
	k_spin_unlock(&bus->lock, key);
	return best >= 0;
}

/** @brief Abort stale TX before restarting; only the bus's TX worker calls this.
 * @param bus Controller state.
 * @return True if the controller is usable.
 */
static bool restart(struct bus_state *bus)
{
	int rc = can_stop(bus->dev);

	if (rc != 0 && rc != -EALREADY) {
		bus->started = false;
		return false;
	}
	/* stop completes abort callbacks before a new frame can reuse the slot. */
	k_sem_reset(&bus->done);
	rc = can_start(bus->dev);
	bus->started = rc == 0 || rc == -EALREADY;
	return bus->started;
}

/** @brief Own one hardware TX slot and controller recovery independently per bus.
 * @param a Bus index.
 * @param b Unused.
 * @param c Unused.
 */
static void tx_worker(void *a, void *b, void *c)
{
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	int index = (int)(uintptr_t)a;
	struct bus_state *bus = &buses[index];
	struct tx_item item;

	while (true) {
		atomic_inc(&bus->progress);
		if (!bus->started && !restart(bus)) {
			atomic_inc(&bus->errors);
			k_msleep(20);
			continue;
		}
		if (!take_next(bus, &item)) {
			(void)k_sem_take(&bus->wake, K_MSEC(20));
			continue;
		}
		int64_t remaining = TX_AGE_MS - (k_uptime_get() - item.origin);

		if (remaining <= 0) {
			atomic_inc(&bus->errors);
			continue;
		}
		k_sem_reset(&bus->done);
		lab_io_toggle(index == 0 ? LAB_TX_A : LAB_TX_B);
		int rc = can_send(bus->dev, &item.frame, K_NO_WAIT, transmit_cb, bus);

		if (rc == 0 && item.relay) {
			lab_io_toggle(LAB_RELAY);
		}

		if (rc == 0) {
			remaining = TX_AGE_MS - (k_uptime_get() - item.origin);
			rc = k_sem_take(&bus->done, remaining > 0 ? K_MSEC(remaining) : K_NO_WAIT);
			if (rc == 0) {
				rc = (int)atomic_get(&bus->completed_status);
			}
		}
		if (rc != 0) {
			atomic_inc(&bus->errors);
			(void)restart(bus);
		}
	}
}

/** @brief Enqueue without waiting for the other bus or a CAN acknowledgment.
 * @param frame Complete payload, never modified.
 * @param index Physical destination bus.
 * @param origin Original local receive/release timestamp.
 * @param relay Whether to toggle RELAY_TX at hardware submission.
 * @return Zero or queue error.
 */
static int enqueue(const struct can_frame *frame, int index, int64_t origin, bool relay)
{
	if (index < 0 || index > 1) {
		return -EINVAL;
	}
	struct bus_state *bus = &buses[index];
	k_spinlock_key_t key = k_spin_lock(&bus->lock);
	bool full = bus->count == TX_DEPTH;

	if (!full) {
		bus->queue[bus->count++] = (struct tx_item){
			.frame = *frame, .origin = origin, .relay = relay};
	}
	k_spin_unlock(&bus->lock, key);
	if (full) {
		atomic_inc(&bus->errors);
		return -ENOSPC;
	}
	k_sem_give(&bus->wake);
	return 0;
}

/** @brief Initialize both controllers before starting their dedicated TX workers.
 * @return Zero or driver error.
 */
int lab_can_init(void)
{
	static const uint16_t ids[] = {LAB_CTRL, LAB_FAULT,
		LAB_HB1, LAB_HB2, LAB_HB3, LAB_BLINK, LAB_FORCE};
	static const struct can_filter lab5_filters[] = {
		{.id = LAB5_REF, .mask = CAN_STD_ID_MASK},
		{.id = LAB5_FILTER_ID, .mask = LAB5_FILTER_MASK},
		/* The same two, as relayed copies. */
		{.id = LAB5_REF | LAB5_RELAY_FLAG, .mask = CAN_STD_ID_MASK},
		{.id = LAB5_FILTER_ID | LAB5_RELAY_FLAG,
		 .mask = LAB5_FILTER_MASK | LAB5_RELAY_FLAG},
	};

	for (int i = 0; i < 2; i++) {
		struct bus_state *bus = &buses[i];

		k_sem_init(&bus->wake, 0, 1);
		k_sem_init(&bus->done, 0, 1);
		if (!device_is_ready(bus->dev)) {
			return -ENODEV;
		}
		int rc = can_set_bitrate(bus->dev, 500000);

		if (rc != 0) {
			return rc;
		}
		for (size_t j = 0; j < ARRAY_SIZE(ids); j++) {
			struct can_filter filter = {.id = ids[j], .mask = CAN_STD_ID_MASK};

			rc = can_add_rx_filter(bus->dev, receive_cb, (void *)(uintptr_t)i, &filter);
			if (rc < 0) {
				return rc;
			}
		}
		/* One masked slot for the whole consensus range; see lab5_filter. */
		for (size_t j = 0; j < ARRAY_SIZE(lab5_filters); j++) {
			rc = can_add_rx_filter(bus->dev, receive_cb, (void *)(uintptr_t)i,
					       &lab5_filters[j]);
			if (rc < 0) {
				return rc;
			}
		}
		rc = can_start(bus->dev);
		if (rc != 0) {
			return rc;
		}
		bus->started = true;
	}
	for (int i = 0; i < 2; i++) {
		k_tid_t tid = k_thread_create(&buses[i].thread, tx_stacks[i], TX_STACK,
			tx_worker, (void *)(uintptr_t)i, NULL, NULL, K_PRIO_PREEMPT(2), 0, K_NO_WAIT);

		(void)k_thread_name_set(tid, i == 0 ? "CAN_TX_A" : "CAN_TX_B");
	}
	return 0;
}

/** @brief Dequeue an RX frame.
 * @param rx Destination.
 * @param timeout Wait bound.
 * @return Queue result.
 */
int lab_can_receive(struct lab_rx *rx, k_timeout_t timeout)
{
	return k_msgq_get(&rx_queue, rx, timeout);
}

/** @brief Assign one sequence and enqueue identical copies independently.
 * @param id Locally owned ID.
 * @param data Eight bytes, sequence and CTRL CRC completed here.
 */
void lab_can_send(uint16_t id, uint8_t data[8])
{
	int slot = lab_slot(id);

	if (slot < 0) {
		return;
	}
	data[0] = sequence[slot]++ & 15;
	if (id == LAB_CTRL) {
		data[7] = lab_crc(data, 7);
	}
	struct can_frame frame = {.id = id, .dlc = 8};
	int64_t origin = k_uptime_get();

	memcpy(frame.data, data, 8);
	(void)enqueue(&frame, 0, origin, false);
	(void)enqueue(&frame, 1, origin, false);
}

/** @brief Report whether this node's own link to one bus is transmitting cleanly.
 * @param bus Physical index 0 for A, 1 for B.
 * @return True when the controller is error active and has no transmit errors.
 */
bool lab_can_link_healthy(int bus)
{
	enum can_state state;
	struct can_bus_err_cnt errors;

	if (bus < 0 || bus > 1 || !buses[bus].started) {
		return false;
	}
	if (can_get_state(buses[bus].dev, &state, &errors) != 0) {
		return false;
	}
	return state == CAN_STATE_ERROR_ACTIVE && errors.tx_err_cnt == 0;
}

/** @brief Send a Lab 5 frame onto both buses with the payload untouched.
 * @param id Lab 5 identifier from enum lab5_id.
 * @param data Eight bytes, sent exactly as given.
 */
void lab_can_send_lab5(uint16_t id, const uint8_t data[8])
{
	struct can_frame frame = {.id = id, .dlc = 8};
	int64_t origin = k_uptime_get();

	memcpy(frame.data, data, 8);
	(void)enqueue(&frame, 0, origin, false);
	(void)enqueue(&frame, 1, origin, false);
}

/** @brief Forward unchanged bytes; never allocate a new sequence or recompute CRC.
 * @param frame Received frame.
 * @param bus Opposite bus index.
 * @param received_ms Timestamp of original dispatch input.
 * @return Queue result.
 */
int lab_can_forward(const struct can_frame *frame, int bus, int64_t received_ms)
{
	return enqueue(frame, bus, received_ms, true);
}

/** @brief Verify both TX workers advanced since the previous watchdog interval.
 * @return True when neither worker is stalled.
 */
bool lab_can_workers_alive(void)
{
	static atomic_val_t previous[2];
	bool alive = true;

	for (int i = 0; i < 2; i++) {
		atomic_val_t current = atomic_get(&buses[i].progress);

		alive &= current != previous[i];
		previous[i] = current;
	}
	return alive;
}

/** @brief Return RX overflow count.
 * @return Dropped RX frames.
 */
uint32_t lab_can_drops(void)
{
	return (uint32_t)atomic_get(&drops);
}

/** @brief Return TX rejects, expiry, timeout and completion errors.
 * @param bus Physical bus index.
 * @return Error count.
 */
uint32_t lab_can_tx_errors(int bus)
{
	return bus >= 0 && bus < 2 ? (uint32_t)atomic_get(&buses[bus].errors) : 0;
}
