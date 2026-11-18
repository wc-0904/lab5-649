/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file lab_io.c
 * @brief Own test GPIOs and one disconnected PWM output.
 */
#include "lab_io.h"
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>

/** @brief Index into the ports table below. */
enum lab_port { PORT_A, PORT_B, PORT_C, PORT_COUNT };

/** @brief One logical output: which GPIO controller, and which pin on it. */
struct lab_pin_map { uint8_t port; uint8_t pin; };

static const struct device *const ports[PORT_COUNT] = {
	DEVICE_DT_GET(DT_NODELABEL(gpioa)),
	DEVICE_DT_GET(DT_NODELABEL(gpiob)),
	DEVICE_DT_GET(DT_NODELABEL(gpioc)),
};
static const struct device *const pwm = DEVICE_DT_GET(DT_NODELABEL(pwm2));
static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
/*
 * Lab 3 rows stay on GPIOC in their original order. The Lab 5 rows avoid every
 * pin the overlay already claims: PA1 is TIM2 PWM, PA5..PA7 SPI1, PA10 and PB5
 * the MCP2515 interrupts, PB6 and PC7 the chip selects, PA2 and PA3 the console,
 * PA13 and PA14 SWD, PB3 SWO, PC13 the USER button. PB12..PB15 are free because
 * the overlay disables SPI2, and PA0 is free and is TIM5 channel 1, which is the
 * 32-bit timer the PPS compare wants in P7.
 */
static const struct lab_pin_map pins[LAB_PIN_COUNT] = {
	{PORT_C, 0}, {PORT_C, 1}, {PORT_C, 2}, {PORT_C, 3}, {PORT_C, 4},
	{PORT_C, 5}, {PORT_C, 6}, {PORT_C, 8}, {PORT_C, 10}, {PORT_C, 12},
	{PORT_C, 14}, {PORT_C, 9}, {PORT_C, 11},
	{PORT_B, 12}, {PORT_B, 13}, {PORT_B, 14}, {PORT_B, 15}, {PORT_A, 0},
};
static struct gpio_callback button_cb;
static void (*button_handler)(void);
static atomic_t io_error;

/** @brief Forward button IRQ without device work.
 * @param dev GPIO device.
 * @param cb Registered callback.
 * @param mask Triggered pins.
 */
static void button_isr(const struct device *dev, struct gpio_callback *cb, uint32_t mask)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(cb);
	ARG_UNUSED(mask);
	button_handler();
}

/** @brief Initialize test outputs and button.
 * @param button_callback ISR-safe event notifier.
 * @return Zero or negative driver error.
 */
int lab_io_init(void (*button_callback)(void))
{
	if (!device_is_ready(pwm) || !gpio_is_ready_dt(&button)) {
		return -ENODEV;
	}
	for (int i = 0; i < PORT_COUNT; i++) {
		if (!device_is_ready(ports[i])) {
			return -ENODEV;
		}
	}
	for (int i = 0; i < LAB_PIN_COUNT; i++) {
		int rc = gpio_pin_configure(ports[pins[i].port], pins[i].pin,
					    GPIO_OUTPUT_INACTIVE);
		if (rc != 0) {

			return rc;
		}
	}
	button_handler = button_callback;
	int rc = gpio_pin_configure_dt(&button, GPIO_INPUT);
	if (rc != 0) {

		return rc;
	}
	gpio_init_callback(&button_cb, button_isr, BIT(button.pin));
	rc = gpio_add_callback(button.port, &button_cb);
	if (rc != 0) {
		return rc;
	}
	return gpio_pin_interrupt_configure_dt(&button, GPIO_INT_EDGE_TO_ACTIVE);
}

/** @brief Drive one test point.
 * @param pin Logical output.
 * @param value Level.
 */
void lab_io_set(enum lab_pin pin, bool value)
{
	if (gpio_pin_set(ports[pins[pin].port], pins[pin].pin, value) != 0) {
		atomic_set(&io_error, 1);
	}
}

/** @brief Toggle one test point.
 * @param pin Logical output.
 */
void lab_io_toggle(enum lab_pin pin)
{
	if (gpio_pin_toggle(ports[pins[pin].port], pins[pin].pin) != 0) {
		atomic_set(&io_error, 1);
	}
}

/** @brief Write scope-only PWM compare value.
 * @param period_ns Period.
 * @param pulse_ns Pulse width.
 * @return Driver status.
 */
int lab_io_pwm(uint32_t period_ns, uint32_t pulse_ns)
{
	int rc = pwm_set(pwm, 2, period_ns, pulse_ns, 0);
	if (rc != 0) {

		atomic_set(&io_error, 1);
	}
	lab_io_toggle(LAB_PWM_SET);
	return rc;
}

/** @brief Walk the Lab 5 outputs one at a time so each can be identified.
 * @param step_ms Time each pin stays high, in milliseconds.
 */
void lab_io_selftest(uint32_t step_ms)
{
	/*
	 * Configure the Lab 5 pins here rather than relying on lab_io_init(),
	 * so the walk works when main() runs it before the task set starts.
	 * Repeating the configuration after lab_io_init() is harmless.
	 */
	for (int pin = LAB_PIN_FIRST_LAB5; pin < LAB_PIN_COUNT; pin++) {
		if (!device_is_ready(ports[pins[pin].port]) ||
		    gpio_pin_configure(ports[pins[pin].port], pins[pin].pin,
				       GPIO_OUTPUT_INACTIVE) != 0) {
			atomic_set(&io_error, 1);
			return;
		}
	}
	for (int pass = 0; pass < 2; pass++) {
		for (int pin = LAB_PIN_FIRST_LAB5; pin < LAB_PIN_COUNT; pin++) {
			lab_io_set((enum lab_pin)pin, true);
			k_msleep(step_ms);
			lab_io_set((enum lab_pin)pin, false);
		}
	}
}

/** @brief Check latched pin errors.
 * @return True after an I/O failure.
 */
bool lab_io_failed(void)
{
	return atomic_get(&io_error) != 0;
}
