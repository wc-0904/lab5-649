/*
 * SPDX-License-Identifier: Apache-2.0
 */
/** @file time_sync.c
 * @brief Build a global timeline from the leader's reference message.
 */
#include "time_sync.h"
#include <zephyr/kernel.h>

/** @brief Ticks per second of the counter passed to this module.
 *
 * Settable so the host tests can drive the arithmetic at a different rate, and
 * so a change of time base cannot silently skew every measurement.
 */
static uint32_t tick_hz = 1000000U;

/** @brief Default one way delay, used until the master is known, microseconds.
 *
 * The delay from the leader stamping L to this node capturing F. Estimated
 * without a round trip, the way TTCAN does it, because Part 6 forbids an NTP
 * style request and response.
 *
 * It is deliberately not a constant. Measured on the bench, this delay is
 * dominated by the *master's transmit path*, not by the bus, so it changes when
 * the leader changes. Holding one value across an election would step every
 * follower's clock by the difference at the handover, and checkoff step 6
 * allows no step larger than 100 microseconds. See time_sync_set_delay_us().
 */
#define DELAY_DEFAULT_US 658

/** @brief Current one way delay estimate, microseconds. */
static uint32_t delay_us = DELAY_DEFAULT_US;

/** @brief Shortest span a ratio may be measured over, microseconds.
 *
 * The ratio is not taken between consecutive references. Measured on the bench,
 * the receive timestamp carries about 50 microseconds of jitter, which over a
 * 20 ms heartbeat reads as 2500 parts per million: forty times larger than the
 * tens of ppm a real crystal is specified to. A ratio measured that way is
 * almost entirely noise, and no amount of averaging separates a signal that is
 * forty times smaller than the measurement error.
 *
 * Measuring across a second instead divides the jitter's contribution by fifty,
 * to roughly 50 ppm per sample, which the average below then brings down to a
 * few. The cost is that a genuine drift change takes a few seconds to follow,
 * which is irrelevant: crystal drift moves with temperature, over minutes.
 *
 * This does not slow the correction down. The base is still re-anchored on
 * every reference, so the ratio only ever has to extrapolate across one
 * heartbeat (TASKS.md D20).
 */
#define RATIO_WINDOW_US 1000000U

/** @brief Longest a window may run before it is abandoned, microseconds.
 *
 * The cycle counter is 32 bits, which at 84 MHz wraps in about 51 seconds, so a
 * window that outlives that cannot be measured. Ten seconds leaves ample margin
 * and is far longer than any gap a live master produces.
 */
#define RATIO_WINDOW_MAX_US 10000000U

/** @brief Weight of a new sample in the ratio average, as a shift.
 *
 * One quarter, so about four windows or four seconds. Lighter than it would
 * need to be on consecutive references, because RATIO_WINDOW_US has already
 * removed most of the noise.
 */
#define RATIO_SHIFT 2

/** @brief Ratios further from one than this are rejected as noise.
 *
 * A crystal is specified in tens of parts per million, so a ratio outside one
 * percent means a missed reference or a restarted master, not drift.
 */
#define RATIO_TOLERANCE (TIME_SYNC_RATIO_ONE / 100)

/** @brief Backward master jump beyond which a new master has taken over.
 *
 * A reference older than the newest one is normally the second copy of a frame
 * this node has already applied, since every REF goes out on both buses. A
 * genuinely new master, though, starts from its own clock, which can be behind.
 * One second separates the two cases by a wide margin: the duplicate arrives
 * within a few hundred microseconds, and no handover lands that close.
 */
#define MASTER_JUMP_US 1000000ULL

static struct {
	bool have_last;
	bool have_anchor;
	bool locked;
	uint64_t base_us;
	uint64_t last_master_us;
	uint32_t last_cycles;
	/* Start of the open ratio window, which spans many references. */
	uint64_t anchor_master_us;
	uint32_t anchor_cycles;
	int32_t ratio;
	int32_t correction_us;
} state;
static struct k_spinlock lock;

/** @brief Convert a cycle difference to microseconds.
 * @param cycles Difference in CPU cycles.
 * @return Microseconds.
 */
uint64_t time_sync_cycles_to_us(uint32_t cycles)
{
	return (uint64_t)cycles * 1000000U / tick_hz;
}

/** @brief Declare the one way delay for the current master.
 *
 * Changing this moves the global timeline by the difference, so it is only
 * called when the master changes, never periodically.
 * @param us Delay in microseconds.
 */
void time_sync_set_delay_us(uint32_t us)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	delay_us = us;
	k_spin_unlock(&lock, key);
}

/** @brief Read the one way delay in force.
 * @return Delay in microseconds.
 */
uint32_t time_sync_delay_us(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	uint32_t us = delay_us;

	k_spin_unlock(&lock, key);
	return us;
}

/** @brief Declare the rate of the counter this module is given.
 * @param hz Ticks per second.
 */
void time_sync_set_tick_hz(uint32_t hz)
{
	if (hz != 0) {
		tick_hz = hz;
	}
}

