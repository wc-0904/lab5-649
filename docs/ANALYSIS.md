# Relay-enabled analysis draft

This supersedes PRE_RELAY_ANALYSIS.md. None of the numerical budgets below are
measured WCETs. No firmware build or hardware test was run for this revision.

## Architecture paragraph for the report

We use fixed-priority preemptive scheduling on N2/N3. Both MCP2515 receive workers
run at priority 0, dispatch at 1, and per-bus transmit workers at 2. N2 and N3 also
run RELAY at priority 2, while Pi forwarding runs in its selector loop. It forwards the first validated copy to the opposite bus
without waiting for failure detection, retaining the original ID, sequence and
payload. Receivers suppress direct/relayed duplicates on either bus within a
bounded 200 ms window. Each STM32 transmit worker selects the lowest queued CAN ID
and keeps only one hardware frame outstanding. Independent bus workers isolate
acknowledgment waits and recovery. N3 PID uses a simulated plant; actual outputs
are LEDs and PWM/DIR test points.

## Catalog and bus load

The shared FAULT identifier is replaced by 0x020/0x021/0x022 for N1/N2/N3 to avoid
simultaneous different payloads with the same arbitration ID. This is a documented
team deviation from the template; obtain course-staff agreement. CTRL, HB, BLINK
and FORCE retain their original IDs. Full policy and limits: RELAY.md.

At 500 kbit/s, worst-case eight-byte standard frame length is
`47 + 64 + floor(97/4) = 135 bits`, so C=270 us and bit time=2 us.
Original nine-stream worst-case load is `8*270/20000 + 270/50000 = 11.34%`.
A per-bus upper envelope adds two forwarded copies of every source stream,
giving `3 * 11.34% = 34.02%`, below the 50% requirement.
This is a worst-case sporadic envelope, not healthy measured traffic; error
retransmissions and controller recovery are excluded.

Use `w_m = B_m + sum ceil((w_m + J_j + 2)/T_j) C_j` and `R_m=J_m+w_m+C_m`.
A forwarded copy shares the original ID: include equal-priority copy interference.
B is 270 us if a lower-priority frame exists, otherwise zero. Sender/relay queuing
jitter must include host scheduling, local queues and the complete first hop.
Do not add sender jitter again when summing a bus R already containing it.

The STM32 queue policy bounds hardware ordering to one selected non-preemptible
frame, but kernel scheduling, SPI blocking and the Pi's Linux FIFO remain relevant.
The ideal ID-priority bus model is not automatically a proof of the implementation.

## SPI and node scheduling

SPI ceiling is 4 MHz; STM32's 84 MHz source divides to 2.625 MHz. A 14-byte read
therefore costs 42.67 us on the wire, excluding status/flag operations and software.
The Pi clock must be measured separately.

Use fixed-priority RTA with bounded release jitter:
`R_i = C_i + B_i + sum ceil((R_i + J_j)/T_j) C_j`.
Aggregate equal-priority TX/relay work and both RX-worker bursts. Receiving two
copies and generating a forwarded copy adds CPU and SPI work; taking the first
copy does not make that work disappear.

`python3 tools/analyze_demo.py` reproduces RTA_TABLES.md. Explicit starting budgets:

| Work | Budget assumption |
|---|---|
| IRQ + RX worker + dispatch | 5 + 55 + 20 = 80 us per possible frame |
| Receive/completion envelope | 54 events/20 ms per node (27 per bus) |
| TX service | 60 us/frame; up to 12 N2 sends or 11 N3 sends/20 ms |
| RELAY enqueue | 10 us per relay; six N2 or seven N3 logical messages/20 ms |
| STEER/DRIVE | 55 us including an allowance for idle wakeup |
| BLINK | 40 us including event work plus safety poll |
| TEST/FAULT enqueue | 30 us/20 ms; physical button debounce remains 50 ms |
| PID_SIM | 200 us/10 ms |
| HB/FORCE enqueue | 10 us at 20/50 ms |
| HB_MON | 30 us/20 ms |
| Supervisor | 20 us/100 ms |
| Small critical-section blocking | 5 us |
| SPI/output blocking | 50 us |
| Higher-priority arrival jitter | Provisional 10 ms envelope |

These are deliberately coarse, unverified assumptions. They do not establish
SPI-lock WCET, ISR/error bursts, dispatch backlog, self-suspension interference,
queue expiry/loss, or recovery-mode delays. The receive envelope conservatively
charges traffic even when a local frame would produce completion rather than RX.
The normal-case CPU and wire models must be reconciled with actual measurements.

## Four end-to-end chains

| Chain | Path / required additions | Requirement |
|---|---|---|
| Brake/throttle, partitioned | Pi UDP/CTRL -> first bus -> N2 RX/dispatch -> RELAY + TX -> second bus -> N3 RX/dispatch -> DRIVE -> PWM/DIR | 2 ms |
| Steering | Pi UDP/CTRL -> available bus -> N2 RX/dispatch -> STEER | 50 ms |
| Blinker | Pi BLINK -> direct path or N2 relay path -> each RX/dispatch -> BLINK output | start 100 ms; edge skew <=1 ms |
| Error | TEST -> immediate local safe outputs / FAULT forwarding, plus independent HB-stop fallback -> peers' monitors -> safe outputs | local 10 ms; global 100 ms |

The two-hop brake chain is not established at 2 ms. The script prints an
illustrative sum which exceeds that budget; this is a failed proof, not a measured
miss. Queue expiration can also drop frames rather than deliver them late, so
latency analysis alone cannot establish functional delivery.

Steering's 50 ms and blink-start's 100 ms provide more margin, but actual bounds
remain conditional on measured Pi/CPU/queue behavior. For blink synchronization,
compare total arrival-to-output times at front and rear, including the additional
bus hop where applicable. The simple common-reception-time skew argument from the
single-bus case does not apply when N3 receives a relayed edge after N2's direct edge.

HB fallback is at least a 60 ms age threshold plus up to 20 ms polling phase, then
monitor/output response. Add delivery time of any final in-flight heartbeat and
relay delay. The immediate FAULT path complements this fallback; it does not
replace the requirement to measure it. Locally generated error hazards are not
synchronized by the normal master-edge mechanism.

## Measurement and remaining claims

Measure SPI frequency, execution excluding preemption, lock blocking, queue loss,
Pi UDP_RX-to-CMD_TX max/p99 under normal and stressed load, both PWM update paths,
front/rear blink skew, heartbeat failure detection and reconnect timing.
PC11 and software cycle maxima include preemption/waits and are not automatically
pure C_i. Optional console prints are intrusive and must be off for final timing.
Pi timestamps begin after UDP dequeue and socket writes do not mean wire completion.

No physical speed, steering-angle, encoder or force measurement is needed with
simulated actuators. Forwarding is implemented in source; neither functional
hardware success nor compliance with every deadline has been established.


All-node forwarding supports any remaining physical route through a two-bus node,
including Pi as bridge. Recompute measured chains for the selected bridge: a Pi
bridge introduces another unbounded Linux scheduling stage. The source and the
other two nodes can create at most four physical copies per logical message
(two original + one per non-origin relay) while the 200 ms cache entry is valid.
A per-bus three-copy envelope does not imply that both buses simultaneously see
three copies of every frame. Late copies older than the cache window or reboot
can violate the copy-count assumption; do not claim an arbitrary-delay guarantee.
