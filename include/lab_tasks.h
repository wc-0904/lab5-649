/* SPDX-License-Identifier: Apache-2.0 */
/** @file lab_tasks.h
 * @brief Preemptive template task set and simulated actuator workload.
 */
#ifndef LAB_TASKS_H_
#define LAB_TASKS_H_
/** @brief Initialize outputs, CAN, watchdog and task threads from main.
 * @return Zero on success, negative initialization error otherwise.
 */
int lab_tasks_start(void);
#endif
