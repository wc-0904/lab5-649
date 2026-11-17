# Lab 3 Part 0: dual MCP2515 bring-up

Two Nucleo-F401REs and a Raspberry Pi 4B each use two XIAO CAN modules with **16 MHz
crystals**. Wire each host to its modules using [WIRING.md](WIRING.md). Leave CANH/CANL
disconnected between nodes. No wheel, motor, sensor, or UART forwarder is needed.

## Firmware behavior

The same image runs on both Nucleos. On startup a dedicated worker tests CAN A then
CAN B with one internal-loopback frame, checking TX completion and the received
8-byte payload. Each passing controller is restarted in normal mode at 500 kbit/s,
without transmitting frames, and its state/error counters print every five seconds.
Both controllers must pass on both boards. Reset after correcting wiring with power off.

Expected output (per Nucleo):

```text
CAN A: internal loopback PASS (TX completion + exact 8-byte RX)
CAN A: ERROR-ACTIVE (state=0), tx_errors=0 rx_errors=0
CAN B: internal loopback PASS (TX completion + exact 8-byte RX)
CAN B: ERROR-ACTIVE (state=0), tx_errors=0 rx_errors=0
Part 0 startup: 2/2 controllers passed. Normal mode sends no frames.
```

A ready device alone is not enough; require both loopback PASS lines and both active
state lines. A loopback timeout points toward INT wiring (or SPI/CS); initialization
failure points toward power, SPI, CS, or the oscillator. Test results require hardware.

## Build and flash on this Mac

From `zephyrproject/zephyr/lab3`:

```sh
source ./setup.sh
west build -b nucleo_f401re/stm32f401xe -d build .
west flash -d build --runner openocd --verify
python -m serial.tools.list_ports
python -m serial.tools.miniterm /dev/cu.usbmodemXXXX 115200
```

Replace the serial port with the listed ST-LINK port. Press RESET to capture startup;
Ctrl+] exits the serial monitor. Flash one Nucleo at a time with only that board's
ST-LINK USB attached so the target is unambiguous. Repeat for the second board.
Both boards use identical firmware in Part 0; no node ID is required yet.
For a stale build, add `-p always` to the build command.

`setup.sh` resolves the workspace relative to this directory and activates its
`.venv`; it no longer assumes `~/zephyrproject`. Zephyr SDK is auto-discovered.
Build/cache outputs stay under this application directory.

## Raspberry Pi

Pull this revision on the Pi, then follow [pi/part0/README.md](pi/part0/README.md).
It includes boot overlay settings and the inspection/up/check commands. No Python
packages are required beyond Python 3. The script verifies real MCP2515 devices
and maps A/B by SPI chip select rather than assuming Linux probe order.

## Part 0 checkpoint

- Both Nucleos: 2/2 loopback passes and both controllers ERROR-ACTIVE in normal mode.
- Pi: both real SocketCAN interfaces UP, ERROR-ACTIVE, bitrate 500000.
- CANH/CANL still disconnected. No normal-mode CAN transmissions yet.

This is controller bring-up, not proof of external bus communication or timing.
Do not run `cansend` until another node can acknowledge the frame in Part 1.

## Layout and later work

- `src/main.c`: starts the diagnostic worker.
- `src/can_bringup.c`, `include/can_bringup.h`: loopback and state diagnostics.
- `boards/nucleo_f401re.overlay`: two SPI1 controllers, CS/INT pins, pin-conflict removal.
- `pi/part0/`: Linux boot configuration snippet, verification helper, instructions.
- `docs/lab2/`: historical operator/wiring/measurement documents.

Old Lab 2 driver sources and `pi/proxy_receiver` remain available for later integration,
but are not built or run by Part 0. The template application, redundancy, relay, LEDs,
watchdog and real-time analysis are later parts, not implemented here.

For the guiding questions: the MCP2515 ISR gives a semaphore; a separate per-device
thread reads SPI and dispatches RX/TX/error events. Its setting is
`CONFIG_CAN_MCP2515_INT_THREAD_PRIO=2`, passed to `K_PRIO_COOP` in this Zephyr version.
A full RX-buffer read transfers one command byte plus 13 register/data bytes: 112
SPI clock bits, or 112 us at exactly 1 MHz, excluding status reads, scheduling, and
other overhead. Measure the actual SPI clock and full service time for Part 3.