/** @brief Forget any master and return to the unsynchronized state. */
void time_sync_reset(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	state.have_last = false;
	state.have_anchor = false;
	state.locked = false;
	state.base_us = 0;
	state.last_master_us = 0;
	state.last_cycles = 0;
	state.anchor_master_us = 0;
	state.anchor_cycles = 0;
	state.ratio = TIME_SYNC_RATIO_ONE;
	state.correction_us = 0;
	k_spin_unlock(&lock, key);
}

/** @brief Apply one reference message.
 * @param master_us Leader clock carried by the frame.
 * @param local_cycles Cycle counter captured when the frame was received.
 */
void time_sync_on_ref(uint64_t master_us, uint32_t local_cycles)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	/*
	 * Every REF is transmitted on both buses, so this node sees each one
	 * twice, a few hundred microseconds apart. The copies carry the same
	 * L_k, so the second contributes no ratio sample, but letting it
	 * overwrite F_k would shorten the next measured interval by the gap
	 * between the buses and inflate the ratio by that gap over one
	 * heartbeat. On the bench that read as 7800 ppm of drift and a 150
	 * microsecond correction, which was the inter-bus gap and nothing else.
	 * Keeping the first copy also keeps the one with the least transport
	 * delay, which is what the delay estimate assumes.
	 */
	if (state.have_last && master_us <= state.last_master_us) {
		bool new_master = state.last_master_us - master_us > MASTER_JUMP_US;

		if (!new_master) {
			k_spin_unlock(&lock, key);
			return;
		}
		/* A different master: drop the old timeline, keep the ratio,
		 * which describes this node's crystal and is still valid.
		 */
		state.have_last = false;
		state.have_anchor = false;
	}
	if (!state.have_anchor) {
		state.anchor_master_us = master_us;
		state.anchor_cycles = local_cycles;
		state.have_anchor = true;
	} else if (master_us - state.anchor_master_us > RATIO_WINDOW_MAX_US) {
		/*
		 * References stopped for long enough that the 32 bit cycle
		 * counter may have wrapped inside the window, which would make
		 * the elapsed local time meaningless. Start a fresh window.
		 */
		state.anchor_master_us = master_us;
		state.anchor_cycles = local_cycles;
	} else if (master_us - state.anchor_master_us >= RATIO_WINDOW_US) {
		/* Unsigned subtraction, so the 32 bit wrap takes care of itself. */
		uint32_t elapsed_cycles = local_cycles - state.anchor_cycles;
		uint64_t local_us = time_sync_cycles_to_us(elapsed_cycles);
		uint64_t master_delta = master_us - state.anchor_master_us;

		if (local_us != 0) {
			int64_t sample = (int64_t)((master_delta <<
						    TIME_SYNC_RATIO_SHIFT) / local_us);

			if (sample > TIME_SYNC_RATIO_ONE - RATIO_TOLERANCE &&
			    sample < TIME_SYNC_RATIO_ONE + RATIO_TOLERANCE) {
				if (!state.locked) {
					state.ratio = (int32_t)sample;
					state.locked = true;
				} else {
					state.ratio += (int32_t)
						((sample - state.ratio) >> RATIO_SHIFT);
				}
				/*
				 * Microseconds the ratio corrects over one
				 * window, so the printed number reads directly
				 * as drift per second.
				 */
				state.correction_us = (int32_t)
					(((int64_t)RATIO_WINDOW_US *
					  (state.ratio - TIME_SYNC_RATIO_ONE)) >>
					 TIME_SYNC_RATIO_SHIFT);
			}
		}
		/* Close this window and open the next one here. */
		state.anchor_master_us = master_us;
		state.anchor_cycles = local_cycles;
	}
	state.base_us = master_us + delay_us;
	state.last_master_us = master_us;
	state.last_cycles = local_cycles;
	state.have_last = true;
	k_spin_unlock(&lock, key);
}

/** @brief Report whether a global timeline has been established.
 * @return True once two references have been received.
 */
bool time_sync_locked(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	bool locked = state.locked;

	k_spin_unlock(&lock, key);
	return locked;
}

/** @brief Read the current global time.
 * @param now_cycles Cycle counter read at the moment of interest.
 * @return Global time in microseconds, or 0 when no reference has arrived.
 */
uint64_t time_sync_global_us(uint32_t now_cycles)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	uint64_t global = 0;

	if (state.have_last) {
		uint64_t since = time_sync_cycles_to_us(now_cycles - state.last_cycles);

		global = state.base_us +
			(((uint64_t)since * (uint32_t)state.ratio) >>
			 TIME_SYNC_RATIO_SHIFT);
	}
	k_spin_unlock(&lock, key);
	return global;
}

/** @brief Read the smoothed drift ratio in Q16.16.
 * @return Ratio, where TIME_SYNC_RATIO_ONE means no drift.
 */
int32_t time_sync_ratio(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	int32_t ratio = state.ratio;

	k_spin_unlock(&lock, key);
	return ratio;
}

/** @brief Report the correction the ratio applied over the last interval.
 * @return Correction in microseconds.
 */
int32_t time_sync_correction_us(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	int32_t correction = state.correction_us;

	k_spin_unlock(&lock, key);
	return correction;
}
