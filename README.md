> October 22 clarification: use [the completion checklist](docs/LAB_COMPLETION_CHECKLIST.md)
> for current normal/measurement builds, wiring, captures and recovery. The report
> drafts have not been rewritten; their older FAULT catalog and RTA numbers need
> revision before submission.

# Lab 3 relay-enabled task demo — simulated actuators

The active build uses preemptive template tasks, dual CAN, real PWM/GPIO test
outputs, simulated N3 PID feedback, and dummy FORCE. **Do not connect motors,
H-bridges or servos.** This relay revision has not yet been built or tested on hardware.
The user performs all builds/flashes. Forwarding is enabled on Pi, N2 and N3; cross-bus operation and timing still need hardware validation.
Read [relay policy and tests](docs/RELAY.md). Update both STM32s and the Pi together: FAULT now uses template ID 0x020 and payload source zone.

Start with [foundations](docs/FOUNDATIONS.md), [writeup/math](docs/ANALYSIS.md),
[full first-pass tables](docs/RTA_TABLES.md), [task graph](docs/TASK_GRAPH.md),
and [new output wiring](docs/DEMO_WIRING.md). Existing CAN wiring stays intact.
Historical Part 1 procedure: [docs/PART1.md](docs/PART1.md).

## Build and flash on the Mac

Use new build directories so the last working Part 1 binaries remain available.
From this project directory:

```sh
source ./setup.sh
west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2
west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3
```

Connect **only N2's USB** for the first flash, then only N3's USB for the second.
West renamed `--skip-rebuild` to `--no-rebuild`; older west versions want the old
spelling, and the openocd runner rejects whichever one it does not know:

```sh
west flash -d build-demo-n2 -r openocd --no-rebuild --verify
west flash -d build-demo-n3 -r openocd --no-rebuild --verify
```

Reconnect both. Console should report `Task startup: 0; relay node=2` on N2 (`relay node=3` on N3).
Nonzero startup is a failure: stop and inspect it before interpreting traffic.

```sh
python -m serial.tools.list_ports
python -m serial.tools.miniterm /dev/cu.usbmodemYOUR_PORT 115200
```

Exit miniterm with Ctrl+]. Startup error/hazards until peers and fresh wheel input
arrive is expected. Periodic console output is disabled by default to avoid UART
printing distorting the timing measurements.

## Build and flash on WSL

The Mac section above does not apply here. `setup.sh` expects the Mac layout and
fails on WSL, where the Zephyr workspace and the SDK live in the home directory.
Use `setup_win.sh`, which checks the venv, the Zephyr tree and the SDK before it
changes anything and prints the versions it selected:

```sh
source ./setup_win.sh
west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2
west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3
```

WSL2 does not pass USB devices through on its own, so the ST-LINK is invisible
until it is attached with `usbipd-win`. From an **elevated Windows PowerShell**:

```powershell
usbipd list                          # find the ST-LINK BUSID
usbipd bind --busid <BUSID>          # once per device, needs administrator
usbipd attach --wsl --busid <BUSID>  # again after every replug
```

### Flashing the right board when both are attached

`west flash -i <serial>` does **not** select the adapter: openocd matches a serial
string that does not correspond to what `pyserial` prints, and on a failed match it
silently falls back to the first adapter instead of erroring. Select by USB topology
instead, which openocd matches directly:

```sh
python -m serial.tools.list_ports -v        # read LOCATION=<bus>-<port> for each board
west flash -d build-demo-n2 -r openocd --no-rebuild --verify \
  --cmd-pre-init-flash "adapter usb location 1-1"
west flash -d build-demo-n3 -r openocd --no-rebuild --verify \
  --cmd-pre-init-flash "adapter usb location 1-2"
```

openocd echoes `adapter usb location: 1-1` when the selection takes. Confirm the two
flashes report **different** verify sizes, since the images differ by node: N2 is
about 66.5 KB and N3 about 67.2 KB. Identical sizes mean the same board was
programmed twice. Locations change when cables are moved between ports, so re-read
them rather than reusing old values.

Back in WSL, `lsusb` should list the ST-LINK and `/dev/ttyACM0` should appear.
Attach one board at a time, which is also what the one-USB-at-a-time flashing
rule above requires:

```sh
west flash -d build-demo-n2 -r openocd --no-rebuild --verify
```

Detach N2, attach N3, then flash `build-demo-n3` the same way. The console is at
`/dev/ttyACM0`, not the `/dev/cu.usbmodem*` path the Mac section uses:

```sh
python -m serial.tools.list_ports
python -m serial.tools.miniterm /dev/ttyACM0 115200
```

