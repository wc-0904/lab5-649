# Lab 5 remaining work and open design decisions

Status as of 2026-11-21. The handout is due 2026-11-18 23:59, but late days cover the
schedule, so this list is ordered by dependency rather than by the calendar.
P0 through P6 are done and verified on hardware; P7 through P15 remain. The
paragraph below describes the starting point this work was built from.
What existed at the start was the Lab 3 firmware this
directory was stripped down to: dual-CAN transport, the preemptive task set, the Lab 3
CAN catalog, and the Pi cockpit. See `CLAUDE.md` for the spec and the current state.

## How this order was chosen

Late days cover the schedule, so this is ordered by dependency and by what can actually
be verified at each step, not by which points are cheapest to bank. Four constraints set
the sequence:

- **Nothing is blocked on anyone outside the team.** Course staff are unavailable, so
  every handout ambiguity is resolved in the assumptions section at the end of this file.
  P0 is agreeing to those positions, not waiting on answers. The cost of being wrong is
  highest for the framing decisions, which is why they are settled before any code.
- **The time master is whoever Raft says is leader.** Time sync therefore sits after the
  agent has real role state, and the election sits after time sync, so the continuous
  global-time estimate exists before handover needs it. Building time sync against a
  hardcoded leader and retrofitting handover means building it twice.
- **Raft grants a vote by comparing logs**, so the election cannot be written before the
  log and replication exist. P3 and P4 are hard prerequisites for P9.
- **The log and state machine are pure and host testable.** They come before the
  networked replication that drives them, so replication debugging is not also state
  machine debugging.

Each phase ends in an observable check rather than in code that compiles. With time
available, run that check before starting the next phase; most of them are a `candump`,
a console comparison across three nodes, or a scope capture.

Only one pair is genuinely independent: P6 to P8, the time path, needs nothing from P3
to P5 beyond a leader that transmits REF. If more than one person is working, that is
the split. Otherwise run it straight through.

## Phase order

### P0. Agree the assumptions, then settle the framing decisions

| # | Task | Done when |
|---|---|---|
| 0.1 | Read "Working assumptions where the handout is ambiguous" and agree with the team | All nine ambiguities already have a position. Disagree now, not during P5 |
| 0.2 | Settle **D1** to **D4**, **D12** and **D28**: node IDs, config distribution and format, CAN ID allocation, which bus carries Raft, new pins | Written down. Every later phase encodes these, and changing them later touches all three nodes |
| 0.3 | Record each decision and its reasoning as it is made | The report's three sections are design arguments; capturing the reasoning now is cheaper than reconstructing it later |

### P1. Foundations  [DONE, flashed and verified on both Nucleos 2026-11-18]

All six items are implemented, built against Zephyr 4.4.99 with SDK 1.0.1, and
flashed to both boards. Confirmed on hardware from the boot banner:

- The generated config reaches both boards and `hash=825e3115` is identical on
  each, so the byte-identical requirement holds in practice, not just in theory.
- Identity resolves through the generated name table: node 2 reports `steering`,
  node 3 reports `drivetrain`.
- Per-node identifiers derive correctly and do not collide. N2 gets ACK 0x046,
  REQ_VOTE 0x04a, VOTE 0x04e, CLIENT 0x052; N3 gets 0x047, 0x04b, 0x04f, 0x053;
  REF 0x008, APPEND 0x040 and REDIRECT 0x054 are shared.
- `Task startup: 0` on both. `lab_tasks_start()` returns early on any init
  failure and calls `lab_io_init()` first, which configures all eighteen pins,
  so zero proves PB12..PB15 and PA0 configure as outputs and that CAN init
  accepted all nine filters, seven Lab 3 plus two Lab 5 masked, on both buses.

**Lab 3 regression passed on the rebuilt rig, 2026-11-19.** With `cockpit.py` and
`send_test.py` running, `candump` showed `010` CTRL at ~20 ms decoding correctly
(steer 0, throttle 0x19 = 25, valid CRC), `100`/`101`/`102` heartbeats at 20 ms
with byte 1 `00` (error cleared) and byte 2 `03` (both buses healthy) on all
three nodes, `200` BLINK carrying the left-blinker request, `300` FORCE at 50 ms,
and no `020` FAULT. That closes the open risk from P1: the `dispatch_task()`
branch and the two masked RX filters do not disturb Lab 3 traffic.

Also confirmed on the bench: the Pi's two MCP2515s enumerate and pass
`can_check.py check` at 500 kbit/s, the config hash `825e3115` is identical on
all three nodes including the Pi's runtime parse, and heartbeat periods settle to
20 ms once the cluster is whole.

Still unobserved: the boot pin walk on a scope, and REF_RX driven by a real REF
frame. Both need the five Lab 5 outputs wired to test points, which is not done
yet and is not needed before P2.

**Bench finding that feeds D24.** Every logical frame appears **four times** on
the wire: the sender transmits on both buses, then each of the other two nodes
relays one copy. Forwarding is unconditional on all three nodes. With Lab 5
adding `REF` at 20 ms, `APPEND`, per-follower acks and client traffic on top,
`REF` would be queueing behind three relayed copies of everything else. The
relay policy probably has to become conditional, driven by `getNetwork()`
reachability, rather than always-on. Decide this before P11, not during it.

**Deviation from 1.5 as written.** `lab_slot()` and `lab_valid()` were *not*
extended to the Lab 5 identifiers. `lab_slot()` indexes the Lab 3 dedup cache
in `lab_tasks.c`, which is dimensioned `cache[10][16]`, and `lab_can.c` keeps a
`sequence[7]`; widening either to cover 14 more identifiers would change Lab 3
behaviour for no benefit. Lab 5 identifiers live in their own namespace instead
(`enum lab5_id`, `lab5_is_consensus()`), and `dispatch_task()` branches them out
before the Lab 3 validation so they are not counted as malformed.


| # | Task | Where | Done when |
|---|---|---|---|
| 1.1 | `git init`, `.gitignore` (build dirs, `__pycache__`, `.cache`), first commit | repo root | GitHub link exists for the report, worth 4 points |
| 1.2 | Rename `project(lab3_demo)` and the `lab3_*` strings | `CMakeLists.txt`, `setup.sh` | Clean build under the new name |
| 1.3 | Node identity table from **D1** | `lab_protocol.h`, `CMakeLists.txt` | One name table used by logs, CAN IDs and prints |
| 1.4 | Config file and its path onto the STM32, from **D2** and **D3** | new `config/`, generated header | Same keys and values readable on all three nodes, byte-identical |
| 1.5 | Extend the CAN catalog with REF, APPEND, APPEND_ACK, REQ_VOTE, VOTE, client request and redirect, from **D4** | `lab_protocol.h/.c` | `lab_slot()` and `lab_valid()` cover the new IDs, host tests extended |
| 1.6 | Add LEADER, SYNC, REF_RX, HZ and PPS outputs, from **D28** | `lab_io.h/.c`, overlay, `WIRING.md` | Pins assigned, scoped and documented |

### P2. Default-leader boot and the two periodic frames, checkoff step 2  [DONE, verified on hardware 2026-11-20]

Wire formats settled (**D5**): REF carries a 48 bit microsecond master clock in
bytes 0..5, measured from `LAB5_EPOCH_US` (2026-01-01T00:00:00Z), and a 16 bit
cycle counter in bytes 6..7. The epoch offset is not cosmetic: a CLOCK_REALTIME
value in 2026 is about 1.8e15 microseconds, needs 51 bits, and was silently
truncating to a 1973 date on the wire until a candump decode caught it. Measured
from the epoch the field spans 8.9 years, which keeps microsecond resolution and
the full 16 bit counter. AppendEntries fits eight bytes without fragmentation, which also
answers most of **D6**: term, prevLogIndex (16 bits, covering log_size 30000),
prevLogTerm, leaderCommit, entry count, and the single entry's event. An entry's
index is prevLogIndex + 1 and its term is the leader's, so only the event type
needs to travel.

`lab_can_send()` silently drops any identifier outside the Lab 3 catalog, so
Lab 5 frames go out through `lab_can_send_lab5()` on the STM32 and
`send_lab5()` on the Pi, neither of which stamps a sequence byte.

Verified on the bench: `candump -t d any` shows `008` every 20.0 ms and `040`
every 100 ms on **both** buses, with the cycle counter contiguous and nothing in
`041`..`054`, so no `REQ_VOTE`. The cockpit reported `refs=3000 appends=600
skipped=0` over 60 seconds, exactly 50/s and 10/s. Both Nucleos printed
`raft: SYNC to cockpit, cycle=N master=1795217266075343 us`, which decodes to
2026-11-20, and byte 2 of every heartbeat read `03`, so all three nodes see both
buses healthy.

Two bugs the bench caught, both now fixed: the 48 bit clock truncated a
CLOCK_REALTIME value to a 1973 date until it was measured from `LAB5_EPOCH_US`,
and a follower reported 63635 missed references when the leader restarted its
cycle counter, until a backward jump was recognised as a restart.

REF and APPEND use an absolute schedule that **skips rather than bursts** when a
tick is late: a skipped REF is better than two REFs milliseconds apart, because
the follower's drift ratio in P6 is computed from the interval between
consecutive receipts.


| # | Task | Where | Done when |
|---|---|---|---|
| 2.1 | Raft role state: `currentTerm`, role, `leaderId`; boot in term 1 with `default_leader` and hold no election | new `src/raft_agent.c/.h`, `pi/demo/` | LEADER lit on the Pi only |
| 2.2 | Pi agent process structure, keeping slow work off the CAN receive path, from **D29** | `pi/demo/cockpit.py` | Agent runs without disturbing the existing cockpit timing |
| 2.3 | REF transmit every 20 ms on the leader, carrying master time and the cycle counter, from **D5** | `raft_agent.c`, `cockpit.py` | `candump -c any` shows REF at 20 ms |
| 2.4 | Empty APPEND every 100 ms on the leader | same | APPEND at 100 ms, and no REQ_VOTE anywhere on either bus |
| 2.5 | REF receive path and REF_RX toggle on followers | `lab_tasks.c` | SYNC on all three, REF_RX toggling |
| 2.6 | Confirm REF wins arbitration against APPEND and CTRL under load | bench | Sync period does not jitter when a log entry is in flight |

### RESUME HERE, 2026-11-21 (late)

P3 to P6 are written and verified on hardware. **P7's STM32 half is written and
builds clean on both targets, but has never been flashed or scoped.** That is the
next thing to do, and it is the step that finally measures `DELAY_US` and the
receive timestamp jitter.

#### Where P6 left the numbers

Drift settles at about **+12 ppm on N2 and +29 ppm on N3** and holds. The residual
is Q16.16 quantization at 15.26 ppm per LSB, and the occasional outlier is common
mode, appearing on both nodes at once, so it belongs to the Pi's transmit path and
not to either crystal.

#### What P7 added, not yet observed

- `src/pps.c` and `include/pps.h`. TIM5 free runs at 1 MHz with channel 1 driving
  PA0 in output compare mode, so the comparator moves the pin and no instruction
  executes at the edge. The interrupt runs after each edge only to reprogram: the
  rising edge schedules the fall 10 ms later, the fall schedules the next second
  from the current estimate, so the schedule tracks the master every second rather
  than free running from the first edge.
- The deadline is the handout's inversion, `F_deadline = F_k + (T - base) / ratio`,
  computed once per second in `arm_next_edge()`. There is no polling anywhere.
