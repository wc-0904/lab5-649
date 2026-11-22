# Lab 5 code review and bench handoff — 2026-11-22

This is an engineering review/run guide, not the submission report. No hardware was flashed,
no Pi process was started, and no oscilloscope measurements were taken during this review.
The assignment PDF in the repository is the requirements source. WIRING.md and
DEMO_WIRING.md are the wiring references; the inherited README and TASKS notes contain
historical claims that are not new measurement evidence.

## Result

Software faults were present. The revised default build is ready for **bench testing**, not
certified as meeting the timing/checkoff requirements. Both Nucleo targets build locally.
245 host tests pass, including compiled C log/clock/codec tests, a new actual C Raft-agent
harness, and Python recovery/PPS regressions. Host kernel stubs do not model preemption,
interrupt latency, CAN arbitration, SPI contention, or the physical wiring.

Update both Nucleos and the Pi together. Reply IDs changed from a shared 0x054 to
per-sender 0x055/0x056/0x057; the existing consensus receive mask covers them.
The default config hash is `a2435d7f`. All three boot banners must match.

## Confirmed findings and changes

| Severity | Finding | Change / verification |
|---|---|---|
| High | A successful short APPEND committed up to the follower's local tail, including entries not matched to the leader. | Commit only through the prefix matched by that RPC. C-agent and Python regression scenarios cover a divergent tail. |
| High | A leader counted old-term ACKs toward its current-term majority. | Require an exact term match and reject indices beyond the leader's log. Covered in C and Python. |
| High | Client success was returned immediately on append, before majority replication. | Withhold success until commit; a retry then receives the committed index without another append. Covered in C and Python. |
| High | A previous-entry term mismatch truncated the log even though the RPC was rejected. | Reject without mutation; return a conservative backtrack hint. C/Python consistency tests cover this. |
| High | A rebooted follower retained its old leader-side matchIndex; accepting the first replayed entry jumped nextIndex back past the missing history. | Lower the match bound on rejection, then advance from replayed ACKs. Covered in C and Python. |
| High | Different nodes could send different client replies using the same CAN arbitration ID. | Give every reply sender its own ID; update firmware, Pi dispatcher, and tests together. |
| High | The STM32 retained only 4,096 entries while configuring 30,000; replaying an evicted prefix erased earlier network state. | Keep the configured history in 13-bit entries (8-bit term, 5-bit event code). 30,000 entries cost 48,750 bytes. Refuse new entries when full instead of evicting history. |
| High | The Pi's PPS always followed CLOCK_REALTIME, even while following an STM32 timeline. | Supply the agent's global clock to the PPS scheduler, refresh the mapping while waiting, and report actual global/local edge timestamps. |
| High | A local test failure stopped HB/REF but still allowed voting, replication and client participation. | Suppress consensus receive/periodic work while failed and relinquish the role without resetting term/vote. |
| Medium | Firmware never initialized the time estimator through reset; a predefined STM32 leader never acquired its own clock. | Initialize before CAN startup and explicitly establish the configured leader's timeline. |
| Medium | STM32 reference processing used the old master's/path's delay for the first new reference. | Set delay before applying the sample; set CLOCK_REALTIME from the adjusted current estimate. |
| Medium | A preempted clock reader could pass a timestamp older than the latest reference, which unsigned arithmetic interpreted as almost 71 minutes. | Use signed short counter differences; test the actual C arithmetic. Rebase leader time periodically to survive the 32-bit counter wrap. |
| Medium | PPS refinement could overwrite an already-fired compare; start/ISR state was not protected. | Mask interrupts while starting, reject pending compares and old deadlines, and record the evaluated global time rather than printing the intended second as measured time. |
| Medium | Pi PPS tests crashed on platforms without Linux scheduling symbols; permanent nanosleep errors retried forever. | Detect missing symbols, fall back to sleeping, and retry only EINTR. |
| Medium | SYNC on the Pi was reset every health poll even with fresh references. | Derive it from leadership or fresh REF reception; allow it to expire. |
| Medium | Hazard speed depended on whether the leader was the Pi, not whether the cockpit had failed. | Select the fast hazard rate from committed COCKPIT_ERROR. An STM32 leader with a healthy Pi is not itself a cockpit failure. |
| Medium | Pi heartbeat discovery appended a second NODE_UP beside the owner's report, which could clear already-recorded links. | Let the node's own client own its NODE_UP report. |
| Medium | STM32 append round-robin advanced even when no APPEND was sent. | Advance only when sending, avoiding timing-dependent peer starvation. |
| Medium | A client redirect could overwrite the Raft time-master identity; late REF copies could then move the clock to an old master. | Keep client redirect hints separate, and reject references from an unrelated known master. |
| Medium | Repeated whole-log replay occurred while the firmware spinlock masked interrupts. | Apply only newly committed entries to the live state machine. |
| Medium | CAN started-state crossed threads as an ordinary bool. | Publish/read it atomically. |
| Medium | Election term overflow could wrap C's wire value or raise a Python packing exception. | Stop initiating elections at term 255 rather than wrap; see limitations below. |
| Low | The boot pin walk emitted artificial LEADER/PPS edges during normal runs. | Default it off; it remains an explicit wiring diagnostic build option. |

