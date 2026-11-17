> Historical pre-relay assumptions; superseded by ANALYSIS.md and RELAY.md.

# Copyable first-pass analysis — assumptions, not measured results

These paragraphs and tables are a starting point for the shared report. The
implementation and assumptions are in FOUNDATIONS.md; TASK_GRAPH.md has the base
graph. Forwarding is intentionally absent. No firmware builds, hardware runs,
WCET measurements, or deadline verification were performed for this revision.

## Foundations paragraph

We use the Lab 3 template task set on N1 (Pi cockpit), N2 (front/steering) and N3
(rear/drivetrain). Both MCP2515 receive workers and all STM32 application tasks
use fixed-priority preemptive scheduling. The Pi uses a nonblocking event loop,
whose latency must be measured rather than treated as a hard real-time bound.
All catalog messages are sent on both independent 500 kbit/s buses, and receivers
act on the first valid copy. Per-bus heartbeat timestamps are recorded before
deduplication. Outputs are LEDs and PWM/DIR test points; N3 PID uses explicitly
simulated feedback. No physical actuator performance is claimed.

## Why these priorities/resources?

Receive workers and dispatch have the highest thread priorities so that control
frames reach output tasks promptly. N3 DRIVE precedes blink and PID because the
brake/throttle chain has the tightest deadline. N2 blink precedes steering because
its local deadline is shorter. Periodic HB/FORCE and supervision run below the
output tasks. DRIVE and PID share a priority-inheriting output mutex; PID computes
outside that mutex and rechecks command version and fault state before committing.
Short spinlocks protect snapshots. Both CAN controllers contend for SPI1, so a
lower-priority SPI transaction can block an arriving high-priority receive path.

The mutex is not the only source of blocking: interrupt-disabled sections, the
SPI driver and controller locks must also be bounded. These are not established
by setting application priorities alone. Fault recovery has a different, potentially
longer blocking path and needs separate analysis/measurement.

## SPI calculation

Requested maximum SPI clock = 4 MHz. STM32 SPI1 source = 84 MHz; the supported
power-of-two divisor gives 84/32 = 2.625 MHz. A 14-byte read takes
`14 * 8 / 2.625 MHz = 42.67 us` on the wire. At a hypothetical true 4 MHz it
would take 28 us. The frame-read transaction is only part of CAN_RX execution;
status reads, flag clearing, locks, IRQ handling and dispatch add work.
The Pi's actual SPI frequency is independently measured.

## Node response-time method

Use `R_i = C_i + B_i + sum(ceil((R_i + J_j)/T_j) C_j)` until convergence.
The handout's no-jitter formula is the special case J=0. A conservative burst
model avoids the incorrect assumption that an entire bus delivers only one frame
every 20 ms: all seven catalog entries can contribute to a window, and dual CAN
doubles receive/completion work. Each 80 us composite budget is provisionally split
as IRQ 5 us + driver 55 us + dispatch 20 us. These segments are not added again
as an extra 80 us task. Same-priority A/B driver work is aggregated. RX/TX-completion
interrupt cost must be checked in measurements; error storms are outside this
healthy-channel envelope.

`tools/analyze_demo.py` generates the full N2/N3 tables and bus table. It uses:

- Seven potential message events per bus per 20 ms as a conservative burst;
  FORCE actually has a 50 ms period. A nine-event sensitivity case models three
  independent FAULT sources rather than one aggregate source.
- Provisional 4 ms receive-release jitter envelope; verify it against final sender,
  controller and bus delays. The assumed Pi release jitter is only 0.5 ms.
- SPI/output blocking budget 50 us, small critical-section budget 5 us. Neither
  is a measured bound; recovery-mode blocking is not included.
- STEER/DRIVE 50 us; BLINK 20 us; PID 200 us; HB/FORCE TX 120 us; HB_MON 30 us.
  The single-bus pass retains the conservative two-bus TX cost rather than halving it.
- TEST/FAULT 140 us = 20 us handler + 120 us dual send, modeled at 20 ms to cover
  software fault notifications as well as the 50 ms physical button debounce.
- Healthy recovery check 20 us/20 ms; watchdog supervisor 20 us/100 ms.

**Model gap to close before a proof:** idle event-worker wakeups, BLINK's safety
poll in addition to received events, tick/other kernel IRQs, the small post-body
instrumentation tail, and driver error/recovery work require measured budgets and
arrival bounds. The test task can also self-suspend for its FAULT rate limit;
its safe-output release occurs before that wait, while FAULT publication may wait
up to 20 ms. The simple periodic RTA is an illustrative baseline, not a complete
self-suspension analysis. Never label these provisional rows as measured passes.

Selected output from the seven-event baseline (all times microseconds):

| Task / conservative batch | N2 single R | N2 dual R | N3 single R | N3 dual R | Local D |
|---|---:|---:|---:|---:|---:|
| CAN_RX through dispatch, whole burst | 565 | 1125 | 565 | 1125 | 300 per frame |
| STEER / DRIVE | 635 | 1195 | 660 | 1220 | N2 1000; N3 500 |
| BLINK_F / BLINK_R | 585 | 1145 | 635 | 1195 | 500 |
| TEST / FAULT CPU envelope | 820 | 1380 | 820 | 1380 | 10000 local reaction |
| PID_SIM | — | — | 975 | 1535 | 10000 |
| HB_TX | 940 | 1500 | 1140 | 1700 | 20000 |
| HB_MON | 925 | 1485 | 1125 | 1685 | 20000 |
| FORCE_TX | 1090 | 1650 | — | — | 50000 |