- **The timestamp source moved from `k_cycle_get_32()` to the TIM5 counter**, so
  F_k and the compare register share one time base and arming needs no conversion.
  Resolution drops from 12 ns to 1 us, which is far below the measured jitter.
  `time_sync_set_tick_hz()` makes the rate explicit, and the host tests drive it at
  84 MHz on purpose so a hard coded 1 MHz would fail there.
- **The per edge console line is printed from the drift task, not the interrupt.**
  It was in the ISR first and that was wrong: the line takes milliseconds at 115200
  baud, and blocking an interrupt that long once a second would hold off the CAN
  receive workers and corrupt the timing being measured. The ISR records, a thread
  prints.
- `&timers5` is enabled in the overlay. No driver binds it; `pps.c` takes the
  register base and clock specifier from the node and configures PA0 as TIM5
  channel 1 itself, because the timers binding carries no pinctrl property.

#### Do this first when you come back

1. Flash both boards, start the cockpit, and confirm a `pps:` line appears once a
   second on each STM32 alongside the existing `sync:` line. First edge should
   arrive within about two seconds of the first REF.
2. Scope PA0 on both Nucleos at once and measure the spread between the rising
   edges. That number is the whole point of P7 and feeds the 13 point drift
   section. Expect it to be dominated by the 50 microseconds of receive timestamp
   jitter measured in P6, not by the ratio.
3. With a spread in hand, adjust `DELAY_US` in `src/time_sync.c`. It is currently
   300, which is a calculation from frame bit count and SPI read time, never a
   measurement. A constant offset between two nodes' edges is this value being
   wrong; the spread around it is jitter and will not move.
4. Only then decide **D22**, PPS on the Pi. Python cannot do a hardware compare,
   and the Pi is the default master, so its edge is the baseline every STM32 edge
   is compared against. Options are a small C helper, pigpio, the kernel
   `pps-gpio` driver, or accepting Python jitter and documenting it. Deliberately
   left open until the STM32 spread is known, because that spread decides whether
   Python's jitter is tolerable or has to go.

#### Bench facts worth not rediscovering

- **Flash both boards while both are attached** by selecting the adapter on USB
  topology, not serial. `west flash -i <serial>` silently falls back to the first
  adapter here, which once put the N3 image on N2.

  | | N2 | N3 |
  |---|---|---|
  | USB location | `1-1` | `1-2` |
  | ST-LINK serial | `0671FF505487525067172616` | `0673FF514988525067191614` |
  | console | `/dev/ttyACM0` | `/dev/ttyACM1` |

  ```sh
  source ./setup_win.sh
  west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2
  west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3
  west flash -d build-demo-n2 -r openocd --no-rebuild --verify \
    --cmd-pre-init-flash "adapter usb location 1-1"
  west flash -d build-demo-n3 -r openocd --no-rebuild --verify \
    --cmd-pre-init-flash "adapter usb location 1-2"
  ```

  The ST-LINK is invisible to WSL until attached with `usbipd` from an elevated
  PowerShell. Re-map the locations after moving cables between ports with:
  `for d in /sys/bus/usb/devices/*; do [ -f "$d/idVendor" ] && [ "$(cat $d/idVendor)" = 0483 ] && echo "$(basename $d) $(cat $d/serial)"; done`

- **`west flash --no-rebuild` does not build.** Running only the flash commands
  after a source change silently reflashes the previous binary. This cost one
  debugging round on P6. Always run `west build` first and confirm the verify
  sizes changed.

- On the Pi: `sudo python3 pi/demo/setup_can.py` once, then
  `python3 pi/demo/cockpit.py --no-gpio --duration 120`. The Pi now has the P6
  code once it pulls, so it prints its own `sync: ... master=self` line to compare
  global seconds against.

- Host tests: `python3 -m unittest discover -s tests -p "test_*.py"`, 103 cases.

### P3. Log and state machine, host testable  [IMPLEMENTED, host tested]

Settled here:

- **D6/D7, log entry and storage.** An entry's index is its position, so only
  term and event are stored, two bytes each. The STM32 ring holds 4096 entries
  for 8 KB, against 53 KB of free SRAM; the Pi keeps the whole log. The
  configured `log_size` stays 30000 and the ring is the documented deviation.
  When the ring fills, the oldest entry is dropped and the first retained index
  advances, which replay respects.
- **Event encoding.** Twenty values, laid out systematically so node and bus
  come back with a shift: `0x10 + node` up, `0x18 + node` down, and
  `0x20`/`0x28` plus `(node - 1) * 2 + (bus - 1)` for links. Twenty values need
  five bits, which is why one entry fits in the AppendEntries byte reserved
  for it.
- **D9, `getNetwork()` representation.** Two bitmasks plus a failure flag: bit
  n-1 of `nodes` is node n, bit `(n-1)*2 + (b-1)` of `links` is node n on bus b.
  Three bytes in all, and `network_print()` renders it as one line such as
  `net nodes=CSD links=121.12 mode=OK`. The demo compares that line character
  for character across the three consoles.
- **D8, replay.** `network_replay()` rebuilds from the oldest retained entry
  every time, so the graph is a pure function of the log by construction rather
  than by discipline. Incremental application stays available as an
  optimisation if profiling ever asks for it.

`raft_log.c` and `network_state.c` are pure C with no Zephyr dependency, so the
host tests compile and run the same code the firmware will. They are in
`target_sources` ready for P4, but nothing in the firmware calls them yet, so
they cost no RAM until a `struct raft_log` is declared.

Verified by 15 host tests, including 200 random logs replayed through both the
C implementation and the Python one with identical rendered graphs. That is the
Part 4 determinism requirement across a heterogeneous cluster, where "any node"
spans C firmware and Python.


| # | Task | Where | Done when |
|---|---|---|---|
| 3.1 | Log entry encoding `(term, index, event)` and the storage ring, from **D6**, **D7**, **D8** | new `src/raft_log.c/.h` | Fits the chosen frame and memory budget |
| 3.2 | `addLink`, `dropLink`, `addNode`, `dropNode`, `getNetwork` as a pure replay, from **D9** | new `src/network_state.c/.h` | Same log replays to the same graph on any node |
| 3.3 | Printable `getNetwork()` | same | Three consoles can be compared side by side at the demo |
| 3.4 | Host unit tests for replay determinism and for the encoding round trip | `tests/` | Extends the existing 21 passing cases, all green |

### P4. Replication, commit and catch-up  [DONE, verified on hardware 2026-11-20]

Verified on the bench. The leader appended five bootstrap entries and committed
all five. Both Nucleos printed the same five `store idx=N term=1 EVENT` lines in
the same order at the same indices, then converged to the identical graph:

```
raft: commit idx=5 net nodes=CSD links=12.... mode=OK
```

character for character on all three nodes. That is Part 4's determinism
requirement shown on hardware across a heterogeneous cluster, and it is also the
artifact checkoff step 4 compares: one entry per change, at the same index, on
every console. Commit riding one index behind store is correct, since the leader
cannot commit until a majority acknowledges and the new commit index travels on
the next AppendEntries.

Settled here:

- **D10, acks.** A CAN frame ack only means someone heard the frame, so each
  follower sends its own `APPEND_ACK` frame carrying its term, its match index
  and the outcome. The identifier says which follower it came from, so the
  payload does not have to.
- **One broadcast APPEND, built for the furthest behind follower.** There is a
  single `0x040`, so the header cannot differ per follower; the leader uses the
  minimum `nextIndex`. Followers already holding that entry accept it
  idempotently, which is exactly why `raft_log_accept()` was written to be
  idempotent. On this bus every append arrives two to four times anyway, once
  per bus plus relayed copies, so that property was needed regardless.
- **Append cadence.** Empty `APPEND` every 100 ms when idle, which is what
  checkoff step 2 measures. While a follower is behind, the leader also sends at
  the heartbeat rate so catch-up is not one entry per 100 ms.
- **D11, persistence.** Nothing is written to flash. A follower catches up over
  `AppendEntries`, and `RAFT_APPEND_FORGOTTEN` names the one case that cannot
  work: `prevLogIndex` having fallen out of the 4096 entry ring, where backing
  up does not help and a snapshot would be needed. No demo scenario reaches it.
- **Commit rule.** The highest index stored by a majority **and created in the
  leader's current term**, which is what stops a later leader committing a
  predecessor's entry by counting alone.

The Pi subscribed to exactly the seven Lab 3 identifiers, so it could not have
received any acknowledgement. Its socket filters now carry the same masked
consensus range the firmware uses, plus an exact filter for REF.

A minimal client bootstraps the log so there is something to replicate: the
leader records its own node and links at startup, and records each peer on first
sight of its heartbeat. Link detection, debounce and the redirect are P5.


| # | Task | Where | Done when |
|---|---|---|---|
| 4.1 | AppendEntries with `prevLogIndex`, `prevLogTerm`, `leaderCommit` | `raft_agent.c` | Follower append works over CAN |
| 4.2 | Per-follower application-level ack, from **D10** | `raft_agent.c` | A CAN frame ack is never treated as stored |
| 4.3 | Commit on majority, then ack the client | `raft_agent.c` | Client blocks until step 6 of Figure 2 |
| 4.4 | Election and append debug prints, required by the `Debug` config key | all agents | Prints visible on all three consoles |
| 4.5 | Catch-up for a rejoining node, from **D11** | `raft_agent.c` | A node restarted mid-run converges to the same log |
| 4.6 | Check the commit path against the watchdog, from **D30** | `lab_tasks.c` | A client blocking until commit never trips `iwdg` |

### P5. Client, link detection and routing, checkoff step 4  [DONE, verified on hardware 2026-11-20]

Verified on the bench. Ten jumper transitions on N2's bus B produced exactly ten
log entries, perfectly alternating DOWN and UP with no chatter, at the same
index on all three consoles, with `term=1` throughout and only N2 ever sending a
client request. That is checkoff step 4's wording satisfied: one entry per
change at the same index everywhere, DEG LEDs as in Lab 3, term unchanged.

The 60 ms down debounce proved right on real jumper bounce.

A second bench run then exposed something a symmetric debounce cannot fix. With
the stub left out the log kept growing, reaching index 77 still alternating
DOWN and UP at about 1.7 entries a second with nobody touching the jumper. A
disconnected branch does not sit quietly in bus off: the controller recovers
automatically after 128 by 11 recessive bits, returns to error active with a
cleared transmit counter, and fails again on its next transmission. Each half of
that cycle outlasts any debounce short enough to be useful.

Final state verified: with everything connected the log reaches index 9 and all
three consoles render `net nodes=CSD links=121212 mode=OK`, every node and every
link present, same index, same text. Each Nucleo reports both of its own links
and only its own.

A fourth bench run found a lost update. A node carries one pending client
request at a time, and both of its links settle together at boot, so the second
link marked itself reported, found the pending slot occupied, and was dropped
with no later retry. Both Nucleos reported bus 1 and never bus 2, showing as
`links=121.1.`. A change is now recorded as reported only once it has actually
been queued.

A third bench run found a cross-node bug. Every reply shares one
identifier and clients number their own requests independently, so both
followers had request id 1 outstanding at once; the leader's acknowledgement to
N2 cleared N3's pending write and drivetrain's links never reached the log, which
showed as `links=121...` with everything connected. The reply now names the node
it answers, and a client ignores replies addressed to anyone else.

Two changes fix the oscillation. Healthy now means error active **with a zero
transmit error count**, not merely "not bus off", so the recovery window does not read as
healthy. And the debounce is asymmetric: 60 ms to report a link down, 750 ms of
continuously clean transmission to report it up. Coming up late costs nothing,
since the checkoff times no entry and only requires one per change.

