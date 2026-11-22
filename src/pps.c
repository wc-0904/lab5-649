/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file pps.c
 * @brief Drive PA0 from TIM5 channel 1 so the edge carries no software jitter.
 */
#include "pps.h"
#include "time_sync.h"
#include <zephyr/kernel.h>
#include <zephyr/drivers/clock_control.h>
#include <zephyr/sys/printk.h>
#include <zephyr/drivers/clock_control/stm32_clock_control.h>
#include <stm32f4xx_ll_gpio.h>
#include <stm32f4xx_ll_tim.h>

/** @brief The timer that owns the compare output. */
#define PPS_TIMER DT_NODELABEL(timers5)
/** @brief Alternate function number that maps PA0 to TIM5 channel 1. */
#define PPS_PIN_AF LL_GPIO_AF_2
/** @brief Width of the emitted pulse, microseconds.
 *
 * Only the rising edge is measured, so this just has to be long enough to see
 * on a scope and short enough to leave the fall far from the next rise.
 */
#define PPS_PULSE_US 10000U
/** @brief Closest an edge may be scheduled, microseconds.
 *
 * Writing a compare value the counter has already passed means waiting a full
 * 32 bit wrap, about 71 minutes, for the match. Deadlines nearer than this are
 * pushed to the following second instead.
 */
#define PPS_ARM_GUARD_US 2000U
/** @brief Microseconds in one second, the PPS period. */
#define PPS_PERIOD_US 1000000U
/** @brief Closest to the edge that a refinement may still rewrite the compare.
 *
 * Writing a compare value the counter has already passed costs a full 32 bit
 * wrap, about 71 minutes, so the refinement stops well before the deadline. Two
 * milliseconds is a hundred monitor periods' worth of margin and leaves the
 * lever arm at roughly one heartbeat, which is all that was wanted.
 */
#define PPS_REFINE_GUARD_US 2000U

static TIM_TypeDef *const timer = (TIM_TypeDef *)DT_REG_ADDR(PPS_TIMER);
static const struct stm32_pclken pclken[] = STM32_DT_CLOCKS(PPS_TIMER);
static struct {
	bool ready;
	bool running;
	/* True between the rising and the falling edge of the current pulse. */
	bool pin_high;
	uint32_t edges;
	/* Whole global second the armed rising edge belongs to. */
	uint64_t edge_global_us;
	/* Last edge, waiting for a thread to print it. */
	struct pps_report report;
	bool report_pending;
} state;
static struct k_spinlock lock;

/** @brief Read the free running counter.
 * @return Counter value in microseconds, wrapping at 32 bits.
 */
uint32_t pps_now(void)
{
	return LL_TIM_GetCounter(timer);
}

/** @brief Report whether the counter is running.
 * @return True once pps_init() has succeeded.
 */
bool pps_ready(void)
{
	return state.ready;
}

/** @brief Count edges emitted since boot.
 * @return Edge count.
 */
uint32_t pps_edges(void)
{
	return state.edges;
}

/** @brief Schedule the rising edge for the next whole global second.
 *
 * Inverts the global time expression. With base and ratio from the last
 * reference, the local deadline for global time T is
 *
 *     F_deadline = F_k + (T - base) / ratio
 *
 * which is what the compare register is loaded with. Nothing here reads the
 * clock in a loop; the arithmetic runs once and the hardware waits.
 * @return True when an edge was armed.
 */