## Assignment coverage and remaining issues

- **Parts 1–4:** all nodes have a Raft agent, client, shared config, bounded log and deterministic
  graph state machine. The default preselected-leader mode and seeded election mode exist.
  The corrected write path waits for a majority. Log storage is in RAM, **not durable**:
  currentTerm, votedFor and entries do not survive a node reset. Rejoining one reset node
  can replay from a surviving leader; this is not a general crash-safe Raft implementation.
  A simultaneous power loss loses all history. This remains a design limitation.
- **Part 5:** graph-derived routing-table functions exist and are tested. Live traffic still
  broadcasts on both buses; consensus relaying is enabled by a missing-link check in the
  committed graph rather than by a per-destination route selection. This is a reduced
  interpretation of the requested routing architecture, not full route-table integration.
  It needs explicit review against the TA's expectation. Multi-link convergence and whether
  the election term stays unchanged must be tested on the actual topology.
- **Part 6:** the corrected estimator, system-clock initialization, drift reports and PPS
  paths exist. The global time API is named `time_sync_global_us()` in C and `global_us()`
  on the Pi rather than literally `global_gettime()`. The estimator measures drift over a
  one-second window to reduce timestamp noise, not just two adjacent 20 ms frames.
  The existing `correction` field describes correction per one-second drift window, not
  the assignment's requested correction since the last heartbeat. Do not label it as the
  latter in the report without changing the telemetry or deriving that value explicitly.
- **Timing:** delay constants (Pi 658 us, STM32 400 us, extra relay 2,000 us) are inherited
  calibrations. They are not measurements of this revised build or necessarily valid for
  this wiring/load. A thread-driven Pi PPS, software-driven blinkers, UART prints and CAN
  queue delay cannot establish the 1 ms alignment or 100 us handover limit by inspection.
  Clock holdover still requires a reference/rebase within half the local counter range
  (about 35.8 minutes at 1 MHz); a very long isolated follower is outside that model.
- **Configuration limits:** exactly three nodes, at most 30,000 retained entries, and
  8-bit terms are explicit bounds. At log capacity new writes stop; at term 255 new
  elections stop. Archive measurements/history and reset the entire cluster for a new
  session before reaching these bounds. Heartbeat configuration is read by the Pi but
  the firmware's REF release is still tied to its 20 ms heartbeat task: use the supplied
  `heartbeat_ms=20` until that scheduling path is generalized.
- **Checkoff 7/8:** replay display logic exists, but duplicate suppression is not a durable
  client-session protocol. Request counters restart on reboot and log entries do not
  retain client request IDs. Exactly-once behavior across arbitrary leader/client crashes
  remains unproven; test the specified Pi-return sequence with full console logs.
- **Extra credit:** no Redis/cloud mirror was added. No submission report was written.

## GPIO review

The compiled devicetree and lab_io table show no overlapping active ownership for the new
Lab 5 pins. The repository's Morpho diagram agrees with these header positions:

| Signal | Both Nucleos | Header position | Pi BCM / physical |
|---|---|---|---|
| LEADER | PB12 | CN10-16 | GPIO19 / 35 |
| SYNC | PB13 | CN10-30 | GPIO26 / 37 |
| REF_RX | PB14 | CN10-28 | Not exported |
| HZ | PB15 | CN10-26 | Not exported |
| PPS | PA0, TIM5 CH1 | Arduino A0 / CN7-28 | GPIO13 / 33 |
| FL / RL | PC10 | CN7-1 | — |

