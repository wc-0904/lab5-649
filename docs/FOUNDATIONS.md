# Current foundations: relay-enabled Lab 3

The active sources implement the task set with all-node forwarding. Hardware LEDs were
verified by the user on the preceding version. This relay revision has **not been
built, flashed or hardware-tested by the assistant**. The user controls those steps.

## Hardware and output scope

Pi 4B + two Nucleo-F401REs, two 16 MHz MCP2515 modules per host; CAN A/B at
500 kbit/s. Requested SPI ceiling 4 MHz; expected STM32 actual 2.625 MHz.
Use DEMO_WIRING.md; all existing LED, CAN and PWM pins are unchanged. PC9/CN10 pin 1 provides RELAY_TX on both STM32s (optional extra test point on N3). No motors, H-bridges, servos, encoders or current
sensors are attached. N2 generates servo-style PWM; N3 generates motor-style
PWM/DIR with simulated feedback. FORCE is a signed dummy counter.

## Actual tasks and priorities

Lower numeric priority runs first. RX ISR only wakes the driver. Driver workers
are made preemptive by cmake/preemptive_can.cmake, which generates a build-local
copy rather than editing upstream Zephyr.

| Task | N2 priority | N3 priority | Trigger / resource |
|---|---:|---:|---|
| MCP2515 A/B workers | 0 | 0 | IRQ semaphore -> shared SPI -> RX queue / completion |
| Dispatch | 1 | 1 | RX queue -> validation, heartbeat tracking, dedup, output events |
| CAN_TX_A/B | 2 | 2 | Independent ID-priority queues; one hardware TX each |
| RELAY | 2 | 2 | First valid foreign-source catalog frame -> opposite bus |
| BLINK_F / BLINK_R | 3 | 4 | Ordered commands + 20 ms fault-state poll |
| STEER / DRIVE | 4 | 3 | Latest CTRL or error event -> PWM/DIR |
| TEST_BTN / FAULT | 5 | 5 | Button min50 ms or software failure; FAULT min20 ms |
| PID_SIM | — | 6 | 10 ms absolute period |
| HB_TX enqueue | 6 | 7 | 20 ms absolute period |
| HB_MON | 7 | 8 | 20 ms absolute period |
| FORCE_TX enqueue | 8 | — | 50 ms absolute period |
| Supervisor / optional diagnostics | 10 | 10 | 100 ms; checks all workers then feeds IWDG |

The former CAN_RECOVERY task is removed. Each TX worker performs its own timeout
and recovery; a broken bus does not make the other worker wait for acknowledgment.
Event workers use idle timeouts for watchdog progress. Those wakeups, kernel work,
TX completion callbacks and recovery must be covered in the final analysis.

Pi forwarding is a nonblocking opposite-interface write in the CAN receive handler.
Returned copies of locally originated frames are discarded on every node.
Pi logical tasks share a nonblocking event loop, not a fixed-priority RTOS. Measure
Linux latency rather than assume a hard bound. CTRL and BLINK publications enforce
20 ms minimum spacing; early updates may wait for that limit.

## Resources

- Shared command, version, heartbeat and error state: brief spinlock snapshots.
- N3 DRIVE/PID output state: priority-inheriting mutex. PID computes outside it,
  then rechecks command version/fault before committing. Fault wakes DRIVE directly.
- SPI: Zephyr/controller serialization; include lock and transaction blocking.
- RX queue: 64 frames; relay queue: 32; blink queue: 16 ordered two-byte events.
  Overflow in these queues latches a local failure.
- Each TX queue: 32 entries under a short spinlock, lowest ID first, equal-ID FIFO.
  Overflow/expiry/error counted per bus. One outstanding hardware frame per bus.
- Dedup state: dispatch-owned. Matching ID/seq/payload suppressed on either bus
  for 200 ms. Sequence advances once per logical source frame. Relay leaves bytes
  unchanged. See RELAY.md for reboot/delay assumptions and same-bus policy.
- Watchdog: IWDG approximately one second; feed only when every enabled application
  worker and both TX workers progressed. A peer timeout does not trigger a CPU reset.

