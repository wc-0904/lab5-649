# Lab 5 remaining work and open design decisions

Status as of 2026-11-17. The handout is due 2026-11-18 23:59, but late days cover the
schedule, so this list is ordered by dependency rather than by the calendar.
Nothing in Parts 1 through 7 is implemented. What exists is the Lab 3 firmware this
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

### P1. Foundations  [DONE 2026-11-17, not yet built or flashed]

All six items are implemented. The firmware has not been compiled or run on
hardware; `lab_protocol.c` was compiled and executed natively as a cross-check,
and the host tests pass, but neither establishes a working Zephyr build.

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

### P2. Default-leader boot and the two periodic frames, checkoff step 2

| # | Task | Where | Done when |
|---|---|---|---|
| 2.1 | Raft role state: `currentTerm`, role, `leaderId`; boot in term 1 with `default_leader` and hold no election | new `src/raft_agent.c/.h`, `pi/demo/` | LEADER lit on the Pi only |
| 2.2 | Pi agent process structure, keeping slow work off the CAN receive path, from **D29** | `pi/demo/cockpit.py` | Agent runs without disturbing the existing cockpit timing |
| 2.3 | REF transmit every 20 ms on the leader, carrying master time and the cycle counter, from **D5** | `raft_agent.c`, `cockpit.py` | `candump -c any` shows REF at 20 ms |
| 2.4 | Empty APPEND every 100 ms on the leader | same | APPEND at 100 ms, and no REQ_VOTE anywhere on either bus |
| 2.5 | REF receive path and REF_RX toggle on followers | `lab_tasks.c` | SYNC on all three, REF_RX toggling |
| 2.6 | Confirm REF wins arbitration against APPEND and CTRL under load | bench | Sync period does not jitter when a log entry is in flight |

### P3. Log and state machine, host testable

| # | Task | Where | Done when |
|---|---|---|---|
| 3.1 | Log entry encoding `(term, index, event)` and the storage ring, from **D6**, **D7**, **D8** | new `src/raft_log.c/.h` | Fits the chosen frame and memory budget |
| 3.2 | `addLink`, `dropLink`, `addNode`, `dropNode`, `getNetwork` as a pure replay, from **D9** | new `src/network_state.c/.h` | Same log replays to the same graph on any node |
| 3.3 | Printable `getNetwork()` | same | Three consoles can be compared side by side at the demo |
| 3.4 | Host unit tests for replay determinism and for the encoding round trip | `tests/` | Extends the existing 21 passing cases, all green |

### P4. Replication, commit and catch-up

| # | Task | Where | Done when |
|---|---|---|---|
| 4.1 | AppendEntries with `prevLogIndex`, `prevLogTerm`, `leaderCommit` | `raft_agent.c` | Follower append works over CAN |
| 4.2 | Per-follower application-level ack, from **D10** | `raft_agent.c` | A CAN frame ack is never treated as stored |
| 4.3 | Commit on majority, then ack the client | `raft_agent.c` | Client blocks until step 6 of Figure 2 |
| 4.4 | Election and append debug prints, required by the `Debug` config key | all agents | Prints visible on all three consoles |
| 4.5 | Catch-up for a rejoining node, from **D11** | `raft_agent.c` | A node restarted mid-run converges to the same log |
| 4.6 | Check the commit path against the watchdog, from **D30** | `lab_tasks.c` | A client blocking until commit never trips `iwdg` |

### P5. Client, link detection and routing, checkoff step 4

| # | Task | Where | Done when |
|---|---|---|---|
| 5.1 | Link up and down detection with debounce, from **D16** | `lab_can.c`, `lab_tasks.c` | A jumper pull makes one entry, not several |
| 5.2 | Ownership rule for who reports a link change, from **D15** | client | Exactly one entry per change at the same index on every node |
| 5.3 | Client write to the leader, follower redirect | new `src/raft_client.c/.h` | A client hitting a follower is redirected |
| 5.4 | Routing table distilled from `getNetwork()` | `raft_client.c` | Per destination: which bus, or which relay |
| 5.5 | Single-link fault run: pull one node's A jumper, then B | bench | DEG LEDs as in Lab 3, term unchanged everywhere, logs agree |

### P6. Time synchronization

Build the estimate on every node from the start, leader included, rather than only on
followers. P9 depends on that and it is awkward to add afterwards.

| # | Task | Where | Done when |
|---|---|---|---|
| 6.1 | Timestamp capture for `L` and `F`, from **D17** | RX path | The single biggest lever on PPS spread, so measure the alternatives |
| 6.2 | One-way delay estimate `d`, from **D18** | new `src/time_sync.c/.h` | Derived from CAN bus properties, no round trip |
| 6.3 | `clock_settime()` on followers at first sync, from **D19** | `time_sync.c` | System time set without breaking timers across the step |
| 6.4 | Drift ratio with filtering, from **D20** | `time_sync.c` | Ratio is stable enough that the PPS edge does not wander |
| 6.5 | `global_gettime()`, maintained continuously on every node | `time_sync.c` | Returns a corrected global time on leader and followers alike |
| 6.6 | Periodic task printing the drift error being corrected | `lab_tasks.c` | Visible on all three consoles |

### P7. PPS output

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
