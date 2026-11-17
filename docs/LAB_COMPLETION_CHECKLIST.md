# Lab completion checklist — October 22 clarification

Operational checklist, not a replacement analysis/writeup. Existing ANALYSIS,
FOUNDATIONS, RTA_TABLES and RELAY prose has not been revised for the clarification;
its old per-zone FAULT IDs and calculated numbers must not be submitted unchanged.
No firmware build, flash, or hardware timing validation was performed by the assistant.

## 1. Required wiring

Three separate small test-point breadboards: one per node. Each row below is an
independent electrically connected group of holes, carrying ONE signal. Never
join unlike signals. The two GND rows may be joined. Existing LED signals may be
tapped; no additional LEDs are required. CN7-35 means connector CN7, pin 35.
Use connector numbering on the board, not a guessed counting direction.

| Row | Pi/N1 (physical pin; BCM GPIO) | N2 | N3 |
|---|---|---|---|
| 1 | GND pin 6 | GND on power header | GND on power header |
| 2 | GND pin 9 | GND on power header | GND on power header |
| 3 | UDP_RX: 16; GPIO23 | CAN_RX_A: PC0, Arduino A5 | CAN_RX_A: PC0, Arduino A5 |
| 4 | CMD_TX: 15; GPIO22 | CAN_RX_B: PC1, Arduino A4 | CAN_RX_B: PC1, Arduino A4 |
| 5 | CAN_TX_A: 11; GPIO17 | PC2, CN7-35 | PC2, CN7-35 |
| 6 | CAN_TX_B: 13; GPIO27 | PC3, CN7-37 | PC3, CN7-37 |
| 7 | DEG_A: 29; GPIO5 | PC4, CN10-34 | PC4, CN10-34 |
| 8 | DEG_B: 31; GPIO6 | PC5, CN10-6 | PC5, CN10-6 |
| 9 | unused | SRV: PA1, Arduino A1 | PWM_SET: PC6, CN10-4 |
| 10 | unused | FL: PC10, CN7-1 | PWM_OUT: PA1, Arduino A1 |
| 11 | unused | FR: PC12, CN7-3 | DIR_A: PC8, CN10-2 |
| 12 | unused | RELAY_TX: PC9, CN10-1 | RL: PC10, CN7-1 |
| 13 | unused | ERR: PC14, CN7-25 | RR: PC12, CN7-3 |

Keep six degraded LEDs and four blinkers, each with its existing series resistor.
No physical motor, servo, H-bridge, encoder or feedback sensor is required.
Scope ground clips go to common GND, never CANH/CANL.

Expose and label A_H, A_L, B_H, B_L on the bus breadboard. Keep each node's H/L
branch removable independently, without removing module power/ground or termination.
Two 120-ohm termination networks per bus, one at each backbone end; all P1 pads
open when using discrete terminators. Existing 100+20-ohm series pairs are 120-ohm
networks. Record resistance across H/L on each bus, POWER OFF: approximately 60 ohms.
Already-soldered P1 termination is the clarification's exception: identify the
modules to the TA and do not also install discrete terminators on that bus.

Existing module wiring (retain it):

| Function | Module pin | Each STM | Pi physical pin (BCM) |
|---|---|---|---|
| 3.3 V | 3V3 | 3V3 | 1 |
| GND | GND | GND | 6 |
| SCK | D8 | PA5 / Arduino D13 | 23 (11) |
| MOSI | D10 | PA7 / Arduino D11 | 19 (10) |
| MISO | D9 | PA6 / Arduino D12 | 21 (9) |
| CAN A CS | D7 | PB6 / Arduino D10 | 24 (8) |
| CAN A INT | D6 | PA10 / Arduino D2 | 22 (25) |
| CAN B CS | D7 | PC7 / Arduino D9 | 26 (7) |
| CAN B INT | D6 | PB5 / Arduino D4 | 18 (24) |

Module D9/D10 here follow the working module mapping, correcting the handout's
MOSI/MISO reversal. Do not confuse module D10 with Arduino D10.
INT/CS can be probed at the existing connections: extra breakout rows are NOT required.

## 2. Software and normal build