`60-openocd.rules` is already installed, so openocd should reach the probe
without `sudo`. If it reports a permission error, the attach step was skipped or
the rules did not apply to the newly attached device.

## Pi setup and functional run

Edit the two existing MCP2515 overlay lines in `/boot/firmware/config.txt` to set
`spimaxfrequency=4000000`, keeping oscillator=16000000 and interrupts 25/24.
Use `pi/part0/config.txt` as the exact reference. Do not append duplicate overlays.
Reboot, then from the pulled repository on the Pi:

```sh
sudo python3 pi/demo/setup_can.py
python3 pi/demo/cockpit.py --no-gpio --duration 70 --csv pi/demo/baseline.csv
```

The demo setup enables 20 ms Linux bus-off restart and bounds the software TX
queue to eight frames. Run it before traffic, never during recovery timing tests.

In a **second SSH terminal**, provide simulated wheel input for the functional test:

```sh
python3 pi/demo/send_test.py 127.0.0.1 --seconds 60 --throttle 25 --button 5
```

This generates the actual Lab 2 UDP format, approximately every 20 ms. A real wheel
sender may replace it; do not run both senders at once. `--button 5` pulses the left
blinker button after two seconds; use 4 for right and 3 for cockpit self-test.
When UDP stops, nodes return to safe outputs after the freshness timeout.

In another terminal, `candump -t d any` should show HB 0x100/101/102 every ~20 ms,
CTRL 0x010 while input is fresh, BLINK 0x200, and dummy FORCE 0x300 every ~50 ms.
FAULT 0x020 is event-driven, not periodic healthy traffic. The old
`pi/part1/check_traffic.py` expects 0x702/703 and does not validate this demo.

## Enable Pi GPIO markers for measurements

Use Python 3.10+ and libgpiod **v2 Python bindings** in a Pi virtual environment:

```sh
python3 -m venv pi/.venv
pi/.venv/bin/python -m pip install -r pi/demo/requirements.txt
sudo pi/.venv/bin/python pi/demo/cockpit.py --duration 70 --csv pi/demo/gpio-baseline.csv
```

The GPIO mode uses `/dev/gpiochip0` on Pi 4B. `--no-gpio` is an explicit functional
fallback when bindings/access are not ready; it cannot produce scope markers.
Repeat with stress load later and report both runs. CSV output is written on exit;
in-memory capacity is 200,000 samples, with an overflow count. Successful socket
writes are reported separately from end-to-end reception or wire completion.
No scheduling/CPU-affinity policy is changed automatically.

## STM32 software timing observations

Task indices: 0 dispatch, 1 STEER/DRIVE, 2 blink, 3 test/fault, 4 PID, 5 HB_TX,
6 HB_MON, 7 FORCE, 8 RELAY (both STM32s), 9 diagnostics, 10 DRIFT. Index 10 is the
Lab 5 time-synchronization report task; it runs once a second and is deliberately
outside the watchdog's progress check, which only covers indices 0 to 8. For
example, configure N3 to trace PID on PC11:

```sh
west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3 -DLAB_TRACE_TASK=4
west flash -d build-demo-n3 -r openocd --no-rebuild --verify
```

PC11 goes high during the selected body. This includes preemption and waiting.
To print per-task elapsed maxima every two seconds for exploratory runs, add
`-DLAB_DIAGNOSTICS=ON` to the build command. **Those prints are intrusive** and
should be disabled for final scope timing using `-DLAB_DIAGNOSTICS=OFF`.
Use `-DLAB_TRACE_TASK=-1` to remove the optional task-envelope marker.

## Functional fault checks before measurements

- Press N2/N3 blue USER button: local fail latches, HB stops, peers brake/hazard.
  While failed, press USER twice within 500 ms to clear the local latch.
- Restart cockpit with `--inject-invalid`: the next CTRL has throttle=101 with a
  valid CRC. Both receiving STM32 nodes should latch failure. Double press USER on each failed board afterward.
- Remove one module's A or B CANH/CANL branch, leaving its power, ground and the
  backbone terminators. Check degraded output and continued healthy-bus control.
  Reconnect and observe recovery; timing limits remain unverified.
- Both buses lost to a peer causes error after heartbeat expiry. Complementary
  N1/N3 bus isolation is supported through any node that remains on both buses; follow docs/RELAY.md to test all three bridge roles.

A software run can fail these checks; record the result and debug it. The source
implementation is not evidence that the real hardware already passed them.

## Host checks and math

```sh
python3 -m unittest discover -s tests -p test_demo.py -v
python3 tools/analyze_demo.py
```

These Python checks do not build or flash firmware. The analysis intentionally
shows bounds that do not establish several deadlines; measured costs and tighter
arrival/queue models are still needed.