static bool arm_next_edge(void)
{
	uint32_t now = pps_now();
	uint64_t global_now = time_sync_global_us(now);
	uint64_t target;
	int64_t ahead_us;
	int64_t local_ahead;

	if (!time_sync_locked() || global_now == 0) {
		return false;
	}
	/* The next whole second of global time, never the current one. */
	target = (global_now / PPS_PERIOD_US + 1) * PPS_PERIOD_US;
	ahead_us = (int64_t)(target - global_now);
	/*
	 * Convert the remaining global interval into local ticks by dividing by
	 * the drift ratio, which is the inverse of the multiplication
	 * time_sync_global_us() applies.
	 */
	local_ahead = (ahead_us << TIME_SYNC_RATIO_SHIFT) / time_sync_ratio();
	if (local_ahead < PPS_ARM_GUARD_US) {
		target += PPS_PERIOD_US;
		local_ahead += PPS_PERIOD_US;
	}
	state.edge_global_us = target;
	/* Rising edge: the comparator drives the pin active on match. */
	LL_TIM_OC_SetMode(timer, LL_TIM_CHANNEL_CH1, LL_TIM_OCMODE_ACTIVE);
	LL_TIM_OC_SetCompareCH1(timer, now + (uint32_t)local_ahead);
	state.pin_high = false;
	LL_TIM_ClearFlag_CC1(timer);
	LL_TIM_EnableIT_CC1(timer);
	return true;
}

/** @brief Reprogram the compare after each edge.
 *
 * Runs after the hardware has already moved the pin, so nothing here affects
 * edge position. On the rising edge it schedules the fall; on the fall it
 * schedules the next second. The per-edge console line required by Part 6 is
 * printed here, which is safe because it happens milliseconds away from the
 * next edge rather than at one.
 */
static void pps_isr(const void *arg)
{
	ARG_UNUSED(arg);

	if (!LL_TIM_IsActiveFlag_CC1(timer)) {
		return;
	}
	LL_TIM_ClearFlag_CC1(timer);
	if (!state.pin_high) {
		/* The rising edge just happened. Schedule the fall. */
		uint32_t at = LL_TIM_OC_GetCompareCH1(timer);

		state.pin_high = true;
		state.edges++;
		LL_TIM_OC_SetMode(timer, LL_TIM_CHANNEL_CH1, LL_TIM_OCMODE_INACTIVE);
		LL_TIM_OC_SetCompareCH1(timer, at + PPS_PULSE_US);
		/*
		 * Record, do not print. local_ticks is the counter value the
		 * comparator matched, which is the local clock at the edge by
		 * construction rather than a reading taken near it.
		 */
		state.report.global_us = time_sync_global_us(at);
		state.report.local_ticks = at;
		state.report.ratio = time_sync_ratio();
		state.report.correction_us = time_sync_correction_us();
		state.report.edge = state.edges;
		state.report_pending = true;
		return;
	}
	/* The falling edge just happened. Schedule the next second. */
	state.pin_high = false;
	if (!arm_next_edge()) {
		LL_TIM_DisableIT_CC1(timer);
		state.running = false;
	}
}

/** @brief Recompute the armed deadline from a fresher reference.
 *
 * Holding the lock masks the timer interrupt on this part, so the phase check
 * and the compare write cannot be interleaved by the edge they are about to
 * schedule.
 */
void pps_refine(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	uint32_t now;
	uint64_t global_now;
	int64_t ahead_us;
	int64_t local_ahead;

	/* Only while waiting for a rising edge that has already been chosen. */
	if (!state.ready || !state.running || state.pin_high ||
	    LL_TIM_IsActiveFlag_CC1(timer) ||
	    !time_sync_locked()) {
		k_spin_unlock(&lock, key);
		return;
	}
	now = pps_now();
	global_now = time_sync_global_us(now);
	if (global_now == 0 || global_now >= state.edge_global_us) {
		/* Past the target already; leave the armed value alone. */
		k_spin_unlock(&lock, key);
		return;
	}
	ahead_us = (int64_t)(state.edge_global_us - global_now);
	local_ahead = (ahead_us << TIME_SYNC_RATIO_SHIFT) / time_sync_ratio();
	if (local_ahead >= PPS_REFINE_GUARD_US &&
	    (int32_t)(LL_TIM_OC_GetCompareCH1(timer) - now) >= PPS_REFINE_GUARD_US) {
		LL_TIM_OC_SetCompareCH1(timer, now + (uint32_t)local_ahead);
	}
	k_spin_unlock(&lock, key);
}

/** @brief Collect the most recent edge, once.
 * @param out Destination, written only when true is returned.
 * @return True when an edge has happened since the last call.
 */
