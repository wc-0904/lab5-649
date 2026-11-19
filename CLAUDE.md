# 18-449/649 Lab 5: Time Synchronization and Consensus

Due 2026-11-18 23:59. Released 2026-11-09. Handout version 1.0.

## What this system is

Three nodes on a model vehicle, each attached to two CAN buses. Every node runs a
Raft agent. The Raft log holds the current network state, meaning which nodes and
which CAN links are up. Each node builds its routing table from that state.

The Raft leader is also the time-synchronization master. It sends a reference
message every 20 ms carrying its clock. Followers use it to set their clocks and
to measure local drift. Blinkers run off the resulting global time, so the front
and rear lamps stay aligned.

Fault tolerance: when a node goes down the others elect a leader, the new leader
takes over the reference message, and the vehicle enters failure mode with hazards
flashing in step. When a CAN link goes down instead, no election happens, because
nodes stay reachable by relaying through the other bus or the third node.

## Current state of this directory

Nothing in Part 1 through Part 7 above is implemented yet. `lab5-649/` started as a copy
of `lab3-649/` at its final state and has since been stripped to the Lab 3 firmware that
Lab 5 builds on, plus the Lab 5 handout PDF and this `CLAUDE.md`. The Lab 2 carry-over
code, the Lab 3 and Lab 4 report packages, and the stale build trees are gone; the whole
directory is now about 1 MB. Things worth knowing before touching anything:

- **`TASKS.md` holds the remaining work in proposed order and every open design
  decision.** Start there; the decision IDs D1 to D30 are referenced from the task rows.
- **P1 of `TASKS.md` is implemented**, so this is no longer bare Lab 3 code. Added:
  `config/cluster.conf`, `tools/gen_config.py`, `pi/demo/cluster_config.py`, the
  `enum lab5_id` catalog, five new GPIO outputs and a boot pin walk. Built against
  Zephyr 4.4.99 with SDK 1.0.1 and flashed to both Nucleos; both report
  `Task startup: 0` and the same config hash.
- **This is a git repository now**, initialised at P1. `lab3-649/.git` still holds the
  full pre-cleanup tree if anything removed earlier is wanted back.
- **The project is `lab5_consensus`** and `setup.sh` uses `lab5_*` variables. The Lab 3
  module names (`lab_can`, `lab_tasks`, `lab_io`, `lab_protocol`) are kept deliberately,
  since those modules are still the Lab 3 ones.
- **`README.md`, `WIRING.md` and `docs/` describe Lab 3**, not Lab 5. They are kept
  because the vehicle, pin map, test points and bus jumpers are the same hardware the
  Lab 5 checkoff uses. `HOW_TO_CODE.md` is not Lab 3 specific: its style rules apply.
- **The repo-root `/home/wasmirc/18449/CLAUDE.md` is out of date.** It describes only
  `lab2-649/` and `lab3-649/`, and it says Lab 3 carries Lab 2's full source tree, which
  is no longer true of this copy.

### What builds

Remember the user runs every build and flash. **On WSL, source `setup_win.sh`, not
`setup.sh`**: `setup.sh` assumes the Mac layout and fails here on a missing venv, and it
would point `ZEPHYR_BASE` at the coursework folder, which is not a Zephyr tree.

```sh
source ./setup_win.sh
west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2
west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3
west flash -d build-demo-n2 -r openocd --no-rebuild --verify
```

west 1.5.0 renamed `--skip-rebuild` to `--no-rebuild`; the old spelling falls through to
the openocd runner, which rejects it and never starts the flash. The ST-LINK is invisible
to WSL until it is attached with `usbipd` from an elevated PowerShell, and the console is
`/dev/ttyACM*`, not the Mac's `/dev/cu.usbmodem*`. See README.md for the full WSL path.

