# Lab 3 Part 1: dual-CAN traffic test

Part 0 startup loopback is retained, followed by normal-mode traffic at 500 kbit/s.
Each Nucleo transmits every 100 ms on both CAN modules and receives its peer's frames.
Crystal frequency remains 16 MHz; SPI maximum remains 1 MHz. Wire as in WIRING.md.

**The user controls all builds and flashes. Do not run builds or flash automatically.**

## Build two images on the Mac

From this application directory:

```sh
source ./setup.sh
west build -b nucleo_f401re/stm32f401xe -d build-n2 . -- -DLAB_NODE_ID=2
west build -b nucleo_f401re/stm32f401xe -d build-n3 . -- -DLAB_NODE_ID=3
```

Label the physical Nucleos N2 and N3. Connect only N2's ST-LINK USB, then:

```sh
west flash -d build-n2 --runner openocd --verify --skip-rebuild
```

Swap to only N3's ST-LINK USB, then:

```sh
west flash -d build-n3 --runner openocd --verify --skip-rebuild
```

Do not flash the old `build` directory: it contains the earlier Part 0 image.
After both are flashed, power both Nucleos and the Pi. When wiring/power changes
are needed, power everything off first. Keep all host grounds connected and
positive supply rails separate. A powered CAN network should have all nodes powered.

Incremental builds: `west build -d build-n2` and `west build -d build-n3`.
The node ID is cached separately in each directory. If reconfiguring a stale build,
use `-p always` with the full command including its `-DLAB_NODE_ID` value.

## Pi: enable both interfaces and observe traffic

Boot overlays from Part 0 stay unchanged. From the Pi's copy of this repository:

```sh
sudo python3 pi/part0/can_check.py up
python3 pi/part1/check_traffic.py
candump -t d any
```

The check listens for ten seconds and expects N2 and N3 on each physical bus,
with advancing counters and about ten frames/second per stream (40 total/second).
It resolves A/B via SPI chip select, even if Linux names can0/can1 in reverse.
No pip dependencies. `candump` exits with Ctrl+C.

| ID | Sender | DLC | Payload |
| --- | --- | --- | --- |
| 0x702 | N2 | 8 | `02 bus counter_LE32 A5 5A` |
| 0x703 | N3 | 8 | `03 bus counter_LE32 A5 5A` |

`bus` is 00 for A and 01 for B; bytes 2-5 are an incrementing 32-bit little-endian
counter. Both copies share a counter, but these test payloads deliberately contain
an A/B marker to detect crossed bus wiring. IDs/payloads are temporary Part 1
instrumentation, not the later lab message catalog.

Example (interface names can swap):

```text
can0  702 [8] 02 00 01 00 00 00 A5 5A
can1  702 [8] 02 01 01 00 00 00 A5 5A
can0  703 [8] 03 00 01 00 00 00 A5 5A
can1  703 [8] 03 01 01 00 00 00 A5 5A
```

## Nucleo serial verification

```sh
python -m serial.tools.list_ports
python -m serial.tools.miniterm /dev/cu.usbmodemXXXX 115200
```

Select the corresponding board's port. Reset to see startup. Look for both loopback
passes and `Part 1 N2` or `Part 1 N3`, with `2/2 controllers ready`. Every five seconds
both bus statistics print: TX_OK counts successful completion (acknowledgment),
TX_ERR counts queue/completion failures and one-second pending timeouts, RX counts
valid diagnostic frames, and last/ctr identify the latest received frame.
With both boards running, TX_OK and RX should increase on both buses. Ctrl+] exits.

Optional Pi-to-STM test, using the actual interface names printed by the helper:

```sh
cansend can0 701#010000000000A55A
cansend can1 701#010100000000A55A
```

Those examples assume can0=A and can1=B. Each STM's RX count should increase;
`last` may already show a newer peer frame by the next five-second report.

## Part 1 fault check

1. Establish all four healthy streams first.
2. With `candump -t d any` running, disconnect **both CANH and CANL branch wires**
   from N2's A module. Leave its ground/power, the bus trunk, and both terminators intact.
3. N2's A stream stops on the Pi; N2 and N3 still appear on B; N3 still appears on A.
   N2's serial RX/TX_OK continue increasing on B. Its A controller may show errors.
4. Reconnect N2's A branch. Run the ten-second checker again after traffic settles.
5. Repeat with N3 and bus B as needed.

Each bus has at most one outstanding frame per Nucleo. A broken bus is not allowed
to block on a CAN acknowledgment. A pending frame is aborted and that controller
restarted after one second; this is diagnostic recovery, not the later 100 ms
recovery requirement. Successful traffic resumes after the physical fault is fixed.
If a stop/start fails, the console marks that controller inactive; reset after repair.

The Pi isn't periodically sending here: its receive/ACK path plus the Nucleo peer
statistics suffice for this test. Later firmware adds cockpit transmit tasks.
Part 1 also requires scope captures of CANH/CANL idle and a frame on both buses.
This test does not implement the later relay, heartbeat, watchdog, degraded LEDs,
or real-time task set.

## Files

`src/can_bringup.c` owns startup and traffic, with state private to each controller.
`CMakeLists.txt` selects LAB_NODE_ID (2 or 3; default 2). The same wiring/overlay
applies to both boards. `pi/part1/check_traffic.py` validates four received streams.
Historical Part 0 instructions are in docs/PART0.md. The Lab 2 docs that were in
docs/lab2/ were removed during the Lab 5 cleanup and remain in `lab3-649/`.
Old actuator drivers and UART proxy remain unbuilt for later integration.