A whole-burst bound exceeding a per-frame deadline means this coarse bound cannot
prove that deadline; it does not demonstrate that every frame actually misses.
Several local bounds exceed their deadlines even before measurements. We must
refine message phasing/queue service, measure costs and blocking, and, if needed,
change scheduling or the workload. We do not declare schedulability from low
average utilization. Dual reception can worsen CPU response time even though the
application takes the first copy; unchanged latency requires spare CPU capacity
and suitable scheduling, not merely deduplication.

## Bus response-time calculation

For eight data bytes, worst-case stuffed length from the handout is
`47 + 64 + floor((34+64-1)/4) = 135 bits`.
At 500 kbit/s, `C = 270 us` and bit time is 2 us. A lower-priority frame already
transmitting blocks for at most 270 us. For the lowest-priority FORCE message,
there is no lower-priority message, so B=0 in this catalog.

Ideal ID-priority arbitration baseline; assumed jitters, all times microseconds:

| Message | J | C | B | w | R=J+w+C | D |
|---|---:|---:|---:|---:|---:|---:|
| CTRL 0x010 | 500 | 270 | 270 | 270 | 1040 | 2000 |
| FAULT 0x020 | 2000 | 270 | 270 | 540 | 2810 | 100000 |
| HB_N1 0x100 | 500 | 270 | 270 | 810 | 1580 | 20000 |
| HB_N2 0x101 | 2000 | 270 | 270 | 1080 | 3350 | 20000 |
| HB_N3 0x102 | 2000 | 270 | 270 | 1350 | 3620 | 20000 |
| BLINK 0x200 | 500 | 270 | 270 | 1620 | 2390 | 100000 |
| FORCE 0x300 | 2000 | 270 | 0 | 1620 | 3890 | 50000 |

The assumed 2 ms STM32 sender jitter envelopes the selected seven-event healthy
model; the nine-event sensitivity case can exceed it, and fault rate-limit delay
can add 20 ms. Therefore those cases require new J values and recomputation.
FAULT's 100 ms deadline is roomy but must not hide those assumptions.

Per bus, utilization = `6*(0.270/20) + 0.270/50 = 0.0864 = 8.64%`, below 50%.
Counting three independent FAULT streams gives 11.34%. This does not account for
same-ID error retransmissions: simultaneous FAULT producers with differing data
need a resolved protocol policy. No local buffer-order bound or Pi SocketCAN FIFO
backlog is established by this table. Both must be included before claiming that
this ideal bus RTA describes the real system. Each healthy bus carries the same
catalog in dual mode, so ideal utilization per bus is unchanged; host work doubles.

## End-to-end chains

Do not add sender response twice: the bus R values above already include sender
jitter. CAN_RX includes work after the frame leaves the wire; it is not in the
wire transmission C. The following are deliberately conservative sums using the
whole-burst receive envelope and independently maximized output-task response.

| Chain | Single-bus illustrative bound | Dual-bus illustrative bound | Requirement | Interpretation |
|---|---:|---:|---|---|
| Brake/throttle | 1.040 + 0.565 + 0.660 = 2.265 ms | 1.040 + 1.125 + 1.220 = 3.385 ms | 2 ms | Not established; coarse bound exceeds deadline |
| Steering | 1.040 + 0.565 + 0.635 = 2.240 ms | 1.040 + 1.125 + 1.195 = 3.360 ms | 50 ms | Inside budget under assumptions only |
| Blinker start, slower receiver | 2.390 + 0.565 + 0.635 = 3.590 ms | 2.390 + 1.125 + 1.195 = 4.710 ms | 100 ms | Add up to 20 ms event rate-limit wait |
| Normal blinker edge skew | <=1.200 ms | <=2.320 ms | 1 ms | Coarse upper bound cannot establish synchronization |

Blink skew uses the maximum receiver-side latency relative to a common reception
instant; it is not the sum of both receiver latencies. When nodes act on different
buses, add the difference in bus arrival times as well. The dual-bus row assumes
the same bus supplies the first useful frame at both nodes.

The brake response is the DRIVE update, not the next PID tick. Adding a 10 ms
PID wait would violate the 2 ms command path. Observe PWM_SET and the actual PWM
output: compare-register changes and the next physical PWM edge are distinct.

For heartbeat error fallback, allow the last HB to finish after the button event:
`local reaction + possible final-HB delivery + 60 ms timeout + up to 20 ms poll
+ monitor response + output response`.
Using the dual seven-event assumptions yields roughly
`1.380 + 3.620 + 60 + 20 + 1.685 + 1.220 = 87.905 ms`.
This fits 100 ms on paper but excludes unbounded Linux/recovery delays and must be
measured. The immediate FAULT path is additional; do not rely on it to conceal
missing heartbeat fallback. Normal blinker synchronization does not prove that
locally generated fault hazard phases are synchronized.

## What to measure later

1. Actual SPI clocks on both host types; SPI transaction and lower-priority lock durations.
2. Pure CPU execution per task/ISR, separately from preemption and lock waits.
   PC11 task envelopes and cycle-counter maxima include preemption; they are
   observations of elapsed time, not automatically C_i or a proven WCET.
3. Pi UDP_RX-to-CMD_TX for at least a minute unloaded and under stress: sample
   count, maximum, p99, and which value is used as J. Rate-limit delay belongs here.
   The software timestamp starts after recvfrom returns, so it excludes socket
   backlog and kernel/network arrival-to-userspace wakeup; scope marker has the
   same boundary. State this measurement boundary explicitly.
4. Command -> PWM_SET/DIR and actual PWM, both front/rear blink edges, errors,
   single-branch disconnection and reconnection. Repeat after relay is implemented.
5. No wheel speed, steering angle, physical force or physical PID tracking data
   is needed when those optional devices are absent.

The final report should replace assumptions with evidence, keep any missed or
unproven deadline explicit, and include the Part 4 relay design before claiming
full Lab 3 completion.