`setup_win.sh` activates `$HOME/zephyrproject/.venv`, sets `ZEPHYR_BASE` to
`$HOME/zephyrproject/zephyr` and `ZEPHYR_SDK_INSTALL_DIR` to `$HOME/zephyr-sdk-1.0.1`,
checking all three exist first. `setup.sh` is the Mac layout and activates `../../.venv`.
CMake cache options: `LAB_NODE_ID` (2 front or 3 rear, required), `LAB_TRACE_TASK`
(task index 0..8 on PC11, `-1` off), `LAB_MEASURE_MODE` (0 normal, 1 RX only, 2 N3 DRIVE,
3 N2 RELAY), `LAB_DIAGNOSTICS` (intrusive periodic console summaries, default OFF).
Both build directories were deleted, so the first build is a full one.

`cluster_config.h` is generated into the build tree by `tools/gen_config.py` from
`config/cluster.conf`; it is not in `include/` and must not be committed. Otherwise
`src/` and `include/` hold exactly what `target_sources` compiles, nothing more:

| File | Role |
|---|---|
| `src/main.c` | Calls `lab_tasks_start()` and nothing else. |
| `src/lab_protocol.c` | CAN catalog, CRC-8/ATM, payload validation, frame origin. |
| `src/lab_io.c` | GPIO/PWM outputs and test points. |
| `src/lab_can.c` | Dual MCP2515 transport, per-bus TX workers, RX queue, relay forward. |
| `src/lab_tasks.c` | Ten preemptive task slots, the whole Lab 3 application. |

Keep it that way: a new module means a new `.c`/`.h` pair **and** a new `target_sources`
line. Nothing in `src/` is dead code any more, so a file being present now does mean it
is in the image.

Lab 3 task indices, as `README.md` documents them: 0 dispatch, 1 STEER/DRIVE, 2 blink,
3 test/fault, 4 PID, 5 HB_TX, 6 HB_MON, 7 FORCE, 8 RELAY. `LAB_TASK_COUNT` is 10.
Actuators are simulated; the README warns not to connect motors, H-bridges or servos.

### Existing CAN catalog

`include/lab_protocol.h` defines seven standard IDs and the Lab 3 timing constants. Lab 5
has to add `REF`, `APPEND` and `REQ_VOTE` to this catalog, and lower ID wins arbitration,
so `REF` beating `CTRL` at 0x010 is not free.

| ID | Name | Period |
|---|---|---|
| 0x010 | `LAB_CTRL` | while UDP input is fresh |
| 0x020 | `LAB_FAULT` | event driven, zone in byte 1 |
| 0x100 / 0x101 / 0x102 | `LAB_HB1` / `LAB_HB2` / `LAB_HB3` | 20 ms |
| 0x200 | `LAB_BLINK` | 500 ms blink period |
| 0x300 | `LAB_FORCE` | 50 ms, dummy |

`enum lab_time`: `LAB_HB_MS` 20, `LAB_TIMEOUT_MS` 60, `LAB_PID_MS` 10, `LAB_FORCE_MS` 50,
`LAB_BLINK_MS` 500, `LAB_DEDUP_MS` 200. Note Lab 3 already heartbeats at 20 ms, the same
period Lab 5 wants for `REF`.

### Hardware description

`boards/nucleo_f401re.overlay` enables two `microchip,mcp2515` nodes on SPI with
transceiver children, plus the GPIO/PWM the Lab 3 tasks use; USART1 and the Lab 2
peripherals are disabled. The three Lab 2 bindings were removed with their drivers, so
`dts/bindings/` now holds only `vendor-prefixes.txt`. `cmake/preemptive_can.cmake`
patches the MCP2515 driver so both RX workers run preemptive at priority 0.

`prj.conf` enables CAN/MCP2515 at 500 kbit/s, SPI, GPIO, PWM, watchdog, logging and
`CONFIG_NUM_PREEMPT_PRIORITIES=16`, with `CONFIG_DEBUG_OPTIMIZATIONS=y`. There is no
POSIX configuration in it, so `clock_settime()` is not reachable from the current build.

### Pi side and host tooling

- `pi/demo/` is the live Lab 3 cockpit: `cockpit.py` (UDP to CAN, GPIO scope markers, CSV
  capture), `send_test.py` (simulated wheel over UDP), `setup_can.py`, `measure_can.py`,
  `protocol.py`, `requirements.txt`. This is the code the Lab 5 `cockpit` node grows out of.