TX frames expire after 5 ms from local enqueue or original local receive for a
relay. In-flight timeout leads to abort/restart; driver mode-change time is extra.
No measured recovery guarantee is claimed. Pi setup enables Linux restart-ms=20
and an eight-frame kernel queue. Linux FIFO/backlog behavior remains to be measured.

## Current wire catalog

All frames standard 11-bit, DLC 8. Byte 0 low nibble is per-ID sequence modulo16;
high nibble is zero. Unused bytes zero. CTRL uses CRC-8/ATM (poly 0x07, init0,
no reflection/final XOR) over bytes0..6, CRC in byte7.

| ID | Source | Name | Period/minimum | Bytes1 onward |
|---|---|---|---|---|
| 0x010 | N1 | CTRL | UDP event >=20 ms | steer int16 LE, throttle u8, brake u8, buttons u8, reserved0, CRC |
| 0x020 | N1 | FAULT_N1 | >=20 ms | zone1, reason, zeros |
| 0x021 | N2 | FAULT_N2 | >=20 ms | zone2, reason, zeros |
| 0x022 | N3 | FAULT_N3 | >=20 ms | zone3, reason, zeros |
| 0x100 | N1 | HB_N1 | 20 ms | state, health bits, zeros |
| 0x101 | N2 | HB_N2 | 20 ms | state, health bits, zeros |
| 0x102 | N3 | HB_N3 | 20 ms | state, health bits, zeros |
| 0x200 | N1 | BLINK | state change / 500 ms toggle; >=20 ms | off/L/R/hazard=0/1/2/3, phase0/1, zeros |
| 0x300 | N2 | FORCE | 50 ms | signed dummy counter, zeros |

Unique FAULT IDs are a deliberate deviation from the shared-ID template; record
it and confirm with course staff. Reason codes:1 self-test,2 invalid input,3 queue,
4 I/O. Fault payload zone must match the producer ID. State0=ready,1=error;
health bit0=A healthy,bit1=B healthy. All three hosts must be updated together.

CTRL steer=-1000..1000, pedals=0..100 percent. Lab2 UDP packet is 276 bytes on port
8000, raw axes -32768..32767, pedals decrease when pressed. Buttons retain indices
0..7:3 self-test,4 right-toggle,5 left-toggle; simultaneous new4+5 toggles hazards.
A 20 ms source is assumed. Optional FORCE UDP return is one signed diagnostic byte
to the explicit --force-port; it is not a physical force-feedback controller.

## Error state / simulation

Startup error clears after fresh CTRL and both other zones' HB on at least one
bus. Per-bus HB timestamp updates before dedup and includes relayed reachability.
Missing one bus means degraded, not failed. Bus age threshold40 ms plus monitor20 ms
targets60 ms detection; zone failure uses60 ms age on both buses, allowing80 ms plus
scheduling. CTRL age60 ms also forces safety. Local self-test, invalid input,
queue overflow or I/O failure latches until reset and stops own HB. Communication
errors recover with traffic; a communication-error node keeps its HB to avoid
startup deadlock. Explicit remote fault clears only after a newer peer heartbeat.
Normal blink half-period500 ms is mastered by Pi; local error hazards use250 ms.
Error-hazard phases are not claimed to meet normal-blinker synchronization.

STEER:50 Hz,1..2 ms pulses; error centers at1.5 ms. DRIVE:20 kHz PWM, DIR low for
normal operation; brake/error means DIR high and zero PWM. This is test-point
semantics, not a validated H-bridge protocol. Feed-forward duty follows throttle
immediately; PID runs every10 ms without delaying DRIVE.

Simulated plant speed/duty normalized0..1000:
`speed += (old_duty-speed)/10`; `target=throttle*10`; `e=target-speed`;
`sum=clamp(sum+e,-20000,20000)`;
`duty=clamp(target+e/4+sum/100+(e-old_e)/8,0,1000)`.
Brake/fault resets the controller integrator and suppresses PID writes. Gains are
workload choices, not motor tuning. Physical feedback is absent; software/CAN and
output timing still require measurements. See ANALYSIS.md and RTA_TABLES.md.
