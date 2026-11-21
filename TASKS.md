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

### P7. PPS output  [STM32 half written and building, never flashed or scoped]

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

**D22 is deliberately still open.** See the resume block above.

| # | Task | Where | Done when |
|---|---|---|---|
| 7.1 | Invert `global_gettime()` to `F_deadline`, arm a hardware timer compare, from **D21** | `time_sync.c`, overlay | No polling loop anywhere near the edge |
| 7.2 | PPS on the Pi, from **D22** | `pi/demo/` | The Pi is the default master, so its edge sets the baseline |
| 7.3 | Per-edge console line: global time, local clock, drift ratio, correction applied | all nodes | Lines parse into the report's drift and jitter data |
| 7.4 | Scope all three PPS pins, record the spread | bench | A capture worth putting in the report |
| 7.5 | Iterate on **D17** and **D20** against the measured spread | as above | The spread stops improving. This is where the 13 points are earned |

### P8. Blinkers on global time, checkoff step 3

| # | Task | Where | Done when |
|---|---|---|---|
| 8.1 | Reschedule the existing 500 ms blink task off `global_gettime()`, from **D23** | `lab_tasks.c` | FL and RL within 1 ms over ten edges |
| 8.2 | Hazard rates, 2 Hz normal and 4 Hz in failure mode | `lab_tasks.c` | Both rates in step across nodes |
| 8.3 | Left, right, cancel from the wheel | `lab_tasks.c`, `cockpit.py` | Checkoff step 3 passes end to end |

### P9. Election and time master handover, checkoff steps 6 and 8

| # | Task | Where | Done when |
|---|---|---|---|
| 9.1 | Election timeout `missed_heartbeats * heartbeat_ms + U(0, jitter)` with a settable seed, from **D14** | `raft_agent.c` | 60 to 99 ms with defaults, and a seed that makes the Pi win |
| 9.2 | REQ_VOTE, vote granting with the log comparison, term handling | `raft_agent.c` | New leader within 1 s of the old one going away |
| 9.3 | Time master handover: the new leader takes over REF | `raft_agent.c` | REF moves with the leader |
| 9.4 | Measure the step at handover on the scope | bench | No step larger than 100 microseconds |
| 9.5 | `LEADER=random` boot mode, from **D13** | config | An STM32 leads from cold boot, no failure mode |

### P10. Failure mode, cockpit down and back, checkoff steps 1, 6 and 7

| # | Task | Where | Done when |
|---|---|---|---|
| 10.1 | `COCKPIT_OK` and `COCKPIT_ERROR` entries and their effect | `raft_agent.c`, `network_state.c` | Enter and leave failure mode from the log |
| 10.2 | Error state within 100 ms, motor braked, from **D27** | `lab_tasks.c` | Cold start with the proxy stopped shows all three nodes in error, hazards in step |
| 10.3 | Held events while the cockpit is down, from **D26** | leader, `cockpit.py` | On return they appear exactly once, in order, marked replayed, then live events |

### P11. Relay and the multi-link convergence window, checkoff steps 5 and 9

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