- `pi/part0/` and `pi/part1/` are historical bring-up; `pi/part1/check_traffic.py` expects
  the retired 0x702/0x703 IDs and does not validate the current demo.
- `tests/test_demo.py` is the only test left, host-only, 21 cases, passing:
  `python3 -m unittest discover -s tests -p test_demo.py -v`. `tools/` holds four Python
  analysis scripts. None of these build or flash firmware.

### What was removed, and from where

If something referenced below is wanted back, take it from `lab3-649/`.

| Removed | Why |
|---|---|
| `build-demo-n2/`, `build-demo-n3/` | Stale Lab 3 build trees, 47 MB. |
| `output/`, `tmp/` | Finished Lab 3 report and the whole Lab 4 design package, 69 MB. |
| 16 `src/*.c` and 17 headers | Lab 2 modules: motor, servo, encoder, current sensing, UART, blinker, `can_bringup`. None were in `target_sources`. |
| `dts/bindings/lab,*.yaml` | Bindings for those Lab 2 devices; the overlay never referenced them. |
| `docs/lab2/`, `READINGS.md` | Lab 2 operator docs and ACS712 readings. |
| `pi/proxy_receiver/` | Lab 2 UDP-to-UART C forwarder, superseded by `pi/demo/`. |
| `tests/current_test.c` | Lab 2 current-sensor test. |
| `TEST_POINTS.md`, `CURRENT_SENSOR_WIRING.md`, `RUNNING.md`, `LEFT_TO_DO.md` | Removed by the user; the first two were Lab 2, `LEFT_TO_DO.md` tracked Lab 3's leftover validation. |

## Node map

| Node ID | Hardware | Role |
|---|---|---|
| `cockpit` | Raspberry Pi | Default leader and boot-time time master. Only node with a network interface. |
| `steering` | STM32 Nucleo | Follower at boot. |
| `drivetrain` | STM32 Nucleo | Follower at boot. |

N = 3, so a majority is 2 and the cluster survives exactly one node failure.

## The six CAN links

Each node has one link to each bus. Event names use `CANLINK_<node>_<bus>`.

| | CAN 1 | CAN 2 |
|---|---|---|
| cockpit | `CANLINK_cockpit_1` | `CANLINK_cockpit_2` |
| steering | `CANLINK_steering_1` | `CANLINK_steering_2` |
| drivetrain | `CANLINK_drivetrain_1` | `CANLINK_drivetrain_2` |

## Configuration file

Must be byte-identical on every node.

| Key | Meaning | Default |
|---|---|---|
| `N` | Cluster size. Majority is N/2 + 1. | 3 |
| `default_leader` | Node ID of the pre-defined leader, or `none`. When set, every node boots in term 1 with that node as leader and holds no election. | `cockpit` |
| `heartbeat_ms` | Leader heartbeat period. Also the clock-sync period. | 20 |
| `missed_heartbeats` | Consecutive missed heartbeats before a follower declares the leader dead. | 3 |
| `election_timeout_jitter_ms` | Random range added to the election timeout. | 39 |
| `log_size` | Entries the Raft log holds. | 30000 |
| `nodes` | ID of each node, used in log entries. | unique per node |
| `Debug` | When set, the agent prints election and append messages. | Yes |

Election timeout = `missed_heartbeats * heartbeat_ms + U(0, election_timeout_jitter_ms)`,
so 60 to 99 ms with defaults.

## Log entries

Each entry is `(term, index, event)`.

| Event | Leader appends when | State machine effect |
|---|---|---|
| `COCKPIT_OK`, `COCKPIT_ERROR` | Cockpit heartbeats resume or stop | Leave or enter failure mode |
| `NODE_X_UP`, `NODE_X_DOWN` | Node X comes up or goes down | Mark node X up or down |
| `CANLINK_X_Y_UP`, `CANLINK_X_Y_DOWN` | Link for node X on bus Y changes | Mark that link up or down |

## State machine API

Four mutators applied during log replay, plus one read.