The run also exposed a gap since fixed: a node reported only link *changes*, so
links that were already up at boot never entered the graph, leaving the rendered
state showing steering's bus 1 and both of drivetrain's links permanently down.
Each node now assumes both its links are down at init, so the first poll reports
them coming up, which is how the leader already bootstrapped its own.

Settled here:

- **D15, who reports a link change: owner-reports-own-links.** Each node reports
  only its own two links, so one physical change produces exactly one entry,
  which is what checkoff step 4 demands. A follower cannot append, so it sends a
  client request to the leader; the leader appends and replicates.
- **D16, detection and debounce.** Detection reads the node's **own** controller
  error state through `can_get_state()`, not the absence of peer traffic, so it
  answers "is my branch connected" rather than "is anyone talking". Pulling a
  node's jumper leaves its transceiver unacknowledged, driving the controller
  error passive and then bus off. A change must hold **60 ms**, three monitor
  periods, before it is reported; undebounced, one jumper pull puts several
  entries in the log because the controller crosses error passive on the way to
  bus off.
- **Duplicate suppression.** The bus relays, so the leader sees each client
  request two to four times. Requests are keyed by (node, request id) and
  answered from the recorded index rather than appended again.
- **Redirect.** A client reaching a follower gets `LAB5_REPLY_REDIRECT` naming
  the leader, so the write path fails closed rather than silently. `0x054`
  carries both the redirect and the commit acknowledgement, distinguished by
  the result byte.
- **Routing.** `network_route_to()` prefers a bus both ends are on, else finds a
  single node bridging them, which is the relayed hop of Part 5.2. Lowest
  numbered bus and relay win, so every node derives the same table from the same
  log. Verified against Lab 3 R4.8 scenario (c): with N1 off B and N3 off A, both
  directions relay through N2.


| # | Task | Where | Done when |
|---|---|---|---|
| 5.1 | Link up and down detection with debounce, from **D16** | `lab_can.c`, `lab_tasks.c` | A jumper pull makes one entry, not several |
| 5.2 | Ownership rule for who reports a link change, from **D15** | client | Exactly one entry per change at the same index on every node |
| 5.3 | Client write to the leader, follower redirect | new `src/raft_client.c/.h` | A client hitting a follower is redirected |
| 5.4 | Routing table distilled from `getNetwork()` | `raft_client.c` | Per destination: which bus, or which relay |
| 5.5 | Single-link fault run: pull one node's A jumper, then B | bench | DEG LEDs as in Lab 3, term unchanged everywhere, logs agree |

### P6. Time synchronization  [DONE, verified on hardware 2026-11-21]

Build the estimate on every node from the start, leader included, rather than only on
followers. P9 depends on that and it is awkward to add afterwards.

All six items are implemented in `src/time_sync.c`, `pi/demo/time_sync.py` and a
`DRIFT` task slot in `lab_tasks.c`, with `tests/test_lab5_sync.py` compiling the C
against a Zephyr stub and comparing it to the Pi byte for byte over drifting,
jittered and wrapping reference sequences. 13 cases, passing. What was settled:

- **D17 timestamp point.** The MCP2515 has no readable timestamp register, so the
  earliest software point is the first statement of the receive callback, which
  runs in the driver's preemptive priority 0 thread. `struct lab_rx` carries that
  cycle count to the dispatch thread, and `raft_on_ref()` uses it rather than the
  uptime it already had, which included the queue wait. The SPI read latency is
  folded into `d`. On the Pi the counter is read at the socket read.
- **D18 one-way delay.** Static, 300 microseconds, computed from the frame: about
  128 bits after stuffing at 500 kbit/s is 256 microseconds on the wire, plus a
  thirteen byte SPI read and interrupt latency. No round trip, as Part 6 requires.
  P7 measures the residual on a scope; this is the starting point.
- **D19 step versus slew.** `clock_settime(CLOCK_REALTIME, ...)` is called exactly
  once, at the first reference, and the clock is then left free running. Stepping
  it every 20 ms would move it backwards whenever the correction is negative and
  break every timer scheduled across the step. All later correction lives in
  `time_sync_global_us()`. `CONFIG_POSIX_TIMERS=y` was added to `prj.conf` for
  this; nothing else in the image uses the POSIX layer.
- **D20 ratio filtering.** Exponential average, weight 1/16, so roughly sixteen
  intervals or a third of a second. A single 20 ms interval is mostly timestamp
  noise: one microsecond of jitter over 20 ms reads as 50 ppm, far larger than any
  real crystal error. Samples more than one percent from unity are rejected
  outright as a missed reference or a restarted master, not drift.
- **Arithmetic.** Everything is a difference taken over at most one heartbeat, so
  the 32 bit cycle counter's wrap never enters the maths and no 64 bit extension
  is needed. The ratio is Q16.16 fixed point, which removes the question of
  floating point in a Zephyr thread and lets the host test be an exact equality
  rather than a tolerance.
- **Continuity.** The leader stamps REF from `master_us()`, which carries a
  `master_offset_us` frozen at the moment a node adopts the master role. It is
  zero on a cold boot and non-zero only after a handover, which is the mechanism
  checkoff step 6's 100 microsecond bound depends on. P9 drives it.

**Found on hardware, 2026-11-21.** The first bench run reported `ratio=7800 ppm`
and `correction=150 us` on both Nucleos: three orders of magnitude more drift than
a crystal has. Every REF goes out on both buses, and dispatch handles `LAB5_REF`
before the dedup check, so `time_sync_on_ref()` ran on both copies. The copies
carry the same `L_k`, so the ratio sample was correctly skipped, but the second
copy still overwrote `F_k` with its later arrival. The next interval then measured
20000 microseconds of master time against 20000 minus the inter-bus gap of local
time, and the ratio absorbed the difference. The 150 microsecond correction was
the gap between the two buses and nothing else.

`time_sync_on_ref()` now ignores a reference that is not newer than the last one,
which keeps the first copy, which is also the one with the least transport delay.
A master more than a second behind is a handover rather than a copy, and resets
the timeline while keeping the ratio, which describes this node's own crystal.
`raft_on_ref()` also stops toggling `REF_RX` on the duplicate, so the marker reads
as one edge per reference whether one bus is up or both; otherwise pulling a
jumper at checkoff step 9 would look like the sync rate halving.
`tests/test_lab5_sync.py` covers both, 15 cases.

**Second bench run, 2026-11-21.** With the duplicate ignored the ratio centred on
zero, but it wandered between +930 and -1511 ppm on both Nucleos, and those are
already smoothed values. Working back through the average, the raw per-sample
ratio spans roughly 5000 ppm, which over a 20 ms heartbeat is about **50
microseconds of jitter on the receive timestamp**. That is the real D17 number,
and it was hidden under the duplicate bug until now.

That makes a ratio measured between consecutive references useless on its own
terms: a crystal drifts by tens of ppm and the measurement noise is 2500, so the
signal is forty times smaller than the error and no averaging weight recovers it.
`RATIO_WINDOW_US` now measures the ratio across a **one second** span instead of
one heartbeat, which divides the jitter's contribution by fifty, and the average
weight was lightened to 1/4 since it no longer has to do that work alone.

This does not slow the correction down, which is the part worth saying in the
report: the base is still re-anchored on every reference, so the ratio only ever
extrapolates across one 20 ms heartbeat. Even 1000 ppm of ratio error is 20
microseconds there. The dominant PPS error will be the 50 microseconds of
timestamp jitter itself, not the ratio, which is what P7 has to attack.
`time_sync_locked()` now takes a second rather than two references, so the first
`sync:` line appears a second after the first REF.

**Third bench run, 2026-11-21: accepted.** With the one second window the ratio
converges from 213 ppm down into a narrow band within about ten seconds and holds
there. Settled means over twenty consecutive samples: **N2 about +12 ppm, N3 about
+29 ppm**. Both are well inside crystal specification, both are stable, and they
differ from each other, which is what two independent crystals should do.
`correction` equals `ratio` numerically because the window is exactly one second,
so the console line reads directly as microseconds lost per second.

Two observations for the report's drift section, both from this run:

- **The residual is quantization, not noise.** Every reported value is a multiple
  of 15.26 ppm, which is one LSB of Q16.16, and the sample to sample variation is
  only two to four LSB. The filter has taken the noise down to the floor of the
  representation, so there is nothing left for a heavier filter to remove.
- **The remaining excursions are common mode, from the Pi.** Both nodes reported
  `-123 ppm` within a second of each other, twice, in separate captures. Two
  independent crystals do not deviate identically at the same instant, so that is
  the master's transmit path: a REF stamped in Python and then delayed before it
  reaches the wire, which both followers observe at once. The per-node drift is
  clean; the outliers belong to the Pi as time master.

**Optional, not done: raise `TIME_SYNC_RATIO_SHIFT` from 16 to 24.** One LSB
becomes 0.06 ppm instead of 15.26, which matters because the signal being reported
is 12 to 29 ppm and is currently resolved to two significant figures at best. The
arithmetic still fits in 64 bits with margin: the widest intermediate is
`master_delta << 24` at about 1.7e13 for a one second window, and
`since * ratio` at about 3.4e11 for a 20 ms extrapolation. `pi/demo/time_sync.py`
and the Q16.16 references in `tests/test_lab5_sync.py` have to move with it.

This is cosmetic for the mechanism and only improves the reported numbers: 15 ppm
of ratio error across one 20 ms heartbeat is 0.3 microseconds, far below the
timestamp jitter. Worth doing before the drift data is collected for the report,
not worth interrupting P7 for.

Still open against the scope: `DELAY_US` is a calculation, not a measurement, and
the only reference for absolute offset is PPS edge spread. P7 has to fix it.

| # | Task | Where | Done when |
|---|---|---|---|
| 6.1 | Timestamp capture for `L` and `F`, from **D17** | RX path | The single biggest lever on PPS spread, so measure the alternatives |
| 6.2 | One-way delay estimate `d`, from **D18** | new `src/time_sync.c/.h` | Derived from CAN bus properties, no round trip |
| 6.3 | `clock_settime()` on followers at first sync, from **D19** | `time_sync.c` | System time set without breaking timers across the step |
| 6.4 | Drift ratio with filtering, from **D20** | `time_sync.c` | Ratio is stable enough that the PPS edge does not wander |
| 6.5 | `global_gettime()`, maintained continuously on every node | `time_sync.c` | Returns a corrected global time on leader and followers alike |
| 6.6 | Periodic task printing the drift error being corrected | `lab_tasks.c` | Visible on all three consoles |

### P7. PPS output  [DONE, all three nodes measured 2026-11-21]

**Measured: mean 9.14 us, standard deviation 19.48 us**, over ten consecutive
edges with the cockpit running two minutes, N2 against N3 on A0 with grounds
tied. Checkoff step 3 allows 1 ms between FL and RL and those run off this same
clock, so there is roughly 50 times the margin needed. Task 7.5 says to iterate
until the spread stops improving; it already has, relative to anything that
matters, so D17 is closed at the receive callback.

Three things to state carefully in the report:

**D18 resolved by measurement, 2026-11-21.** Scoping the Pi's PPS against N2
gave a **mean of 272.4 us with the Pi rising first**, standard deviation 105.5 us
over ten edges, while the Pi's own thread self reported **+85.7 us mean lateness**
(worst +304, SCHED_FIFO granted) over 300 edges. Differencing:

```
Pi edge = T + 85.7
N2 edge = T + 85.7 + 272.4 = T + 358.1
```

