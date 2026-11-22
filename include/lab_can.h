/* SPDX-License-Identifier: Apache-2.0 */
/** @file lab_can.h
 * @brief Bounded dual-CAN transport; application dispatch owns deduplication.
 */
#ifndef LAB_CAN_H_
#define LAB_CAN_H_
#include <zephyr/drivers/can.h>
/** @brief Received frame with physical bus and callback timestamps.
 *
 * `cycles` is the TIM5 microsecond counter that also drives the PPS compare,
 * taken as the first statement of the receive callback, which runs
 * in the MCP2515 driver's interrupt thread at preemptive priority zero. That is
 * the earliest point software can reach: the MCP2515 has no timestamp register
 * readable without a further SPI transaction, so the SPI read of the frame is
 * already behind us and is folded into the one way delay estimate instead
 * (TASKS.md D17). Cycle resolution is the CPU clock, and only differences are
 * ever taken, so the 32 bit wrap at about 51 seconds never matters.
 */
struct lab_rx {
	struct can_frame frame; int64_t time_ms; uint32_t cycles; uint8_t bus;
};
/** @brief Initialize both normal-mode controllers and catalog filters.
 * @return Zero or initialization failure; starts independent TX workers.
 */
int lab_can_init(void);
/** @brief Take a queued frame from the RX worker callbacks.
 * @param rx Destination.
 * @param timeout Kernel timeout.
 * @return Queue result.
 */
int lab_can_receive(struct lab_rx *rx, k_timeout_t timeout);
/** @brief Send one logical frame into both bus queues, assigning one sequence.
 * @param id Catalog ID (one producer per ID).
 * @param data Eight-byte payload; byte zero/CTRL CRC filled here.
 */
void lab_can_send(uint16_t id, uint8_t data[8]);
/** @brief Send a Lab 5 frame onto both buses with the payload untouched.
 *
 * lab_can_send() stamps a Lab 3 sequence number into byte zero and refuses any
 * identifier outside the Lab 3 catalog, so consensus and time frames, whose
 * every byte is payload, go out through this path instead.
 * @param id Lab 5 identifier from enum lab5_id.
 * @param data Eight bytes, sent exactly as given.
 */
void lab_can_send_lab5(uint16_t id, const uint8_t data[8]);
/** @brief Report whether this node's own link to one bus is transmitting cleanly.
 *
 * Reads the controller's error state rather than inferring from missing peer
 * traffic, so it answers "is my branch connected" rather than "is anyone
 * talking". Pulling this node's jumper leaves its transceiver unacknowledged,
 * which drives the controller error passive and then bus off. That is the
 * signal the owner-reports-own-links rule needs (TASKS.md D15).
 *
 * Healthy means **a frame was heard on that bus within the last 100 ms**. The
 * controller's error counters are not used: the TX worker resets the
 * controller after every unacknowledged frame, which clears them, so on a
 * disconnected branch they read healthy most of the time. See lab_can.c.
 * @param bus Physical index 0 for A, 1 for B.
 * @return True when a frame was received on that bus within the last 100 ms.
 */
bool lab_can_link_healthy(int bus);
/** @brief Report whether any frame arrived on either bus within a window.
 * @param window_ms Window length in milliseconds.
 * @return True when this node heard something on at least one bus.
 */
bool lab_can_heard_within(uint32_t window_ms);
/** @brief Read the controller's state and error counters, for diagnosis.
 *
 * A link reported down is either a real electrical fault or a detection
 * threshold set too tight, and those want completely different fixes. Printing
 * the counters at the moment of the report settles which it is without a
 * scope: the transmit counter rises by eight per failed transmission, so a
 * value near the limit means a dozen consecutive failures rather than noise.
 * @param bus Physical index 0 for A, 1 for B.
 * @param state Destination for enum can_state, or -1 when unreadable.
 * @param tx_errors Destination for the transmit error counter.
 * @param rx_errors Destination for the receive error counter.
 */
void lab_can_link_state(int bus, int *state, int *tx_errors, int *rx_errors);
/** @brief Return total dropped software RX frames.
 * @return Overflow count.
 */
uint32_t lab_can_drops(void);
/** @brief Read send failures (including aborted frames).
 * @param bus Physical index 0=A, 1=B.
 * @return Failure count.
 */
uint32_t lab_can_tx_errors(int bus);
/** @brief Forward unchanged bytes to a single bus from the local relay worker.
 * @param frame Original received frame.
 * @param bus Destination bus 0 or 1.
 * @param received_ms Original local receive timestamp.
 * @return Zero or queue error.
 */
int lab_can_forward(const struct can_frame *frame, int bus, int64_t received_ms);
/** @brief Check both TX workers for watchdog supervision (single caller).
 * @return True if both progressed since the previous call.
 */
bool lab_can_workers_alive(void);
#endif