| Function | Applied on | Behavior |
|---|---|---|
| `addLink(X, Y)` | `CANLINK_X_Y_UP` | Mark the link between node X and bus Y up, adding it if absent. |
| `dropLink(X, Y)` | `CANLINK_X_Y_DOWN` | Mark that link down. Node and bus stay in the graph. |
| `addNode(X)` | `NODE_X_UP` | Add node X, or mark it alive again, with no links until `addLink` runs for it. |
| `dropNode(X)` | `NODE_X_DOWN` | Remove node X and every link attached to it. |
| `getNetwork()` | read only | Return the whole network graph. Called by the Raft client when it builds its routing table. |

The state machine must be a pure function of the log. Replaying the same log on
any node must produce the same graph.

## Client write path

Six steps, per handout Figure 2.

1. Client calls the leader, for example `addNode(X)`.
2. Leader appends to its own log.
3. Leader sends `AppendEntries` to followers.
4. Followers append.
5. Followers ack.
6. Leader acks the client.

Commit happens between 5 and 6, once a majority has stored the entry. The client
blocks until step 6. A client that contacts a follower gets redirected to the
leader.

The client has two jobs: report link and node changes, and build the routing
table from `getNetwork()`.

## Time synchronization

The leader stamps each heartbeat with its clock. The follower records its own
clock on receipt. Network delay is one way, estimated from CAN bus properties
rather than a round trip, the way TTCAN does it. Do not use an NTP-style
request/response round trip.

Per sync k, with leader stamp `L_k`, local receipt time `F_k`, and delay estimate `d`:

```
base  = L_k + d
ratio = (L_k - L_{k-1}) / (F_k - F_{k-1})

global_gettime() = base + (local_now - F_k) * ratio
```

To schedule the PPS edge for the next whole global second `T`, invert it:

```
F_deadline = F_k + (T - base) / ratio
```

Arm a hardware timer for `F_deadline`. Never poll `global_gettime()` in a loop to
find the edge, or the scope trace shows scheduler jitter instead of clock error.

Also required:
- Update system time on followers from the adjusted leader time (`clock_settime()`).
- A periodic task that calls `global_gettime()` and prints the drift error it is
  currently correcting for.
- A pulse-per-second GPIO output, one rising edge at every whole second of
  `global_gettime()`. Edges from all nodes should line up on a scope. The spread
  is the measure of sync quality.
- On each PPS edge, print the global time at the edge, the local clock reading at
  the same instant, the drift ratio, and the correction applied since the last
  heartbeat. These printouts are the report's drift and jitter data.

## Routing and relaying

Each node builds its routing table from its local agent's `getNetwork()`, then
distills the graph into the subset it needs. With three nodes and two buses the
table is small: per destination, which bus, or which relay.

Two-link edge case, handout section 5.2: the first link failure is detected by the
client on the leader and recorded cleanly. The second may take several rounds to
propagate and for every agent's routing table to settle, depending on which node
is leader. Traffic in that window must survive, so buffer and retry rather than
drop. Checkoff step 5 depends on this.

## Build order

1. Config parser and node IDs.
2. Pre-defined leader boot: term 1, no election, no `REQ_VOTE` on the bus.
3. `REF` at 20 ms and empty `APPEND` at 100 ms.
4. Log plus state machine, replay driven.
5. Client: link detection, write to leader, follower redirect, routing table.
6. Time sync: `clock_settime()`, drift ratio, `global_gettime()`, drift printout.
7. PPS GPIO and the per-edge console line.
8. Election, including continuous global-time estimate on every node so handover
   does not step the clock.
9. Relay path and the two-link convergence window.

## Design decisions

### Settled

- **PPS edge generation**: hardware timer compare, not polling.
- **Time master handover**: every node maintains its global-time estimate
  continuously, so whichever node wins an election is already on the shared
  timeline. Checkoff step 6 allows no step larger than 100 microseconds.
- **Relay addressing**: explicit destination plus a relay flag. Never blind
  rebroadcast, which loops. One hop always suffices with three nodes.

### Open, decide before writing the relevant module