A node fires at `T - d + D`, so late by 358 means `D = d + 358 = 658 us`. Only
256 us of that is time on the wire. The rest is the Pi's transmit path: Python,
the socket, the SocketCAN queue, the mcp251x workqueue and a 4 MHz SPI
transaction standing between the timestamp and the first bit transmitted.

**Confirmed after the correction.** With `LAB5_DELAY_COCKPIT_US` at 658 the Pi to
N2 gap collapsed from 272.4 us to **6.99 us**, which over ten samples with a
standard deviation of 154 us is 0.14 sigma: indistinguishable from zero. Before
the correction the Pi led on every edge; afterwards the sign flips edge to edge,
which is what being centred looks like. The Pi's own self measurement was stable
across the two runs, mean lateness 85.7 then 82.4 us, worst 304 then 200.

The apparent rise in spread, 105.5 to 154 us, is not real: a standard deviation
estimated from ten samples carries about 24 percent relative error of its own.

**Where the spread lives, for the report.** The Nucleo pair measures 19.5 us for
the *difference* of two boards, so each board contributes about 19.5/sqrt(2) =
13.8 us. Subtracting in quadrature from the three node figure:

```
Pi contribution = sqrt(154^2 - 13.8^2) = 153.4 us
```

Over 99 percent of the variance in the three node spread is the Raspberry Pi's
scheduler; the Nucleos are almost invisible in it. That is the argument for why a
general purpose Linux box cannot be a time reference of the same quality as the
microcontrollers it synchronizes, and it is a measurement rather than an opinion.

**The consequence is larger than the constant.** The delay belongs to the
master's transmit path, not to the bus, so it changes when the leader changes.
An STM32 master has none of that software stack. Holding 658 across an election
would step every follower's clock by the difference at the handover, and
checkoff step 6 allows no step larger than 100 us. `enum lab5_delay` now carries
one value per master and `raft_on_ref()` selects it from the current leader.
`LAB5_DELAY_STM32_US` is a calculation at 400 and **is not measured yet**: P9
measures it the same way and more easily, since a master applies no delay to its
own edge, so the gap from master to follower is the error directly.

- **The N2 to N3 measurement could not have found this.** An error in
  `d` common to both nodes shifts both edges equally and cancels exactly in a node
  to node comparison. What the 9.14 us mean bounds is the *asymmetry* between the
  two boards, and that is not statistically distinguishable from zero either: the
  standard error of the mean over ten samples is 19.48/sqrt(10) = 6.16 us, so 9.14
  is 1.5 sigma. Breaking the symmetry needed a third edge from a node that applies
  no delay to itself and can report its own scheduling error, which is what the
  Pi's PPS thread provides.
- **The jitter came in better than P6 predicted**, 19.5 us against about 50. The
  difference is the common mode component: a REF the Pi stamps and then delivers
  late moves both nodes' edges together, so it largely cancels in the difference.
  What survives is only the uncorrelated part of each node's receive path. The
  earlier per node observation of 465 us excursions appearing on both boards at
  once is the same effect seen from the other side.
- **The free running experiment is worth half a minute of bench time.** Scope the
  spread with the cockpit up, stop the cockpit, and watch the gap walk apart
  linearly at the relative crystal rate, tens of microseconds per second. Restart
  and it pulls back together. That is a direct demonstration of what the
  synchronization buys and is hard to argue with in a report.

Settled here, subject to what the scope says:

- **D21, PPS timer.** TIM5 channel 1 on PA0, which `lab_io.c` already reserved
  for this. TIM5 is the 32 bit timer on this part, so at the 1 MHz `pps.c`
  prescales it to it wraps every 71 minutes rather than the 51 seconds a 32 bit
  counter at the core clock would give.
- **Output compare, not a counter alarm.** Zephyr's counter API would interrupt at
  the right count and let the handler set the pin, but that puts interrupt latency
  in the edge path, and with two CAN receive workers at preemptive priority 0 that
  latency is a few microseconds and is not bounded. P6 measured the system noise
  floor at 50 microseconds; adding an unbounded term to the one measurement worth
  13 points is not worth the simplicity.
- **One time base.** F_k is now the TIM5 counter rather than `k_cycle_get_32()`,
  so the deadline is written straight to the compare register with no conversion.

**D22, PPS on the Pi: a thread on absolute deadlines, and measure it.**
`pi/demo/pps.py` sleeps with `clock_nanosleep(CLOCK_REALTIME, TIMER_ABSTIME)`
rather than a relative sleep, so the time spent computing the next deadline
cannot accumulate into it and a late wakeup is absorbed instead of carried
forward. It asks for `SCHED_FIFO` on that thread alone, which needs root; without
it the thread still runs and the jitter is simply worse, which the exit summary
says rather than hides. The clock is `CLOCK_REALTIME`, the same one stamped into
REF, so the edge and the reference cannot disagree about when a second begins.

Rejected: pigpio's hardware timed waveforms, which would produce a better edge
but by a different mechanism than the one the cockpit actually schedules against,
and the kernel `pps-gpio` driver, which is an input for disciplining a clock from
an external pulse rather than an output.

The thread records the gap between the deadline it asked for and the clock
immediately after the GPIO write, and the cockpit prints count, mean and worst at
exit. **That measurement is the deliverable, not the edge.** The Nucleos reach
19.5 us because a comparator moves their pins; the Pi cannot, and saying so with
a number is a better report paragraph than an edge that matches because the
jitter was hidden. It also sets up the extra credit question about keeping the
GUI alive through a cockpit failure and why the Nucleos cannot do it.

Fall back to pigpio only if the measured error turns out to be milliseconds
rather than hundreds of microseconds.

| # | Task | Where | Done when |
|---|---|---|---|
| 7.1 | Invert `global_gettime()` to `F_deadline`, arm a hardware timer compare, from **D21** | `time_sync.c`, overlay | No polling loop anywhere near the edge |
| 7.2 | PPS on the Pi, from **D22** | `pi/demo/` | The Pi is the default master, so its edge sets the baseline |
| 7.3 | Per-edge console line: global time, local clock, drift ratio, correction applied | all nodes | Lines parse into the report's drift and jitter data |
| 7.4 | Scope all three PPS pins, record the spread | bench | A capture worth putting in the report |
| 7.5 | Iterate on **D17** and **D20** against the measured spread | as above | The spread stops improving. This is where the 13 points are earned |

### P8. Blinkers on global time, checkoff step 3  [DONE, measured 2026-11-22]

**Measured: FL on N2 against RL on N3, mean 2.5 us, standard deviation 3.05 us**
over ten edges. Checkoff step 3 allows 1 ms, so this passes by about 400 times.
The TIM5 compare upgrade noted below as a fallback is not needed.

**That result revealed a mistake in the P7 analysis.** The blinkers are driven by
a thread sleeping to a deadline; PPS is driven by a hardware comparator. The
software path beat the hardware path by six times, which should not happen. The
reason is the length of the extrapolation, not the delivery mechanism:

| Path | Armed how far ahead | Ratio error over that span | Measured |
|---|---|---|---|
| PPS | ~990 ms, from the falling edge | 15.1 us | 19.5 us |
| Blink | ~20 ms, re-entered each edge | 0.3 us | 3.05 us |

One LSB of the Q16.16 ratio is 15.26 ppm. Multiplied by a one second
extrapolation that is 15.1 us, which accounts for essentially the whole measured
PPS spread. **So the 19.5 us is ratio error through a long lever arm, not
receive timestamp jitter**, which is what P7 recorded and what D17 was closed
against. D17 is still closed, since the jitter is evidently smaller than
believed, but the reasoning in the P7 section was wrong about the cause.

Two consequences, neither urgent since both figures already pass:

- **`TIME_SYNC_RATIO_SHIFT` 16 to 24 is not cosmetic.** It was parked as
  improving only the reported drift number. Ratio quantization lands on the PPS
  edge through the extrapolation, so finer resolution tightens the edge itself.
- **Re-arming closer to the edge is the bigger lever. Done 2026-11-22**:
  `pps_refine()` rewrites the compare register from the 20 ms monitor task for
  the second already chosen, cutting the extrapolation from 990 ms to about
  22 ms. Holding the module lock masks the timer interrupt on this part, so the
  phase check and the compare write cannot be interleaved by the edge they are
  scheduling, and a 2 ms guard keeps a refinement from ever writing a value the
  counter has passed, which would cost a full 32 bit wrap of 71 minutes.
  Not yet measured on hardware.

**D23 settled: the phase is a pure function of global time.**

```c
phase = (global_us / half_us) & 1
```

No message carries timing any more. Two nodes agree because they divide the same
timeline, not because they were told to, so a node that reboots, is cut off, or
joins late comes up already in step with nothing to restore. That is what makes
checkoff step 7's "hazards return to 2 Hz" free rather than a resynchronization
problem.

Both half periods divide a second exactly, 250000 and 125000 microseconds, so an
edge coincides with the PPS edge and the two can be scoped against each other.
The phase is recomputed from the clock on every pass, never incremented, so
nothing accumulates.

Rate as signal: 2 Hz while the cockpit still leads, 4 Hz once an STM32 does,
selected from `raft_leader()`. Checkoff step 6 asks for 4 Hz when the Pi is
down, step 7 for the return to 2 Hz, and this gets both from the leader identity
rather than from a second failure flag.

Scheduling: far from an edge the task waits on the event semaphore with a 20 ms
timeout so an error state change is acted on promptly; within 20 ms of an edge it
sleeps to the deadline instead. Only the deadline sleep decides how closely two
nodes agree, and the system tick is 100 microseconds against the 1 ms checkoff
step 3 allows. If the bench shows that marginal, the upgrade is a second TIM5
compare channel driving an interrupt, the same mechanism P7 uses for PPS, which
would take it to a few microseconds.

`pi/demo/cockpit.py` derives its phase the same way, so a candump and the lamps
cannot disagree, and a direction change now transmits at once instead of waiting
for an edge, so left, right and cancel feel immediate.

`tests/test_lab5_blink.py`, 8 cases, pins the properties rather than the
implementation: the rates divide a second, the phase is low at every whole
second, two nodes 20 microseconds apart disagree only within 20 microseconds of
an edge, and edges are exactly a half period apart with no accumulation.

| # | Task | Where | Done when |
|---|---|---|---|
| 8.1 | Reschedule the existing 500 ms blink task off `global_gettime()`, from **D23** | `lab_tasks.c` | FL and RL within 1 ms over ten edges |
| 8.2 | Hazard rates, 2 Hz normal and 4 Hz in failure mode | `lab_tasks.c` | Both rates in step across nodes |
| 8.3 | Left, right, cancel from the wheel | `lab_tasks.c`, `cockpit.py` | Checkoff step 3 passes end to end |

### P9. Election and time master handover, checkoff steps 6 and 8  [DONE, verified on hardware 2026-11-22]

**D14 settled: a seeded generator, not hardware entropy.** Checkoff step 8 asks
for "the seed that makes the Pi win", so the draw has to be a function of
something the operator sets. `src/election.c` mixes the shared seed with the
local node ID, so one value in `config/cluster.conf` gives each node a different
series while every node still agrees what the seed was. Plain xorshift32 has poor
low bits and the timeout takes a modulus, which reads exactly those, so the state
is multiplied by an odd constant first. `tools/find_seed.py` searches for a seed
whose first round elects a chosen node, with a `--margin` so the result survives
a late heartbeat rather than being a tie. With the current config,
`election_seed=6` gives cockpit 62 ms, steering 91, drivetrain 94.

Written so far on the STM32:

- Vote frames. `REQ_VOTE` carries the candidate's term plus its last log index
  and term; the identifier says who is standing. The log fields implement Raft
  5.4.1 and are what stop an election losing a committed entry, which is concrete
  here: an isolated STM32 has a short log and electing it would drop link state
  the cluster had agreed on. `VOTE` names the candidate it answers, because two
  can stand in one term and a vote meant for one must not count towards the
  other's majority. That exact class of bug cost a debugging round in P5.
- `raft_election_tick()` from the monitor task, with a fresh draw every round so
  two nodes that collide once do not collide on every retry.
- `ELECTION_BASE_MS` is `SYNC_TIMEOUT_MS`, deliberately: the moment a node stops
  believing it has a time master is the moment it should try to become one.
- `raft_ref_tx()`, stamping this node's **global estimate** rather than its local
  uptime. A new leader tracked the shared timeline while following, so continuing
  to report it is what keeps the handover free of a step. Nothing is reset at the
  handover, which is the whole mechanism checkoff step 6's 100 us bound rests on.
- REF shares the 20 ms heartbeat slot and goes out first, since REF holds the
  lowest identifier and winning arbitration is wasted if it is queued second.

Three correctness points that only appear once a node can lead:

- **A leader must not apply its own reference.** The two buses do not hear each
  other directly, but a relay hop puts a leader's REF back on its other bus, and
  `base = L + d` would add the delay again on every pass and run away.
- **Indicators are driven from the role every 20 ms**, not at each transition.
  Role changes now happen on five paths, and one that forgot an indicator would
  leave LEADER lit on a node that had stepped down, which is exactly what
  checkoff steps 6 and 8 read.
- **A leader is its own time master**, so SYNC is lit for it by definition; it
  receives no reference to keep the flag fresh.

**D13 settled: hold an election, do not wait.** `none` and `random` already
parsed to `default_leader_id = 0`, so every node boots a follower in term 0 and
the timeout elects one with no extra mechanism.

Testing that exposed a real gap, now closed. **In random mode no follower knew
who the leader was.** REF and APPEND each use one shared identifier and neither
payload named its sender, which is harmless with a pre-defined leader and wrong
without one, because two behaviours key off the master's identity: the one way
delay constant, which belongs to the master's transmit path rather than to the
bus, and the hazard rate, 2 Hz while the cockpit leads and 4 Hz once an STM32
does. Checkoff steps 6 and 8 read both.

REF now carries the master's node ID in the top two bits of byte 7, taken from
the cycle counter, which had them to spare: at 14 bits it still wraps only every
16384 heartbeats, five and a half minutes, far longer than any gap it exists to
detect. The TA still reads the clock and counter out of REF at step 2.

`tests/test_lab5_vote.py`, 21 cases, drives the Pi's state machine, which mirrors
the firmware's, and compiles the vote codecs from the real C to compare byte for
byte. What it pins is what makes an election *safe* rather than merely
terminating: a stale log cannot win, a higher last term beats a longer log, a
vote only counts for the candidate it names, a node votes once per term but will
re-grant to the same candidate so a lost vote is recoverable, and granting holds
off the voter's own election. 147 host tests pass in total.

**First bench run, 2026-11-22: the election works.** With the cockpit killed, N2
and N3 both reported `lost SYNC, no REF for 60 ms`, N3 stood in term 2, N2
granted it, and N3 took the leadership with 2 of 3 votes inside a second. N2
re-synced to drivetrain and the hazards went from 2 Hz to 4 Hz on both boards,
which is the other half of checkoff step 6.

Two observations from that run:

- **`vote granted` appeared twice on N2**, one copy per bus. Re-granting to the
  same candidate is deliberate: it is how a candidate recovers a lost vote.
  Granting to a different candidate in the same term is what is forbidden.
- **A leader's PPS spacing is exactly deterministic.** Once N3 was leading it
  received no reference, so its base and ratio froze and consecutive edges came
  999985 microseconds apart, every time. That is 1000000 / (1 + 15e-6) with its
  reported ratio of 15 ppm, which is the drift arithmetic checking out to the
  microsecond against a completely independent path.

**The handover step is not yet a clean number.** Careful which interval holds it:
the compare is armed at the falling edge, so the edge after the handover was
already scheduled under the old master and the *second* one is the first armed
under the new one. On N2 that interval came out 175 microseconds short, but the
baseline scatter on neighbouring intervals was already plus or minus 150, so one
sample cannot separate a step from noise and no claim against the 100 microsecond
bound is made yet. `pps_refine()` is the prerequisite: with the lever arm cut the
scatter should fall to single digits and the step becomes readable. Then repeat
the handover five or six times and tabulate the first post-handover interval.

One encouraging sign: had the delay stayed at the Pi's 658 microseconds, the step
would have been about 260 in a consistent direction. Nothing that large appeared,
so the per master delay is doing its job and 400 is in the right neighbourhood.

**Handover step measured, 2026-11-22: it passes.** On N2, across the handover to
N3, with the local mean at about 999970 microseconds:

| Interval | Delta | Versus local mean |
|---|---|---|
| across the handover | 999976 | +6 us |
| the one after | 999886 | -84 us |

Both inside the 100 microsecond bound checkoff step 6 sets, though the step is
not separable from the scatter around it, so the honest claim is that both are
under 100 rather than that the step is 6.

**The limiting term has moved to the STM32 master's REF transmit jitter.** With
the Pi leading the ratio sat within one LSB, 15 ppm. With N3 leading it bounces
30, -46, +45 ppm. `raft_ref_tx()` stamps the clock inside `tx_task`, a thread
woken on a 20 ms deadline, so that thread's wakeup jitter lands directly on L;
about 100 microseconds over a one second ratio window reads as 100 ppm. If more
margin is ever wanted, stamping closer to the actual transmission is the lever,
but the bound is met.

**A dying master's last REF poisons the ratio the new leader freezes.** On the
run above, N3's ratio went 0 to -46 ppm exactly at the handover and stayed
there, which is impossible from references since a leader receives none. The
sample came from the Pi's final REF, delivered late and stretching the
measurement window; 46 microseconds of error across a one second window is 46
ppm. The new leader then froze it and free-runs on it, visible as a steady
1000046 microsecond edge spacing. Harmless, since 46 ppm is within crystal
spec, but worth a line in the report: the last message from a dying master is
the least trustworthy one. A cheap mitigation is to discard the ratio sample
taken across an interval in which the master went silent.

**The cockpit can now step down.** `RaftAgent.on_append()` applies an
AppendEntries, adopts a higher term, replays the state machine through the new
commit index and acknowledges. Before it, the Pi had `on_ref` and could vote but
nothing carried a term it had to respect, since REF has no term field and a vote
only arrives during an election it took part in. A cockpit restarted after an
election therefore kept transmitting REF beside the real leader and dragged
every follower between two timelines, which the bench showed as endless
`append rejected result=1` and `time master restarted`. A leader also ignores an
append at or below its own term, because both buses carry it and a relay can
return it, and applying it would mean running the consistency check against its
own log and truncating entries it is still replicating.

**The step-down did not work on the first try, and the reason was larger than
the symptom.** The Pi still led after a restart. The cause was not on the Pi at
all: **an STM32 leader had no replication path whatsoever.** It could win an
election and send REF, but there was no AppendEntries transmitter, no ack
handling and no client request handling, because none of that was ever needed
while the cockpit was permanently leader. So an STM32 leader was a time master
only, and since REF carries no term and a vote only reaches a node that took
part in the election, nothing on the bus carried a term the restarted Pi had to
respect.

Added to `src/raft_agent.c`:

- `raft_append_tx()`, idle at 100 ms and at the heartbeat rate while a follower
  is behind, built from the furthest behind follower's nextIndex since one
  identifier is broadcast to all of them. This is what lets a stale leader
  discover it has been replaced.
- `raft_on_ack()`, advancing matchIndex and nextIndex, walking nextIndex back on
  a refusal, and committing by majority.
- `advance_commit()`, which only commits an entry from the leader's own term,
  the rule that keeps a later leader from committing a predecessor's entry on a
  count that was never a majority at the time.
- `raft_on_client_request()`, so link changes reported while an STM32 leads are
  recorded rather than retried for ever, with a redirect when a client reaches a
  follower.
- `become_leader()`, resetting every nextIndex to the leader's own end of the
  log and every matchIndex to zero, because a new leader knows nothing about
  what its followers hold.

**The term ran away to 110 on the bench, and the election was not the cause.**
The election at term 110 resolved in a single round: `standing`, one grant,
`LEADER with 2 of 3 votes`. What drove the term up was a feedback loop, and the
Pi's exit line named it: `refs=1946 appends=583 skipped=1567`. The cockpit was
skipping eighty percent of its REF deadlines, so followers timed out and
elected, which raised the term, which produced more traffic, which slowed the
cockpit further.

Two causes, both of them things the C leader path was missing that the Pi's had:

- **No duplicate suppression on client requests.** The Pi keeps `client_seen` so
  a repeated request is answered again rather than appended again. The C
  `raft_on_client_request()` written tonight had none, and every request arrives
  two to four times as a matter of course, once per bus plus relayed copies, with
  the client retrying every 200 ms until answered. So an STM32 leader appended
  the same `CANLINK_steering_1_UP` repeatedly, the log filled with duplicates,
  and every follower truncated and re-stored on every round. `client_answered`,
  `client_request` and `client_index` now hold the last answer per node, cleared
  in `become_leader()` because a request outstanding across a leadership change
  has to be appended once in the new term.
- **The refusal line was printed from the dispatch thread on every append.** At
  115200 baud a console line takes about four milliseconds, so a leader whose log
  had diverged left the dispatch thread unable to drain the receive queue,
  precisely when it most needed draining. Rate limited to one a second, like the
  reference gap report.

**The real cause of the runaway was a protocol flaw, not a logic slip.**
`struct lab5_append` carried the leader's term but **not the carried entry's own
term**, so a follower had no choice but to stamp what it stored with the header
term. That is correct exactly as long as the cluster never changes term, which
was true of every test before P9. Once the log spans two terms it deadlocks:

1. Leader's log is `[1:(164,X), 2:(165,steering)]`; `nextIndex` walks to 1.
2. It sends `prev=0, count=1, event=X` with header term 165.
3. The follower stores `(165, X)` at index 1 and acknowledges ACCEPTED.
4. The leader advances to `nextIndex=2` and sends `prev=1, prev_term=164`.
5. The follower holds term 165 there, so MISMATCH, and it truncates to empty.
6. The leader backs off to `nextIndex=1`. Return to step 2, for ever.

That is the `store idx=1` / `result=3` / `result=2` cycle seen on both Nucleos,
and it emptied the committed graph cluster wide: the leader's commit line read
`net nodes=... links=......` because every NODE and CANLINK entry had been
truncated away.

The payload is now `term`, `prev_index` in fifteen bits with the count in bit
fifteen, `prev_term`, `commit` in fifteen bits, **`entry_term`**, `event`. The
bits came from the two index fields and the count, which were wider than they
ever needed to be: `log_size` is 30000 and an append carries at most one entry.
`raft_log_accept()` stores and compares on the entry's term on both sides.

Five tests pin it, including the loop itself, so the regression is recognisable
rather than merely absent.

**A pattern worth putting in the report.** Three bugs this session were the same
shape: a rule that existed in `pi/demo/raft_agent.py` and not in
`src/raft_agent.c`, because the STM32's leader path was written from scratch
while the Pi's had been working for days. Duplicate client request suppression,
a leader ignoring its own append, a leader ignoring its own REF. On this bus a
node routinely hears itself, once per bus plus relayed copies, so every handler
needs an explicit answer for that case, and two implementations of one protocol
need the rule written twice or tested once against each other.

