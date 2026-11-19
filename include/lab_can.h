/* SPDX-License-Identifier: Apache-2.0 */
/** @file lab_can.h
 * @brief Bounded dual-CAN transport; application dispatch owns deduplication.
 */
#ifndef LAB_CAN_H_
#define LAB_CAN_H_
#include <zephyr/drivers/can.h>
/** @brief Received frame with physical bus and callback timestamp. */
struct lab_rx { struct can_frame frame; int64_t time_ms; uint8_t bus; };
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