- **Timestamp point for `L` and `F`**: CAN peripheral hardware timestamp register,
  RX interrupt handler, or application thread. Prefer hardware, else first
  instruction in the ISR. This is the single biggest lever on PPS spread.
- **How `d` is estimated**: static computation from bitrate and frame bit count,
  a one-time startup measurement, or folded into the offset.
- **Step versus slew**: the handout asks for both `clock_settime()` and a
  correcting `global_gettime()`, which pull against each other. Stepping the
  system clock every 20 ms will step it backward and break timers scheduled
  across the step. Suggested: set the system clock once at first sync, then leave
  it free running and carry the correction in `global_gettime()`.
- **Drift ratio filtering**: a raw single-interval ratio over 20 ms is mostly
  timestamp noise and makes the PPS edge wander. Choose an EWMA, a median over N,
  or a longer measurement window. Document the choice; it is worth points.
- **CAN arbitration IDs**: `REF`, `APPEND` and `REQ_VOTE` need IDs consistent with
  the Lab 3 catalog. `REF` must win arbitration against `APPEND` or the sync
  period jitters whenever a log entry is in flight.
- **Fragmentation**: a classic CAN payload is 8 bytes and an `AppendEntries`
  carries term, index, prevLogIndex, prevLogTerm, leaderCommit and the event.
  Either use ISO-TP, a custom multi-frame scheme, or a tight encoding that fits
  in 8 bytes. `log_size` 30000 needs 15 bits of index; terms stay small.
- **Application-level acks**: CAN frame ack means "someone heard it", not "the
  follower stored it". Step 5 of the write path needs its own frame per follower.
- **Which bus carries Raft**: both, or one for consensus and one for control.
  Relaying only makes sense if a node can lose one bus and still participate.
- **Who may report a link failure**: all three clients can see the same drop, but
  checkoff step 4 requires exactly one entry per change at the same index on every
  node. Owner-reports-own-links is cleanest, with the leader's heartbeat timeout
  covering a fully isolated node.
- **Detection threshold and debounce**: CAN error counters and bus-off state, or
  absence of expected periodic frames. Needs debounce or a jumper pull chatters
  several entries into the log.
- **`getNetwork()` representation**: adjacency matrix, edge list, or per-node
  bitmask. Must be printable at the demo, since the state on every node has to be
  shown and compared.
- **Persistence**: Raft wants `currentTerm`, `votedFor` and the log durable. On
  STM32 that means slow, finite flash writes. Checkoff step 7 can be satisfied by
  catching up over `AppendEntries` instead, which allows a RAM-only log. Decide
  and be ready to defend it.
- **Fallback when `default_leader` is absent at boot**: eventually hold an
  election, or wait.
- **Seeded election RNG**: checkoff step 8 asks for "the seed that makes the Pi
  win", so the jitter RNG needs a settable seed rather than a hardware source.

## Handout ambiguities

Resolve with course staff. Until then, pick one and stay consistent.

1. The checkoff refers to `MEMBER` entries and an `EVENT` from a node, but Part 3
   defines the event types as `COCKPIT_OK`/`COCKPIT_ERROR`, `NODE_X_UP`/`NODE_X_DOWN`
   and `CANLINK_X_Y_UP`/`CANLINK_X_Y_DOWN`. The vocabularies do not match.
2. Part 2 says the 20 ms heartbeat is also the clock-sync message, while checkoff
   step 2 expects `REF` every 20 ms and empty `APPEND` every 100 ms. That implies
   two separate periodic messages, with the election timeout driven off missed
   `REF`s. Current assumption: implement both.
3. The checkoff mentions a "Part 5 checkpoint" and "scenario (c) jumpers" that the
   handout does not define. Check Lab 3 and Piazza.

## Demo scenarios

Run in order. For each, record `getNetwork()` on every node and confirm all logs agree.

1. Link failure, default mode. Start devices, introduce a link failure. Examine
   network state after the failure.
2. Link failure and recovery, leader setup mode. Start, fail a link, recover.
   Examine state after failure and again after recovery.
3. Leader failure, leader setup mode. Start, fail the leader. Examine state, and
   the blinkers, to confirm time sync survives the election.