Current changes: shared FAULT 0x020 with source zone in byte 1; dedup distinguishes
FAULT sources. Pi CMD_TX follows the first CAN write. STM RELAY_TX follows a
successful asynchronous can_send return (not wire completion). Double-press
recovery is implemented on each STM and the Pi. One press fails a healthy node;
while failed, two new presses within 500 ms clear its local latch. Fresh input
and peer heartbeats are still needed to leave error. STM debounce is 50 ms.

Update both STM images and the Pi scripts together. No changes have been pushed
by the assistant: transfer/sync this working tree to the Pi using your usual method.
The shared FAULT ID follows the template; simultaneous distinct FAULT producers
can contend with identical CAN IDs, so verify the bad-input check on hardware.

Mac, from lab3:

```sh
source ./setup.sh
west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2 -DLAB_MEASURE_MODE=0 -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1
west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3 -DLAB_MEASURE_MODE=0 -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1
```

Connect ONLY the intended Nucleo's USB while flashing it:

```sh
west flash -d build-demo-n2 --runner openocd --verify --skip-rebuild
# Switch USB to N3 before this command:
west flash -d build-demo-n3 --runner openocd --verify --skip-rebuild
```

Reconnect both. Both must print startup 0 and measurement mode 0.
Pi, after syncing the changed files:

```sh
cd ~/Documents/lab3-649
sudo python3 pi/demo/setup_can.py
sudo pi/.venv/bin/python pi/demo/cockpit.py
```

The real wheel proxy sends its normal 276-byte UDP packets to PI_IP:8000. Keep
cockpit running throughout checkoff. Existing buttons: index 3 test, 4 right,
5 left; simultaneous new 4+5 requests hazards. Use the wheel at checkoff.

For internal testing ONLY, replace the wheel sender with (second Pi terminal):

```sh
python3 pi/demo/send_test.py 127.0.0.1 --seconds 600 --throttle 0 --brake-cycle
```

Do not run real and simulated senders together. Optional internal recovery test:
`python3 pi/demo/send_test.py 127.0.0.1 --seconds 10 --throttle 0 --test-recovery`
fails at 2 seconds and sends recovery presses at 4 and 4.3 seconds.

## 3. Six required report measurements

Each gets a scope screenshot with labeled channels, time scale and maximum in us.
Capture at least 60 seconds after settling. Reset scope statistics between runs.
Record firmware mode, node, topology, traffic, duration and maximum. C GPIO on
STM; Python/libgpiod GPIO on Pi. Measurements include marker overhead; they are
observed maxima, not proven hard WCETs.

| # | Value | Start | Stop |
|---|---|---|---|
| 1 | Pi stage | Pi row 3 / physical16 / GPIO23 UDP_RX | Pi row4 / physical15 / GPIO22 CMD_TX |
| 2 | N3 CAN_RX C | CAN A INT, PA10 / D2 falling | N3 row3, PC0 / A5 CAN_RX_A |
| 3 | N3 DRIVE C | N3 row3, PC0 / A5 CAN_RX_A for CTRL | N3 row11, PC8 / CN10-2 DIR_A rising on brake |
| 4 | SPI frame load B | CS falling | SAME CS rising; longest low window |
| 5 | N2 CAN_RX C | CAN A INT, PA10 / D2 falling | N2 row3, PC0 / A5 CAN_RX_A |
| 6 | N2 RELAY C | N2 row3, PC0 / A5 CAN_RX_A | N2 row12, PC9 / CN10-1 RELAY_TX |

#4: use N2 CAN B CS (PC7 / Arduino D9) during the relay measurement; also inspect
CAN A CS (PB6 / Arduino D10) for a longer SPI critical transaction. Record which
CS/window produced the maximum. One probe is sufficient for CS pulse width.

Match start and stop to the SAME frame; INT also signals TX/errors. Toggle markers
can rise or fall. #3 requires repeated released-to-applied brake transitions, not
a permanently held brake. #6 includes dispatch/relay/TX-worker submission, not
just the relay thread's software queue operation.

### Measurement #1: normal firmware and wheel stream

Use normal firmware on both STMs and both buses connected. Run:

```sh
sudo pi/.venv/bin/python pi/demo/cockpit.py --duration 70 --csv pi/demo/pi-stage.csv
```

Keep wheel packets flowing. Scope row3 to row4 for at least a minute. The CSV and
printed max/p99 measure UDP dequeue to the first CAN write marker; reject runs
with first-write failures or sample overflow. Scope is the required evidence.
Original handout additionally requests Pi max/p99 with and without stress; retain
these as two conditions of #1 (clarification does not explicitly waive them).
For a repeatable CPU-loaded condition, run this in another Pi terminal before
starting the 70-second cockpit capture:

```sh
python3 - <<'PY'
import multiprocessing as mp
import time

def load():
    end = time.monotonic() + 90
    while time.monotonic() < end:
        pass

workers = [mp.Process(target=load) for _ in range(4)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
PY
```

Name the second CSV `pi-stage-load.csv`, and record four busy Python processes,
90 seconds, default Linux scheduling. Do not claim a p99 from a single screenshot.

### Measurements #2–#6: isolated firmware, no cockpit

Stop cockpit and the wheel/simulated UDP sender. The special STM images do not
run PID, blink, heartbeat, health monitor, or test-button tasks. Only the measured
path, required CAN drivers/workers and a lower-priority watchdog supervisor run.
These images are NOT the functional demo and intentionally do not implement its
heartbeat/fault behavior. The watchdog remains enabled. Quiet CTRL-only traffic
avoids unrelated receive/TX-completion interference on the measured CAN A INT.

Build the four measurement images on the Mac:

```sh
west build -b nucleo_f401re/stm32f401xe -d build-measure-n2-rx . -- -DLAB_NODE_ID=2 -DLAB_MEASURE_MODE=1 -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1
west build -b nucleo_f401re/stm32f401xe -d build-measure-n3-rx . -- -DLAB_NODE_ID=3 -DLAB_MEASURE_MODE=1 -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1
west build -b nucleo_f401re/stm32f401xe -d build-measure-n3-drive . -- -DLAB_NODE_ID=3 -DLAB_MEASURE_MODE=2 -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1
west build -b nucleo_f401re/stm32f401xe -d build-measure-n2-relay . -- -DLAB_NODE_ID=2 -DLAB_MEASURE_MODE=3 -DLAB_DIAGNOSTICS=OFF -DLAB_TRACE_TASK=-1
```