bool pps_take_report(struct pps_report *out)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	bool pending = state.report_pending;

	if (pending) {
		*out = state.report;
		state.report_pending = false;
	}
	k_spin_unlock(&lock, key);
	return pending;
}

/** @brief Schedule the next edge and keep scheduling after it. */
void pps_start(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	if (state.ready && !state.running && arm_next_edge()) {
		state.running = true;
	}
	k_spin_unlock(&lock, key);
}

/** @brief Start the counter and claim PA0 as the compare output.
 * @return Zero, or a negative error.
 */
int pps_init(void)
{
	const struct device *clock = DEVICE_DT_GET(STM32_CLOCK_CONTROL_NODE);
	uint32_t rate;
	int rc;

	if (!device_is_ready(clock)) {
		return -ENODEV;
	}
	rc = clock_control_on(clock, (clock_control_subsys_t)&pclken[0]);
	if (rc != 0) {
		return rc;
	}
	/*
	 * The node lists two clocks and they are not interchangeable. Index 0
	 * is the APB1 gate, which reports the bus rate, 42 MHz here. Index 1 is
	 * the timer input, which the reference manual doubles to 84 MHz because
	 * the bus prescaler is not one. Reading index 0 by mistake halves every
	 * derived timestamp, which rejects every drift sample on tolerance and
	 * silently leaves the module unlocked: no global time and no edge.
	 */
	if (ARRAY_SIZE(pclken) < 2) {
		return -EINVAL;
	}
	rc = clock_control_configure(clock, (clock_control_subsys_t)&pclken[1], NULL);
	if (rc != 0) {
		return rc;
	}
	rc = clock_control_get_rate(clock, (clock_control_subsys_t)&pclken[1], &rate);
	if (rc != 0) {
		return rc;
	}
	/*
	 * Derive the divider rather than assuming 84 MHz, so a clock tree change
	 * cannot silently skew every timestamp.
	 */
	if (rate < PPS_TICK_HZ || rate % PPS_TICK_HZ != 0) {
		return -EINVAL;
	}
	/*
	 * PA0 is taken over from the GPIO table in lab_io.c, which only drives
	 * it during the boot pin walk. The timers binding carries no pinctrl
	 * property, so the alternate function is selected directly.
	 */
	LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_0, LL_GPIO_MODE_ALTERNATE);
	LL_GPIO_SetAFPin_0_7(GPIOA, LL_GPIO_PIN_0, PPS_PIN_AF);
	LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_0, LL_GPIO_OUTPUT_PUSHPULL);
	LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_0, LL_GPIO_SPEED_FREQ_VERY_HIGH);

	LL_TIM_SetPrescaler(timer, rate / PPS_TICK_HZ - 1);
	LL_TIM_SetAutoReload(timer, 0xFFFFFFFFU);
	LL_TIM_SetCounterMode(timer, LL_TIM_COUNTERMODE_UP);
	LL_TIM_GenerateEvent_UPDATE(timer);
	LL_TIM_OC_SetMode(timer, LL_TIM_CHANNEL_CH1, LL_TIM_OCMODE_FORCED_INACTIVE);
	LL_TIM_OC_SetPolarity(timer, LL_TIM_CHANNEL_CH1, LL_TIM_OCPOLARITY_HIGH);
	LL_TIM_CC_EnableChannel(timer, LL_TIM_CHANNEL_CH1);
	LL_TIM_EnableCounter(timer);

	IRQ_CONNECT(DT_IRQN(PPS_TIMER), DT_IRQ(PPS_TIMER, priority), pps_isr, NULL, 0);
	irq_enable(DT_IRQN(PPS_TIMER));
	state.ready = true;
	/*
	 * One banner line, not periodic. The tick rate is the single assumption
	 * every timestamp rests on, and getting it wrong fails silently: the
	 * drift samples are simply rejected and nothing is ever printed. Make it
	 * visible at boot instead.
	 */
	printk("pps: TIM5 clock %u Hz, tick %u Hz\n", rate, PPS_TICK_HZ);
	return 0;
}
