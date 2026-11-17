/* SPDX-License-Identifier: Apache-2.0 */
/** @file lab_io.h
 * @brief Scope-only outputs and trace points; no actuator connection required.
 */
#ifndef LAB_IO_H_
#define LAB_IO_H_
#include <stdbool.h>
#include <stdint.h>
/** @brief Logical outputs in the pin table in docs/DEMO_WIRING.md.
 *
 * The first thirteen are the Lab 3 set and keep their order, because other
 * modules index this enum. The Lab 5 additions are appended: LAB_LEADER and
 * LAB_SYNC are status LEDs, LAB_REF_RX toggles on each received reference
 * frame, LAB_HZ marks the hazard phase, and LAB_PPS carries the pulse per
 * second. LAB_PPS is driven directly only for bring-up; from P7 a hardware
 * timer compare owns it.
 */
enum lab_pin { LAB_RX_A, LAB_RX_B, LAB_TX_A, LAB_TX_B, LAB_DEG_A, LAB_DEG_B,
	LAB_PWM_SET, LAB_DIR, LAB_LEFT, LAB_RIGHT, LAB_ERR, LAB_RELAY, LAB_TRACE,
	LAB_LEADER, LAB_SYNC, LAB_REF_RX, LAB_HZ, LAB_PPS, LAB_PIN_COUNT };
/** @brief First Lab 5 output, so bring-up can walk just the new pins. */
#define LAB_PIN_FIRST_LAB5 LAB_LEADER
/** @brief Initialize pins, PWM, and user-button interrupt.
 * @param button_callback Nonblocking callback executed in GPIO ISR context.
 * @return Zero or a driver error.
 */
int lab_io_init(void (*button_callback)(void));
/** @brief Set a test output (thread context).
 * @param pin Logical pin.
 * @param value Output level.
 */
void lab_io_set(enum lab_pin pin, bool value);
/** @brief Toggle a marker (thread context).
 * @param pin Logical pin.
 */
void lab_io_toggle(enum lab_pin pin);
/** @brief Set TIM2 CH2 on PA1; scope only.
 * @param period_ns PWM period in nanoseconds.
 * @param pulse_ns High interval in nanoseconds.
 * @return Driver result.
 */
int lab_io_pwm(uint32_t period_ns, uint32_t pulse_ns);
/** @brief Walk the Lab 5 outputs one at a time so each can be identified.
 *
 * Blocking, intended for boot before the task set starts. Drives each new pin
 * high alone for @p step_ms, twice, then leaves every one of them low.
 * @param step_ms Time each pin stays high, in milliseconds.
 */
void lab_io_selftest(uint32_t step_ms);
/** @brief Return whether any GPIO operation failed.
 * @return Latched I/O error.
 */
bool lab_io_failed(void);
#endif