Flash only the intended board at each step (disconnect the other's USB):

1. #2: N2 RX-only, N3 RX-only; buses connected normally.

```sh
west flash -d build-measure-n2-rx --runner openocd --verify --skip-rebuild
# Switch USB to N3:
west flash -d build-measure-n3-rx --runner openocd --verify --skip-rebuild
```

2. #3: keep N2 RX-only, flash N3 DRIVE.

```sh
west flash -d build-measure-n3-drive --runner openocd --verify --skip-rebuild
```

3. #4/#5/#6: flash N2 RELAY and N3 RX-only. Disconnect Pi-B and N3-A. N2 stays
   on both. Termination remains on both backbones.

```sh
west flash -d build-measure-n2-relay --runner openocd --verify --skip-rebuild
# Switch USB to N3:
west flash -d build-measure-n3-rx --runner openocd --verify --skip-rebuild
```

Reconnect/power both boards before every capture. For EACH capture, run on Pi:

```sh
python3 pi/demo/measure_can.py --seconds 70
```

This sends valid CTRL on CAN A every 20 ms, with zero throttle and alternating
0/100 brake each second. It is not the cockpit and sends no heartbeat or BLINK.
Check send errors = 0. It discovers CAN A by SPI identity, not guessed can numbering.
Both Nucleos should remain running without resetting for the entire capture.

After #2–#6: restore all CAN branches and flash BOTH `build-demo-n2` and
`build-demo-n3` using the normal flash commands. Verify measurement mode 0.

## 4. Required functional/checkoff evidence and fault matrix

Normal firmware, real wheel, cockpit continuously running. No interface setup
resets during a recovery test. These are retained checkoff requirements, separate
from the six execution/latency inputs to the report.

- Cold start with wheel proxy stopped: error/hazards; start proxy and verify normal
  operation once peer heartbeats and valid CTRL arrive.
- Show `candump -t d can0 can1`: CTRL010, FAULT020 on an event, HB100/101/102,
  BLINK200, FORCE300. Sequence low nibble increments per originating ID; relay
  copies repeat the original payload. HB20ms, FORCE50ms; BLINK on changes/edges.
- Wheel left/center/right: scope N2 SRV row9 (PA1/A1), 50Hz and approximately
  1/1.5/2ms high pulses. Verify steering response against 50ms.
- Left/right/cancel blinkers. Scope N2 FL row10 (PC10/CN7-1) and N3 RL row12
  (PC10/CN7-1). Save maximum front/rear offset over at least ten edges: <=1ms.
  Verify normal blink 1Hz +/-10%, start <=100ms.
- Save direct brake-chain capture: Pi UDP_RX row3 to N3 DIR_A row11. With four
  channels add Pi CAN_TX_A row5 and N3 CAN_RX_A row3 to distinguish stages.
  Compare end-to-end maximum to 2ms; apply/release pedal to obtain DIR transitions.
- Save relayed brake-chain capture: Pi UDP_RX row3, N2 RELAY_TX row12, N3 DIR_A
  row11, with Pi-B and N3-A disconnected. Compare to 2ms; report misses honestly.
- Single-node A/B branch removal at each selectable node: degraded indication
  <=60ms, controls remain functional; restore, degraded indication clears <=100ms.
- Whole-bus loss: remove all A branches, operate on B; restore. Rehearse reverse.
- Four split cases (controls continue, degraded indications on lost connections):

| Disconnected branches | Relay |
|---|---|
| Pi-B, N3-A | N2 (report case) |
| Pi-A, N3-B | N2 |
| Pi-A, N2-B | N3 |
| Pi-B, N2-A | N3 |

- Full STM isolation: remove that node's A and B branches. Isolated node fail-safe,
  other nodes system error within100ms. Restore both: normal within500ms, no reset.
- Blue USER B1 on each STM (PC13, not black RESET): local failure <=10ms,
  system error/hazards/brake <=100ms. While failed, double press within500ms:
  heartbeats resume and normal operation returns with valid traffic. Repeat with
  wheel test button (index3) for Pi. N2 ERR is row13; N3 braking is DIR_A row11.
  Button PC13 can be probed at the existing button connection if timing the press.
- Bad input: restart cockpit with `--inject-invalid` while valid wheel data flows;
  the next CTRL has throttle101 with valid CRC. Verify receiving-zone error and
  FAULT020 with source zone in byte1. Double press each failed STM to recover.

Record fault-matrix removal configuration, zone/LED states, detection/recovery
elapsed time and pass/fail. Measure from the physical change, not from the last
heartbeat. If using timestamped/high-frame-rate video of contact and LEDs, record
its frame interval and uncertainty; borderline results need a more precise scope
capture with an event reference. A stopwatch/visual guess cannot establish60ms.
A Pi-only bridge is not required. Do not add motors or feedback sensors.

## 5. Hand-in checklist

- Wiring schematic: both buses, terminators, all six modules' SPI/CS/INT/power/GND,
  test-point boards. Circuit photographs are not an additional stated requirement.
- Six measurement values with six scope screenshots; Pi max/p99 conditions above.
- Required checkoff brake-chain/blink captures and fault matrix with measured recovery.
- Task graph with all template tasks including TEST_BTN, seven messages, IDs/periods,
  both-bus sends and four highlighted chains; add N2 RELAY and forwarded edges.
- Single-bus RTA for every N2/N3 task, only CTRL message RTA required, five chain
  results. Use supplied C placeholders except the specified measurements.
- Relay case N2 re-analysis, per-bus utilization starting with the specified8.6%
  baseline plus enumerated forwarded traffic, relayed CTRL jitter/response and
  five chain verdicts. Do not double-count first-hop/Pi jitter.
- Team-defined values and Lab1 revision history. Report <=6 pages excluding
  schematic/scope captures. Prepare every teammate for the analysis walk-through.