**Election and step-down verified on the bench, 2026-11-22.** Over repeated
kill and restart cycles with all three nodes fresh: terms went 53, 54, 55 and
stopped. One failed round then success, which is ordinary Raft rather than a
runaway. The new refusal diagnostic earned itself immediately:

```
raft: vote refused for drivetrain term=54 mine=54 log-behind
raft: standing term=55 last=3/53
raft: LEADER term=55 with 2 of 3 votes
```

N3 stood with `last=1/52`, N2 held `last=3/53` and refused it for being behind,
then stood itself and won. That is Raft 5.4.1 doing exactly its job, observed.
The cockpit steps down on restart every time and the leadership stays with the
STM32, which is checkoff step 7's requirement.

**Open, and the next thing to fix: the committed graph stays empty under an
STM32 leader.** Entries replicate correctly, `store idx=3 term=55
CANLINK_steering_1_UP` and so on, but every commit line reads
`net nodes=... links=......`. Two facts explain it:

- `network_link_up()` requires `network_node_up()` for the same node, which is
  right: a link belonging to a node that is not up is meaningless, and
  `dropNode` is specified to remove the node and all its links.
- `src/raft_agent.c` contains no call to `raft_event_node()` at all. The STM32
  client reports only its own two links. Node-up entries came solely from the
  cockpit appending them directly while it was leader.

So once an STM32 leads, nothing puts `NODE_*_UP` into the log, no node is marked
up, and every link is suppressed even though it committed. The entries are
right and the rendering is right; the input is missing.

**Fixed, not yet on the bench.** Both halves, following D15's own logic: the
owner reports its own links, so the owner reports its own node.

- `raft_client_poll()` queues `NODE_<self>_UP` ahead of the link reports, behind
  a `self_reported` flag. The link loop now stops once something is queued, since
  a node carries one request at a time.
- The cockpit gained the client path it never had: `client_report()`,
  `client_poll()` and `on_reply()`, mirroring the firmware. `client_append()`
  only ever worked while this node led, so as a follower the Pi's own
  `NODE_cockpit_UP` could not reach the log at all.
- `refill_self_reports()` requeues all three when this node **stops** leading.
  Anything a node appended while it led is not safe, because a new leader's log
  wins and entries written under an older term are overwritten. Re-reporting is
  how it gets back into the graph, and it is free when nothing changed: the
  leader answers a repeated request without appending a second copy.
- The reports are driven from the cockpit's run loop rather than once at
  startup, so they survive a leadership change.

Nine tests cover it, including the redirect, the retry, a reply addressed to
another node, and the requeue on stepping down.

**Verified on hardware 2026-11-22.** All three nodes converge on the same index
with the same graph:

```
raft: commit idx=12 net nodes=CSD links=121212 mode=OK
```

identical on the Pi, N2 and N3 at term 65. That is checkoff step 4's
requirement, every node and link present at the same index on every console.
The full client write path is visible on both Nucleos, `client req=2
CANLINK_steering_1_UP`, `client ack req=2 idx=11`, then `store idx=11 term=65`
on the peer, including the redirect when a client reaches a follower.

A restarted cockpit prints `stepping down` exactly once and settles as a
follower, which is checkoff step 7's leadership requirement.

**Bench note for the demo.** A node that has just joined shows a large first
drift ratio, 2273 ppm in this run, decaying geometrically to normal over about
ten seconds as the quarter weight average washes out the one sample taken
across the sync acquisition itself. Its PPS edge is off by milliseconds during
that window. Let a freshly started node settle for fifteen seconds before
scoping anything.

Still to do in P10: the held events and replay-exactly-once behaviour of
checkoff step 7, and `COCKPIT_OK`/`COCKPIT_ERROR`.

| # | Task | Where | Done when |
|---|---|---|---|
| 9.1 | Election timeout `missed_heartbeats * heartbeat_ms + U(0, jitter)` with a settable seed, from **D14** | `raft_agent.c` | 60 to 99 ms with defaults, and a seed that makes the Pi win |
| 9.2 | REQ_VOTE, vote granting with the log comparison, term handling | `raft_agent.c` | New leader within 1 s of the old one going away |
| 9.3 | Time master handover: the new leader takes over REF | `raft_agent.c` | REF moves with the leader |
| 9.4 | Measure the step at handover on the scope | bench | No step larger than 100 microseconds |
| 9.5 | `LEADER=random` boot mode, from **D13** | config | An STM32 leads from cold boot, no failure mode |

### P10. Failure mode, cockpit down and back, checkoff steps 1, 6 and 7  [DONE, verified on hardware 2026-11-22]

**D27 settled: failure mode is a log entry, braking is not.** Only the leader
appends `COCKPIT_OK` and `COCKPIT_ERROR`, which is what keeps checkoff step 4's
rule of exactly one entry per change: every node can see the cockpit go quiet,
but only one of them is entitled to say so. `raft_report_cockpit()` compares
against what it last recorded, so it is one entry per change rather than one
per observation, and `become_leader()` clears that memory because a new leader
cannot trust what a predecessor may never have committed.

The cockpit cannot report its own death, so `COCKPIT_ERROR` waits for whoever
wins the election. That is exactly why checkoff step 6 allows a second for the
failure mode to appear but only 100 ms for the motor to brake: **braking is
local and does not wait for consensus.** The monitor task already enters the
error state from its own inputs within one 20 ms pass; failure mode is folded
in on top, so the committed view can hold the vehicle in error even when a
node's own inputs look fine, but can never be the thing that makes it slow.

**D26 settled: a cursor over committed indices, and one line in the log.**
Checkoff step 7 wants held events shown exactly once, in order, marked
replayed, then live events. Ordering and exactly-once come free from the log: a
cursor that only moves forward over committed indices cannot skip or repeat.
The only real decision is where replay ends, and the natural line is **the
commit index the leader reported in the first append this node accepted after
it started following**. Everything at or below it happened while this node was
away or leading; everything above is live.

Losing the leadership resets that cursor, because entries this node appended
while it led are not safe: a new leader's log wins and anything written under
an older term is overwritten. The log it rejoins is effectively a new one.

Nine tests cover both halves, including one entry per change rather than per
observation, a follower recording nothing, and the replayed-then-live boundary.

**First bench run, 2026-11-22: both work.** A leading STM32 appended
`COCKPIT_ERROR`, failure mode propagated, and every commit line walked
`mode=FAIL` and back to `mode=OK` when `COCKPIT_OK` landed at index 8. The
returning cockpit printed its held event marked `replayed` and then live events
unmarked. That is checkoff step 7's display requirement.

**One bug the run exposed: a leader transmitted its own client reports.**
`raft_client_poll()` always put the request on the bus, and a node does not
receive its own frames, so a leader's own request never reached its own
handler. A follower picked it up, saw it was not leading, and correctly
redirected to the node that had sent it, which produced an endless
`redirected to steering, retrying req=1` on steering's own console while
steering was the leader. The leader now records its own reports directly, which
is what the Pi's `client_report()` already did. Another instance of the pattern:
a rule the Pi had and the firmware did not.

**Second bench run: the redirect spam is gone, and it exposed a worse bug.**
With the cockpit down the leader appended `COCKPIT_ERROR`, reached `mode=FAIL`,
and recorded its own reports locally. But **the commit index sat at 1 while the
log held seven entries**, for twelve seconds.

`raft_append_tx()` built the header from the **minimum nextIndex across all
followers**, and that array includes the cockpit, which was dead and never
acknowledged. Its nextIndex stayed at 1 for ever, so the minimum stayed at 1,
so the leader re-sent entry 1 on every round and nothing past it replicated.
**One unreachable node pinned the entire replication stream**, which defeats the
whole point of tolerating one failure. The Pi had the identical flaw; it never
showed because the Pi was always leader with both Nucleos alive.

A single broadcast identifier cannot serve followers at different positions, so
each append is now aimed at **one follower in turn**. That costs a factor of N
in catch-up rate and wastes a dead node's turns, which is the right trade: the
alive followers keep moving, and a follower accepts an entry it already holds
idempotently, so a turn spent elsewhere does it no harm. Three tests cover it,
including the exact bench case of a silent node and a seven entry log.

**Verified on hardware.** With the cockpit down the commit index walks all the
way out and the graph is correct for a cluster running without it:

```
commit idx=4 net nodes=.S.  links=..12.. mode=FAIL
commit idx=5 net nodes=.SD  links=..12.. mode=FAIL
commit idx=6 net nodes=.SD  links=..121. mode=FAIL
commit idx=7 net nodes=.SD  links=..1212 mode=FAIL
```

No cockpit, both Nucleos up on both buses, failure mode from the committed
`COCKPIT_ERROR`.

The recovery half, on the same image, is checkoff step 7 almost verbatim:

```
raft: stepping down, term=52 leader is elsewhere
raft: event idx=1 term=52 COCKPIT_ERROR replayed
raft: event idx=2 term=52 NODE_steering_UP replayed
...
raft: event idx=7 term=52 CANLINK_drivetrain_2_UP replayed
raft: event idx=8 term=52 COCKPIT_OK
raft: event idx=9 term=52 NODE_cockpit_UP
```

Seven held events, each exactly once, in order, marked; then four live events
unmarked. Both Nucleos then walk `commit idx=8` through `idx=11` to
`nodes=CSD links=121212 mode=OK` as `COCKPIT_OK` clears failure mode and the
cockpit re-reports itself through the STM32 leader.

**The replayed boundary corrected itself.** An earlier run marked only index 1,
because during catch-up the leader reports a commit index that lags its real
one, and that is the value the boundary is taken from. Aiming each append at one
follower in turn removed the lag, so the leader was committed to 7 when the
cockpit arrived and the line fell exactly where it should. No separate fix was
needed.

| # | Task | Where | Done when |
|---|---|---|---|
| 10.1 | `COCKPIT_OK` and `COCKPIT_ERROR` entries and their effect | `raft_agent.c`, `network_state.c` | Enter and leave failure mode from the log |
| 10.2 | Error state within 100 ms, motor braked, from **D27** | `lab_tasks.c` | Cold start with the proxy stopped shows all three nodes in error, hazards in step |
| 10.3 | Held events while the cockpit is down, from **D26** | leader, `cockpit.py` | On return they appear exactly once, in order, marked replayed, then live events |

### P11. Relay and the multi-link convergence window, checkoff steps 5 and 9  [11.1 and 11.2 WRITTEN, not yet on hardware]

**The scenario, and why it could not work before.** Checkoff step 5 pulls the
cockpit off one bus and the drivetrain off the other, so those two cannot hear
each other at all and every frame between them has to cross at the node that
still holds both. Lab 3's relay carried only catalog traffic: the dispatcher
returned early for Lab 5 frames, so `REF`, `APPEND`, acknowledgements, votes and
client requests were never forwarded.

**D24 settled: the flag goes in the identifier, not the payload.** Three
reasons. Every Lab 5 payload is already full to eight bytes. A flag in the
identifier is visible in a `candump` without decoding anything, which is what
the checkoff reads. And it costs nothing to filter on.

`LAB5_RELAY_FLAG` is 0x400, a high bit, so **a relayed copy sorts below every
original in arbitration**. That is the correct priority: a relayed copy is by
definition a second chance at something and must never delay first-hand
traffic. Lab 3's catalog tops out at 0x300, so the range was free. The existing
consensus mask 0x7e0 already covers bit ten, so the two ranges separate with no
mask change: originals match `(0x040, 0x7e0)`, copies match `(0x440, 0x7e0)`.

