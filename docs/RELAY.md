# All-node forwarding policy and validation

Lab 3 p.15 explicitly asks whether only N2 or every node on both buses should
relay. It does not require N2-only forwarding. This design enables forwarding on
**Pi, N2 and N3** so any node still attached to both buses can provide the bridge.

## Policy

Each node validates every incoming catalog frame, rejects returning copies of its
own messages, records foreign heartbeat reachability per ingress bus, and dedups
before application delivery and forwarding. A first-seen foreign-source frame is
forwarded once to the opposite interface with **unchanged ID/DLC/payload/sequence/CRC**.
There is no heartbeat-failure detection delay before forwarding. Attempting an
isolated destination bus can fail; it does not block submissions on the other bus.
Local latched failure disables forwarding. Communication error alone does not,
otherwise startup or a partition could prevent the bridge from restoring traffic.

| Origin | Messages never re-forwarded by their own source |
|---|---|
| Pi/N1 | CTRL, BLINK, HB_N1, FAULT_N1 |
| N2 | FORCE, HB_N2, FAULT_N2 |
| N3 | HB_N3, FAULT_N3 |

A/B RX workers have priority 0, dispatch 1. Both STM32s have RELAY and independent
CAN_TX_A/B workers at priority 2. Pi forwarding runs as a nonblocking SocketCAN
write in its selector loop; Linux delay is a measured input, not an RTOS bound.
PC9 / CN10 pin 1 toggles on either STM32 when its TX worker attempts a forwarded
frame with can_send. On N3 this is an optional extra test point; existing LED and
required test-row wiring stays unchanged. Pi CAN_TX_A/B markers include forwarded
writes, and its final summary reports successful relay writes to each bus.

## Duplicate window and loop assumptions

The cache key is (ID, 4-bit sequence, complete payload), suppressed on either bus
for **200 ms** from the first accepted copy. Duplicate copies do not refresh that
time. A non-origin node forwards at most once during the window, and the origin
never forwards its own returned frames. With three hosts, a logical source frame
therefore has at most two original transmissions plus two forwarded copies under
this residence-time assumption. Source ownership is inferred from the catalog ID,
not the immediate transmitter; the relayed bytes stay identical.

The 200 ms window replaces 15 ms to cover delayed copies through multiple bridges.
It is below the fastest legitimate sequence reuse interval (16 * 20 ms = 320 ms).
Same-bus repeats inside the window are redundant traffic, not new commands. This
is an explicit choice for the handout's same-bus-repeat design question.
A reboot that repeats matching sequence/payload can suppress updates for up to
200 ms. Unchanged payload and four sequence bits cannot distinguish arbitrarily
old traffic from legitimate wrapped traffic. **This is bounded-delay loop prevention,
not a TTL or a guarantee against arbitrarily delayed/replayed packets.**
STM32 queues expire unsent traffic after 5 ms; Linux queue/controller delays,
reboots and error recovery must be tested against the 200 ms assumption. The host
topology test uses short delivery delays and cannot prove a real-time bound.

Per-bus heartbeat timestamps are updated before dedup. LEDs indicate reachability
through that interface, including forwarded heartbeats. A locally unplugged
interface still loses all heartbeat traffic and lights DEG. Another node may
remain green when forwarding preserves reachability. Returned own heartbeats are
ignored. No new relay flag is inserted: changing payload bytes under the same ID
could cause data-field collisions with an original transmission.

## TX and FAULT policy

Each STM32 controller uses a 32-entry software queue, smallest ID first and FIFO
for equal IDs, with exactly one frame outstanding in hardware. Already submitted
traffic cannot be preempted. The worker waits only for the remainder of the 5 ms
release/receive age budget, then aborts/restarts that controller. Driver mode-change
SPI time is additional; this is not a proven 5 ms last-bit bound. Queue overflow,
expiry and errors are counted. Watchdog supervision includes both TX workers and
RELAY on both STM32s. No delivery guarantee is claimed under overload.

Pi uses nonblocking Linux SocketCAN writes with no userspace retry backlog. The
kernel queue is eight frames and restart-ms=20 in setup_can.py. Linux FIFO/driver
ordering still needs measurement and inclusion in the analysis. A successful
write means queued to the kernel, not acknowledged or delivered on the CAN wire.

FAULT IDs remain unique per producer: N1=0x020, N2=0x021, N3=0x022. Payload zone
must match ID. This is a documented deviation from the handout's shared 0x020;
include it in the report and confirm with course staff. All three hosts must use
the current catalog. Different fault senders no longer arbitrate under one ID.

## Load and timing

Original worst-case nine-stream load is 11.34% per bus. Each of the two non-origin
nodes can add a copy, so a conservative per-bus envelope is **34.02%**. The combined
number across BOTH buses is at most four copies per source message under the
bounded-delay policy; the three-copy per-bus maxima cannot both occur for every
message simultaneously. Error retransmissions are excluded.

The updated RTA script includes both STM32 relay tasks and increased traffic/TX
work. It remains provisional and does not establish 2 ms brake latency. When the
Pi is the bridge, the chain contains an additional Linux receive/forward stage
whose latency must be measured. Front/rear blink skew must include unequal hop
counts. Taking the first copy does not remove CPU work from later copies.

## Hardware checks: exercise every possible bridge

Update both STM32s and pull the Pi files together. Keep terminators on the bus
backbones and keep every module's power/ground attached. Remove only the specified
CANH/CANL branch pairs. Restore all branches between cases.

| Bridge (keep BOTH attached) | First endpoint disconnect | Second endpoint disconnect | Needed forwarding |
|---|---|---|---|
| N2 | Pi B | N3 A | Pi A -> N2 -> N3 B |
| N2 | Pi A | N3 B | reverse of above |
| Pi/N1 | N2 B | N3 A | N2 A -> Pi -> N3 B |
| Pi/N1 | N2 A | N3 B | reverse of above |
| N3 | Pi B | N2 A | Pi A -> N3 -> N2 B |
| N3 | Pi A | N2 B | reverse of above |

In each case, keep the 20 ms wheel stream active. Verify heartbeats from every peer,
command/PWM operation, blinkers and FORCE; the unplugged local DEG indicators
should light. Test self-test/FAULT propagation while partitioned, then reset the
latched node/process. Remove a necessary bridge branch to create a truly missing
route: peers must then enter the safe state. Restore and measure recovery.
Never rerun interface setup during a recovery timing measurement.

On Pi, separate terminals:

```sh
sudo python3 pi/demo/setup_can.py
python3 pi/demo/cockpit.py --no-gpio --duration 130 --csv pi/demo/relay.csv
python3 pi/demo/send_test.py 127.0.0.1 --seconds 120 --throttle 25 --button 5
candump -t d any
```

For Pi LEDs/markers, use the libgpiod environment from README and omit --no-gpio.
No LED rewiring is required. The optional N3 relay marker uses the existing PC9 pin.

Host tests cover all 27 combinations of nonempty A/B attachment sets and all nine
catalog IDs (243 policy cases), checking reachable-node delivery and bounded
forward count. Additional tests exercise Pi forwarding unchanged, own-frame rejection,
local-failure suppression and sequence wrap. These do not build or run Zephyr and
do not establish hardware timing or immunity to delays beyond the cache window.