SPI1 remains PA5/PA6/PA7; selects PB6/PC7; interrupt inputs PA10/PB5. SPI2 is disabled,
freeing PB12–PB15. USART1 is disabled to free PB6; USART2 retains PA2/PA3. TIM2 CH2
uses PA1; TIM5 CH1 uses PA0. PA5 LED/PWM consumers are disabled. I2C3 is disabled to
free PC9. PC13 remains the USER button. PC14 is a scope-only error output; LSE is disabled
in the compiled tree. The Pi outputs avoid SPI0 and CAN interrupts GPIO24/25.

This checks software ownership, not jumper continuity, solder bridges or signal integrity.
Follow WIRING.md: common grounds, separate host 3.3 V rails, 16 MHz modules, 500 kbit/s,
about 60 ohms H-to-L per unpowered bus. Remove any old sensor output from PA0. The
firmware still models actuators: its banner says no physical actuators. Scope outputs
are not a reviewed motor/servo control installation.

## Build and flash on the Mac

```sh
cd /Users/itsnathan21/nfs/cmu/18649/zephyrproject/zephyr/lab5-649
source ./setup.sh
west build -b nucleo_f401re/stm32f401xe -d build-lab5-n2 . -- -DLAB_NODE_ID=2 -DLAB_PIN_SELFTEST=OFF -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1 -DLAB_MEASURE_MODE=0
west build -b nucleo_f401re/stm32f401xe -d build-lab5-n3 . -- -DLAB_NODE_ID=3 -DLAB_PIN_SELFTEST=OFF -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1 -DLAB_MEASURE_MODE=0
python -m unittest discover -s tests -v
```

Attach **only N2's ST-LINK USB** and run:

```sh
west flash -d build-lab5-n2 -r openocd --no-rebuild --verify
```

Then attach **only N3's ST-LINK USB** and run:

```sh
west flash -d build-lab5-n3 -r openocd --no-rebuild --verify
```

Reconnect both. Identify each serial port and open two consoles:

```sh
python -m serial.tools.list_ports -v
python -m serial.tools.miniterm /dev/cu.usbmodemREPLACE_WITH_N2_PORT 115200
# In another terminal with setup.sh sourced:
python -m serial.tools.miniterm /dev/cu.usbmodemREPLACE_WITH_N3_PORT 115200
```

Expected: correct node identity, identical config hash, `Task startup: 0`, and
`pps: TIM5 clock 84000000 Hz, tick 1000000 Hz`. Exit miniterm with Ctrl+].

## Transfer the reviewed working tree

The changes are uncommitted; a Pi `git pull` does not fetch them. From the Mac repo,
substitute the Pi SSH login, then copy the source without build trees:

```sh
PI_HOST='YOUR_USER@YOUR_PI_HOST'
ssh "$PI_HOST" 'mkdir -p ~/lab5-649'
rsync -av --exclude='.git/' --exclude='build*/' --exclude='.cache/' \
  --exclude='__pycache__/' --exclude='pi/.venv/' ./ "$PI_HOST:lab5-649/"
```

## Run on the Pi

Check the existing MCP2515 overlay lines against `pi/part0/config.txt`; do not duplicate
those overlays. Reboot only if changing boot configuration. Then:

```sh
cd ~/lab5-649
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential can-utils
python3 -m venv pi/.venv
pi/.venv/bin/python -m pip install -r pi/demo/requirements.txt
mkdir -p captures
sudo python3 pi/demo/setup_can.py
sudo pi/.venv/bin/python -u pi/demo/cockpit.py --csv captures/baseline.csv 2>&1 | tee captures/pi-baseline.log
```

Leave the wheel/proxy stopped first: verify error hazards, SYNC and leader identity.
The cockpit program is the Pi Raft node and stays running; the UDP wheel sender is
what starts/stops for the initial proxy check.

In a second Pi terminal, after the initial observation:

```sh
cd ~/lab5-649
python3 pi/demo/send_test.py 127.0.0.1 --seconds 120 --throttle 25 --button 5
```