**The relayer's ID is in the identifier too, and that was not optional.**
Two nodes can hold both links at once, and then both relay. Under one shared
identifier their copies collide destructively: CAN arbitrates on the identifier
alone, so two frames sharing an ID and differing in the data field leave the
node sending recessive while reading dominant with a bit error, and the
transmit counter rises by eight for both. Their queues are offset by a few
hundred microseconds, so they relay different frames at the same moment
routinely.

The bench made this visible only once the cockpit came off bus B. From then on
a frame originating on bus A was relayed onto B by **both** Nucleos, while a
frame originating on B had only one relayer left for A. Errors appeared on bus
B and never on A, with the transmit counter reaching 128 and the receive
counter staying at zero: the signature of a node losing a bit contest during
its own transmission rather than seeing a corrupted bus.

Carrying the relayer in bits 9 and 8 makes the two copies arbitrate normally:
one wins, the other defers and retries. It also simplifies the filtering, since
matching on the flag alone covers every relayer.

**One hop is enforced structurally**: a node never relays a frame that already
carries the flag. With three nodes one hop always suffices, so nothing is lost,
and no loop is possible regardless of topology.

**D25 settled: relaying is a reflex, not a routing decision.** A node holding
both links forwards one copy and does **not** consult its routing table to
decide whether anyone needs it. This is the direct answer to the handout's
section 5.2 warning that after a second link failure the tables take several
rounds to settle and traffic has to survive that window: **a reflex has no
window, because there is nothing to converge.** The routing table still exists
and is still built from `getNetwork()`, but it informs the client's view of
reachability rather than gating delivery.

The cost is one duplicate per frame while every link is healthy. Every Lab 5
handler is already idempotent, because each frame arrives twice over the two
buses in any case, so a third copy changes nothing but bus load. A reference is
about 256 microseconds on the wire at 500 kbit/s against a 20 ms period, so
there is room.

Eight tests pin the identifier scheme on both endpoints, including that a
relayed copy loses arbitration, does not collide with the Lab 3 catalog, stays
inside eleven bits, and is not mistaken for an original by the consensus mask.

**Bench run found an unrelated bug, now fixed: a leader elected without ever
having synchronized emitted no PPS at all.** N2 booted with no cockpit present,
won an election, and printed `sync: no lock yet, ref=0` for ever with no `pps:`
lines. A leader receives no reference, so nothing locks its estimate, and
everything downstream is gated on that lock: `arm_next_edge()` refuses to arm
and `raft_ref_tx()` falls back to the epoch plus uptime.

It had worked until now purely by accident, because every STM32 that became
leader had locked as a follower first. That is exactly checkoff step 8's cold
start with `LEADER=random` and no cockpit.

`time_sync_assume_master()` lets such a node declare itself the timeline's
origin: ratio exactly one, because there is no other clock to measure against,
and zero delay, because the frame never crossed a bus. It does nothing when the
module is already locked, so a node that led *after* following keeps the
timeline it already had, which is what makes a handover seamless and keeps
checkoff step 6's 100 microsecond bound reachable. Two tests cover both halves.

**D16 revisited: zero was the wrong detection threshold.** The bench showed a
`CANLINK_*_DOWN` followed by an `UP` about a second later, every twenty to sixty
seconds, on both nodes and both buses, with nothing physically touched. The
shape gave it away: 60 ms down debounce plus 750 ms up debounce, the machinery
working correctly on a trigger that should never have fired.

`lab_can_link_healthy()` required `tx_err_cnt == 0`. The CAN transmit error
counter jumps by **eight** on one transmission error and falls by only **one**
per successful frame, so a single stray bit keeps it non-zero for eight more
frames, and the link reads as down for long enough to clear the debounce.

Zero came from P5, to stop a disconnected branch oscillating: the MCP2515
auto-recovers from bus off into error active with the counter reset, then fails
again. But the 750 ms up debounce already covers that, and zero was the wrong
way to get it. The threshold is now 96, three quarters of the way to the
error-passive boundary at 128. A genuinely disconnected branch gets no
acknowledgement at all, so its counter runs past 128 within a few frames and the
state check catches it regardless; the threshold only decides how much transient
noise is tolerated on a link that is really there.

The principle is worth stating in the report: **a consensus log entry is
expensive and permanent, so a detection threshold feeding one has to sit well
above the noise floor, not at it.** The same reasoning produced the asymmetric
debounce in P5.

**The threshold fix worked, and uncovered a zombie leader.** With the flapping
gone, both Nucleos printed `append rejected result=1 prev=0 match=0` once a
second indefinitely. `result=1` is STALE_TERM: the Nucleos held a higher term
and the cockpit was still leading and still sending appends it had no right to
send.

The Pi's `on_ack()` read `result` and `match_index` but **never `term`**. The
firmware's `raft_on_ack()` adopts a higher term and steps down; the Pi's did
not. So every follower was telling it the term was stale in the one field it
did not look at.

That channel is the only one left for a leader that missed an election. REF
carries no term, and a vote only reaches a node that took part in the election,
so a stale leader's own appends provoking refusals is its sole remaining notice.
Five tests cover it, including that an ack in the same term is still counted
normally.

This is the reverse of the pattern that produced most of tonight's bugs: usually
the Pi had a rule the firmware lacked. Both directions have now bitten, which is
the real argument for the two implementations being tested against each other
rather than written twice.

**Reverted: the graph self-healing check.** The idea was that a node absent
from the committed graph should re-report itself, because entries it wrote can
vanish without any term change when a consistency mismatch truncates the tail,
and catching each such path one at a time does not converge.

It caused two separate log floods on the bench, reaching index 3852 and then
733 of one repeated entry, and after two guards the reason it kept firing was
still not understood. **It is reverted.** Two things made it dangerous:

- "The committed graph disagrees with me" is *continuously true* for a window
  after every report, because commit always lags the log. That makes it a
  feedback input rather than an error signal.
- A follower's retry generates a **new request id** each time, which defeats
  the leader's duplicate suppression entirely, so every repeat appends. The
  suppression keys on the request id, which only protects against a *resent*
  request, not a *re-issued* one.

The underlying problem is real and still open: **a node whose entries are
truncated away has no path back into the graph** except `adopt_term()` and
stepping down, and a mismatch truncation goes through neither. A correct fix
needs to be designed rather than bolted on, and must not re-derive its trigger
from a value that lags its own action. Candidates: make the client retry reuse
its request id so suppression works; or have the *leader* notice a node absent
from the graph while heartbeating and record it, since the leader sees the
committed state without a lag relative to its own log.

Kept from that work: serialising self-reports on the commit index, and the
cockpit's own link detection, both of which stand on their own.

Still to do: 11.3, the bench work, where the open question is whether a node
reachable only by relay still holds FL and RL within 1 ms. The relay adds a
queue hop and a transmit worker to the reference path, which shifts that node's
`base` by the relay latency. It is systematic rather than random, so it appears
as a constant offset rather than jitter, but it has never been measured.

| # | Task | Where | Done when |
|---|---|---|---|
| 11.1 | Explicit destination plus relay flag, coexisting with `lab_can_forward()` and the 200 ms dedup, from **D24** | `lab_can.c` | No blind rebroadcast, one hop only |
| 11.2 | Buffer and retry across the convergence window, from **D25** | `lab_can.c` | Pull N1 off B and N3 off A: wheel still steers, blinks and brakes |
| 11.3 | Relayed sync with an STM32 master, scenario (c) jumpers | all | SYNC stays on at the cut-off node, REF_RX still toggles, FL and RL within 1 ms |

### P12. Isolation and test button, checkoff step 10

Reuse the Lab 3 behavior and add the rule that the term changes only if the isolated
node was leader, and exactly once.

### P13. Integration and demo rehearsal

With the schedule covered there is room to do this properly rather than discover it in
front of a TA.

| # | Task | Done when |
|---|---|---|
| 13.1 | Run the three handout demo scenarios in order, recording `getNetwork()` on every node | All three logs agree in each scenario |
| 13.2 | Run the full ten-step checkoff sequence end to end, twice | Two clean runs, no surprises |
| 13.3 | Rehearse the verbal questions, including what would change with four nodes | Everyone on the team can answer any of them |
| 13.4 | Capture the scope traces the report needs | PPS spread, FL/RL skew, handover step |

### P14. Report

Three sections, one page each: time drift adjustment (13), Raft state machine (13),
client and routing during multi-link failures (10), plus the GitHub link (4). The drift
section must be written from the actual P7 printouts, not from the design intent. The
decision log from P0.3 is the raw material for the other two.

### P15. Extra credit, 15 points

Now genuinely reachable rather than a stretch. Cloud VM with Redis and a web app joining
as a client through the Pi. Asynchronous best-effort publishing on its own thread, never
on the CAN receive path, with a 1000 entry queue and resume from the last acknowledged
stream id. Five GUI views and a staleness banner. Points are 3 for secure deployment,
5 for publishing as specified, 4 for the GUI, 3 for the demonstration and the paragraph
on keeping the GUI alive through a cockpit failure.

## Continuous, not a phase

- The config file stays byte-identical on every node. Adding a key means adding it in
  three places.
- Every new `.c` needs a `target_sources` line. `src/` currently matches the build
  exactly, which is worth preserving.
- `HOW_TO_CODE.md` rules: one `.c`/`.h` pair per module, all state `static`, no `extern`,
  Doxygen on every function, documented `#define`s, tabs, `snake_case`.
- The user runs every build and flash. Never run `west build` or `west flash`.
- Keep `WIRING.md` in sync as pins are added.
- No em-dashes in code, comments or documentation.

---

# Design decisions to make

Three were already settled in `CLAUDE.md`: PPS edges come from a hardware timer compare
rather than polling; every node maintains its global-time estimate continuously so an
election does not step the clock; and relay addressing is an explicit destination plus a
relay flag, never a blind rebroadcast. The rest are open.

## Identity, config and framing

| ID | Decision | Options | Needed by |
|---|---|---|---|
| D1 | Node ID scheme | Reuse `LAB_NODE_ID` 2/3 plus Pi as 1, with a name table; or renumber to match `cockpit`/`steering`/`drivetrain` | P0 |
| D2 | How the STM32 gets a config file with no filesystem | Generate a header at build time from one shared file; hand-maintained header; Kconfig; Pi pushes config at boot | P0 |
| D3 | Config file format | `key=value` text; JSON on the Pi only; C header as the single source | P0 |
| D4 | CAN arbitration IDs for REF, APPEND, APPEND_ACK, REQ_VOTE, VOTE, client request and redirect | Must stay consistent with the Lab 3 catalog, where CTRL at 0x010 is currently the highest priority. REF must win against APPEND or the sync period jitters | P0 |
| D5 | REF payload layout in 8 bytes | Master time plus the cycle counter the TA reads at checkoff step 2, at what resolution | P2 |
| D6 | AppendEntries fragmentation | ISO-TP; a custom multi-frame scheme; or a tight encoding that fits term, index, prevLogIndex, prevLogTerm, leaderCommit and the event into 8 bytes. `log_size` 30000 needs 15 bits of index | P4 |
| D28 | Pins for LEADER, SYNC, REF_RX, HZ and PPS | Which free GPIOs, and which get scoped | P0 |

## Raft