## Checkoff sequence

1. Cold start with `LEADER=cockpit` and the proxy stopped: all three nodes in the
   error state, hazards in step (FL, RL and HZ scoped), LEADER on the Pi only,
   SYNC on all three. Starting the proxy leaves the error state.
2. `candump -c any` shows every catalog ID including `REF` from the Pi every 20 ms
   and empty `APPEND` every 100 ms, and no `REQ_VOTE`. TA reads the cycle counter
   and master time out of `REF`.
3. Blinkers: left, right, cancel. FL and RL within 1 ms over ten edges.
4. Link faults, no election. TA picks a node; pull its A jumper, then B. DEG LEDs
   as in Lab 3, term unchanged on every console, one entry per change at the same
   index everywhere.
5. Relayed hop with the Pi as master. Pull N1 off B and N3 off A. Wheel still
   steers, blinks and brakes. FL and RL still within 1 ms, term unchanged, two
   entries committed, and an event from N2 reaches N3's log via the Pi.
6. Pi down. Within 100 ms: error state and motor braked. Within 1 s: one STM32
   LEADER on, `REF` from it, hazards at 4 Hz on FL and RL in step, no step larger
   than 100 microseconds at the handover.
7. Pi back. Rejoins as a follower. Held events appear on its display exactly once,
   in order, marked replayed, then live events. Hazards return to 2 Hz. LEADER
   stays on the STM32.
8. Random leader. Reboot with `LEADER=random`; an STM32 leads. Power it off: new
   leader within 1 s, `REF` moves, no failure mode. Power off the Pi: failure mode,
   held events, replay on return, with whichever node is leader. Then the seed that
   makes the Pi win, and step 6 again.
9. Relayed sync with an STM32 master. With an STM32 leading, pull the scenario (c)
   jumpers so the other STM32 is cut off. SYNC stays on there, REF_RX still
   toggles, FL and RL within 1 ms. Restore with no step on the edges.
10. Isolation and test button as in Lab 3, plus: the term changes only if the
    isolated node was leader, and exactly once.

Expect verbal questions throughout, directed at anyone on the team. One known
question: what would change with four nodes.

## Grading

| Item | Points |
|---|---|
| Live demo, the checkoff sequence | 40 |
| Checkoff questions answered by the team | 20 |
| GitHub link | 4 |
| Time drift adjustment: detecting when to use it and how it is applied, 1 page max | 13 |
| Raft state machine description, 1 page max | 13 |
| Raft client functionality and its effect on CAN routing during multi-link failures, 1 page max | 10 |

Report goes to Gradescope. Write the drift section from actual PPS printouts.

### Extra credit, 15 points

A cloud VM running Redis and a small web app joins the cluster as a client. The Pi
is the uplink, since it is the only node with a network interface.

| Redis structure | Key | Written when | Holds |
|---|---|---|---|
| Stream (`XADD`) | `vehicle:log` | every committed entry | term, index, type, payload, global time |
| Hash with TTL | `vehicle:heartbeat` | every 1 s | Pi wall clock, global time, FL/RL offset as last measured |

Publishing is asynchronous and best effort: a queue on the Pi drained by its own
thread, never on the CAN receive path, never delaying `REF`, `APPEND` or `CTRL`.
Show that in a scope capture. If the uplink drops, the queue keeps the last 1000
entries and resumes from the last acknowledged stream id.

The GUI shows the three nodes with alive/dead and bus A/B status, leader and time
master marked, and the count of events the leader is holding, plus a staleness
banner.

Points: 3 for secure deployment (no open Redis, HTTPS with login), 5 for publishing
as specified with resume after uplink loss, 4 for the five GUI views and the
staleness banner, 3 for the demonstration and a one-paragraph explanation of what
it would take to keep the GUI alive through a cockpit failure and why the Nucleos
cannot do it.

## Conventions

- Never use em-dashes in generated text, comments or documentation.
- The state machine stays a pure function of the log. No side channels into the
  network graph.
- Keep the config file identical across nodes. If a key is added, add it everywhere.
- Debug prints for elections and appends are required by the config, not optional.