This is synthetic Lab 2-format wheel input. Use button 4 for right, 5 for left, 3 for
self-test. Use the real wheel/proxy instead when available; do not run two senders.
In a third Pi terminal:

```sh
cd ~/lab5-649
candump -t a any | tee captures/can-baseline.log
```

Use Ctrl+C to stop cockpit cleanly so its CSV is written. `--no-gpio` is available
for functional debugging but cannot provide PPS/LED scope measurements. Do not rerun
setup_can.py during a fault/recovery measurement: it resets the controllers.

## Bench acceptance before collecting final screenshots

1. Cold start with default cockpit leader, all three hash banners equal. No spontaneous
   elections or local-failure latch while steady traffic runs. Collect all consoles.
2. Check REF about every 20 ms and idle APPEND about every 100 ms. Confirm client replies
   are on 0x055..0x057. Compare each node's committed index and rendered network graph.
3. Scope N2/N3 PA0 plus Pi physical 33 for PPS. Separately scope N2/N3 PC10 and HZ PB15.
   Measure at least ten blinker edges, not a single favorable pair.
4. Pull/restore one branch at a time, then the assignment's complementary two-branch
   topology. Confirm graph convergence, no unwanted election, control delivery and skew.
5. Power off the Pi and capture braking, election, REF handover, hazard frequency and edge
   step. Restore the Pi; verify it follows, catches up, and labels held events replayed.
6. Repeat with `default_leader=random` and suitable seeds. Edit the shared config, rebuild
   both boards and recopy the Pi source/config each time. `tools/find_seed.py --node steering`
   and `--node cockpit` suggest RNG seeds; asynchronous startup/poll timing still affects
   the actual winner, so the seed alone is not proof of the election outcome.
7. Test follower and leader isolation separately, then USER-button failure/recovery.
   Record bus counters and startup/fault logs if anything misbehaves.

The next deliverable is measured evidence and any resulting corrections. Only then should
scope screenshots and the submission report be finalized.

## Bench follow-up: Pi bus-B recovery

The single-link bench test exposed a stale graph after reconnect and repeated drivetrain
NODE_UP entries. Two recovery defects were reproduced in host tests and corrected:

- Pi link polling could replace `link_event` while a previous report waited behind a
  self-report or uncommitted leader write. It now retains that report and leaves the
  second change pending detection until the slot is free.
- A live node could remain absent after its NODE_DOWN committed, because it only reset
  its presence reports on locally detected isolation or some leadership changes.
  Both firmware and Pi now reannounce after a newly committed local removal, followed
  by reports for the links they actually hear. Unrelated commits do not restart this
  repair, and a batch ending with the node already UP does not trigger another NODE_UP.

Validation: 250 host tests pass, including actual C-agent removal/rejoin tests and a Pi
scenario that queues both recovered links while waiting for replication. Both Nucleo
images rebuild successfully. These tests reproduce the software defects; they do not
establish the cause of the original receive interruption. Repeated drivetrain NODE_UP
at one term still needs the drivetrain serial console to distinguish reboot from local
isolation. Capture boot banners, `raft: isolated`, and local-failure lines if it recurs.

## Bench follow-up: false isolation from RX timestamp preemption

An N3 console capture showed `raft: isolated` followed by a new NODE_UP request while
PPS edge numbers and the local timer continued uninterrupted. In `lab_can_heard_within`,
uptime was sampled before reading the atomic last-RX timestamps. Higher-priority CAN
workers could preempt that reader and publish newer timestamps on both buses. Unsigned
subtraction then yielded an apparent age near UINT32_MAX, falsely exceeding the 600 ms
isolation threshold. `lab_can_link_healthy` had the same evaluation-order risk.

Both checks now read the receive timestamp before sampling uptime, using separate
statements. A host test compiles the production health-check functions and injects RX
updates immediately after the clock sample. It fails on the former implementation and
passes with this change; genuine silence, one surviving bus, unseen buses and ordinary
uptime wrap are also checked. All 251 tests pass and both firmware targets build. This
is a confirmed false-isolation mechanism; physical bench retesting is still required to
establish whether it explains every observed outage. No Pi or wire-format change is
needed for this particular fix.