| ID | Decision | Options | Needed by |
|---|---|---|---|
| D7 | `log_size` 30000 against 96 KB of SRAM on the F401RE | Even a 4 byte entry is 120 KB, so the full log does not fit. Cap the STM32 ring and document the deviation; keep the full log on the Pi; compress entries; or snapshot. The config must stay identical across nodes, so decide whether the key stays 30000 with a per-node cap | P3 |
| D8 | Replay strategy | Replay from scratch on every commit, or apply incrementally. Either way the state machine stays a pure function of the log | P3 |
| D9 | `getNetwork()` representation | Adjacency matrix, edge list, or per-node bitmask. Must be printable and comparable across three consoles at the demo | P3 |
| D10 | Application-level acks | One ack ID per follower, or a single ID carrying a follower bitmask. A CAN frame ack means someone heard it, not that the follower stored it | P4 |
| D11 | Persistence of `currentTerm`, `votedFor` and the log | STM32 flash, which is slow and finite; or RAM-only with catch-up over AppendEntries, which checkoff step 7 allows. Be ready to defend it | P4 |
| D12 | Which bus carries Raft | Both, or one for consensus and one for control. Relaying only makes sense if a node can lose one bus and still participate | P0 |
| D13 | Fallback when `default_leader` is absent | Eventually hold an election, or wait | P9 |
| D14 | Seeded election RNG | Checkoff step 8 asks for the seed that makes the Pi win, so the jitter RNG needs a settable seed rather than a hardware source. Decide where the seed comes from | P9 |
| D30 | Watchdog interaction | `iwdg` is enabled and `lab_can_workers_alive()` feeds supervision. Decide how new blocking paths, especially a client blocking until commit, avoid tripping it | P4 |

## Client and link detection

| ID | Decision | Options | Needed by |
|---|---|---|---|
| D15 | Who may report a link failure | Owner-reports-own-links, with the leader's heartbeat timeout covering a fully isolated node; or all three clients report. Checkoff step 4 requires exactly one entry per change at the same index on every node | P5 |
| D16 | Detection threshold and debounce | CAN error counters and bus-off state, or absence of expected periodic frames. Lab 3 already has HB_MON and the DEG LEDs, so decide how much to reuse. Without debounce a jumper pull chatters several entries into the log | P5 |

## Time

| ID | Decision | Options | Needed by |
|---|---|---|---|
| D17 | Timestamp point for `L` and `F` | The MCP2515 is an external SPI controller, so there is no directly readable hardware timestamp. Options are the first instruction of the RX interrupt handler, SPI read completion, or the application thread. This is the single biggest lever on PPS spread | P6 |
| D18 | How `d` is estimated | Static computation from bitrate and frame bit count, a one-time startup measurement, or folded into the offset. Must not be an NTP-style round trip | P6 |
| D19 | Step versus slew | The handout asks for both `clock_settime()` and a correcting `global_gettime()`, which pull against each other. Stepping every 20 ms steps the clock backward and breaks timers scheduled across the step. Suggested: set the system clock once at first sync, then free-run and carry the correction in `global_gettime()` | P6 |
| D20 | Drift ratio filtering | A raw single-interval ratio over 20 ms is mostly timestamp noise and makes the PPS edge wander. EWMA, median over N, or a longer window. Document the choice, it is worth points | P6 |
| D21 | PPS timer on the STM32 | TIM2 is already taken by the PWM output on PA1, so pick another timer and compare channel | P7 |
| D22 | PPS generation on the Pi | No hardware compare from Python. A small C helper, pigpio, the kernel `pps-gpio` driver, or accept Python jitter and document it. The Pi is the default time master, so this sets the baseline the STM32 edges are compared against | P7 |
| D23 | Blinker scheduling off global time | How the existing 500 ms periodic blink task becomes a global-time schedule, and how 2 Hz normal and 4 Hz failure-mode hazards are derived from it | P8 |

## Routing, relaying and system behavior

| ID | Decision | Options | Needed by |
|---|---|---|---|
| D24 | Relay frame layout | How the destination and relay flag coexist with the existing `lab_can_forward()` and the `LAB_DEDUP_MS` 200 ms dedup. One hop always suffices with three nodes | P11 |
| D25 | Buffer and retry policy in the convergence window | Queue depth, retry interval and drop policy while routing tables settle after the second link failure. Checkoff step 5 depends on traffic surviving this window | P11 |
| D26 | Held events during a cockpit outage | Where they are held, how ordering is preserved, and how they are marked replayed so they appear exactly once on the Pi's display | P10 |
| D27 | Failure mode definition | What `COCKPIT_ERROR` does to the motor and hazards, and how the 100 ms budget in checkoff step 6 is met | P10 |
| D29 | Pi agent process structure | Extend the single-threaded `cockpit.py` run loop, or run the agent as its own thread or process. The CAN receive path has to stay clear of slow work, and the extra credit explicitly forbids publishing on it | P2 |

## Working assumptions where the handout is ambiguous

Staff are not available, so every one of these is resolved here. Two were settled from
the Lab 3 handout and its R4.8 fault matrix, which is on disk at `../lab3-649/`; those
are facts, not guesses. The other seven are our calls, each with the reasoning and the
risk if it turns out wrong. Keep this section current, it is the raw material for the
report and for the verbal questions.

### Settled from the Lab 3 handout, not assumptions

**Scenario (c) and the full fault matrix.** Lab 3 R4.8 defines jumper scenarios (a) to
(f). Lab 5 checkoff step 9's "scenario (c) jumpers" is R4.8 (c): **N1 loses CAN B and N3
loses CAN A**, so nothing N1 sends reaches N3 directly and N2, still on both buses,
bridges. That is exactly the fault Lab 5 step 5 spells out as "Pull N1 off B and N3 off
A", so steps 5 and 9 are the same topology with a different time master.

| R4.8 | Fault | Lab 5 step |
|---|---|---|
| (a), (b) | Pull one node's A jumper, then its B jumper | step 4 |
| (c) | N1 off B, N3 off A, N2 bridges | steps 5 and 9 |
| (d) | Both jumpers of one STM32, full isolation | step 10 |
| (e) | Every node off bus A, whole-bus loss | not used |

**"Part 5 checkpoint".** Lab 3's Part 5 is titled "Implement the demo", and "Checkpoint"
is Lab 3's word for a per-part deliverable. The extra credit therefore means: run the
demo with the GUI open, showing what Lab 5 checkoff steps 6 and 7 already describe, the
cockpit dying, an STM32 taking over, the held-event count rising, and replay on return.
No missing definition to chase.

**Checkoff step 4's "then Bfs" is a typo for "then B".** Lab 3's own step 5 reads
"Single-bus faults (R4.8 a, b). Pull any one node's A jumper", so Lab 5 step 4 is
scenario (a) followed by scenario (b) on the same node.

### Our calls

**1. `MEMBER` and `EVENT` are the checkoff's informal names, not new entry types.**
Part 3 already calls the third field of a log entry "an event", so `EVENT` is the generic
entry. `MEMBER` is used in steps 4 and 5 for entries that change the network graph, which
is the `NODE_X_UP`/`NODE_X_DOWN` and `CANLINK_X_Y_UP`/`CANLINK_X_Y_DOWN` family.
Implement only the Part 3 vocabulary.
*Mitigation:* make the debug print carry both readings, for example
`idx=7 term=1 MEMBER CANLINK_steering_1_DOWN`, so the TA sees whichever word they expect.

**2. REF is the heartbeat, APPEND is replication.** Part 2's "heartbeat, which is also the
clock-sync message" is REF at 20 ms. The empty APPEND at 100 ms is Raft log replication,
carrying `leaderCommit` forward when there is nothing new to append. The election timeout
counts **missed REFs**, because `missed_heartbeats * heartbeat_ms` is 3 x 20 = 60 ms and a
60 to 99 ms timeout measured against a 100 ms APPEND would fire continuously. This is the
only reading under which Part 2 and checkoff step 2 are both satisfiable.

**3. `log_size` stays 30000 in the config; the STM32 keeps a bounded window.** The config
must be byte-identical, so the key does not change. Entries pack into 4 bytes: 8 bits of
term, 15 bits of index, 5 bits of event type, which covers all 20 values (2 cockpit, 6
node, 12 link). The Pi holds the full 30000 at 120 KB; each STM32 holds a 4096 entry ring
at 16 KB, which fits the F401RE's 96 KB of SRAM with room to spare and is still three
orders of magnitude more than any demo generates. A node that falls behind the window
gets a snapshot of the network state rather than entry-by-entry replay, which is sound
because the graph is tiny and bounded at 3 nodes and 6 links.
*Risk:* a grader reading `log_size` literally as a per-node allocation. Document the
deviation in the state machine report section.

**4. `nodes` is the list of all three IDs; local identity comes from the build.** This is
the only reading consistent with "the config file must be identical on every node"; the
default column's "a unique ID for each node" describes that the IDs within the list are
distinct, not that the file differs. Local identity stays where Lab 3 already put it,
`LAB_NODE_ID` on the STM32 and a CLI flag on the Pi. The mapping follows Lab 3's existing
numbering and wiring, where the Pi is N1 and `lab_origin()` returns 1 to 3:

| Config ID | Lab 3 node | Hardware | Evidence |
|---|---|---|---|
| `cockpit` | N1 | Raspberry Pi | Checkoff step 5 says "Pull N1 off B", and the Pi is the only node with a network interface |
| `steering` | N2 | Nucleo, front | The wiring table gives N2 the servo, FL and FR |
| `drivetrain` | N3 | Nucleo, rear | The wiring table gives N3 PWM_SET, DIR_A, RL and RR |

**5. `LEADER` is `default_leader` under another name, and `random` behaves as `none`.**
Accept `cockpit`, `steering`, `drivetrain`, `none` and `random`. A node ID means boot in
term 1 with that leader and hold no election. Both `none` and `random` mean no predefined
leader, so every node starts as a follower and the first to time out wins; `random` just
names the outcome. For checkoff step 8's "the seed that makes the Pi win", add an
`election_seed` config key, identical on every node, combined with the local node ID so
each node draws different jitter from the same seed. That makes the winner deterministic
and selectable, which is what the step asks for.

**6. The cockpit dying appends both `COCKPIT_ERROR` and `NODE_cockpit_DOWN`, in that
order.** They are different state changes: `COCKPIT_ERROR` sets vehicle failure mode,
hazards and braked motor; `NODE_cockpit_DOWN` applies `dropNode(cockpit)` and changes the
graph that routing reads. Appending both does not violate checkoff step 4's "one entry
per change at the same index", because that rule is stated for the link faults of step 4,
and each of these two entries is the single entry for its own distinct change.
`COCKPIT_ERROR` goes first because failure mode has the 100 ms deadline in step 6. The
state machine therefore holds a pair, the graph and the failure-mode flag, and
`getNetwork()` returns only the graph.
*Rejected alternative:* deriving failure mode from the cockpit's absence in the graph and
appending only `NODE_cockpit_DOWN`. It is tempting, but Part 3 assigns that effect to
`COCKPIT_OK`/`COCKPIT_ERROR` explicitly.

**7. The log is RAM-only, with no flash persistence.** Raft wants `currentTerm`,
`votedFor` and the log durable, but on the F401RE that means 128 KB flash sectors, erase
times near a second and roughly 10k cycle endurance, which a per-vote write would consume
quickly. Every demo scenario is satisfied by catching up over `AppendEntries`: checkoff
step 7 has the Pi rejoin and replay, and step 8 power-cycles a single STM32. Durability
exists in Raft to survive the simultaneous loss of a majority, which no scenario here
exercises.
*State the limit plainly:* if two of the three nodes lose power at the same instant,
committed entries can be lost. That is the honest answer to the verbal question, and it
is also most of the answer to what would change with four nodes.
