/* SPDX-License-Identifier: Apache-2.0 */
/** @file time_sync.h
 * @brief Global time from the leader's reference message, with drift correction.
 *
 * Per sync k, with leader stamp L_k, local receipt F_k and a one way delay
 * estimate d:
 *
 *     base  = L_k + d
 *     ratio = (L_k - L_(k-1)) / (F_k - F_(k-1))
 *     global_gettime() = base + (local_now - F_k) * ratio
 *
 * Every quantity used here is a difference taken over at most one heartbeat, so
 * the 32 bit cycle counter's wrap never enters the arithmetic. The ratio is
 * carried in Q16.16 fixed point: the F401 has an FPU but Zephyr does not enable
 * floating point in every thread by default, and fixed point removes the
 * question entirely.
 */
#ifndef TIME_SYNC_H_
#define TIME_SYNC_H_
#include <stdbool.h>
#include <stdint.h>
/** @brief Fractional bits in the drift ratio. */
#define TIME_SYNC_RATIO_SHIFT 16
/** @brief A ratio of exactly one, meaning the local clock matches the master. */
#define TIME_SYNC_RATIO_ONE (1 << TIME_SYNC_RATIO_SHIFT)
/** @brief Declare the rate of the counter this module is given.
 *
 * Defaults to one microsecond per tick, which is what pps.c provides. The host
 * tests set it to the rate they simulate.
 * @param hz Ticks per second.
 */
void time_sync_set_tick_hz(uint32_t hz);
/** @brief Forget any master and return to the unsynchronized state. */
void time_sync_reset(void);
/** @brief Apply one reference message.
 * @param master_us Leader clock carried by the frame.
 * @param local_cycles Cycle counter captured when the frame was received.
 */
void time_sync_on_ref(uint64_t master_us, uint32_t local_cycles);
/** @brief Report whether a global timeline has been established.
 * @return True once a full ratio measurement window has closed.
 */
bool time_sync_locked(void);
/** @brief Read the current global time.
 * @param now_cycles Cycle counter read at the moment of interest.
 * @return Global time in microseconds, or 0 when no reference has arrived.
 */
uint64_t time_sync_global_us(uint32_t now_cycles);
/** @brief Read the smoothed drift ratio in Q16.16.
 * @return Ratio, where TIME_SYNC_RATIO_ONE means no drift.
 */
int32_t time_sync_ratio(void);
/** @brief Report the correction the ratio applies over one measurement window.
 *
 * The signed microseconds by which the local clock would have been wrong after
 * one window had the ratio not been applied. The window is one second, so the
 * number reads directly as drift per second. This is the drift error the
 * periodic task prints.
 * @return Correction in microseconds.
 */
int32_t time_sync_correction_us(void);
/** @brief Convert a cycle difference to microseconds.
 * @param cycles Difference in CPU cycles.
 * @return Microseconds.
 */
uint64_t time_sync_cycles_to_us(uint32_t cycles);
#endif
