> Active template demo: see [scope/LED pin map](docs/DEMO_WIRING.md).
> CAN pins below stay unchanged; the active SPI request is now 4 MHz.
> Sections describing diagnostic bring-up are historical.

# Lab 3 wiring: two independent controllers per host

**Current stage: Part 1.** Host-to-module pins below are unchanged. Join the three
A modules' CANH terminals to the A_H rail and CANL terminals to the A_L rail; repeat
separately for B. Fit 120 ohms across H/L at both ends of each bus, four total.
100 + 20 ohms in series is a valid 120-ohm termination. Keep P1 pads open and
measure approximately 60 ohms H-to-L on each bus with power off. Connect all host
grounds together. See README.md for traffic and branch-disconnection tests.
The disconnected-bus instructions below describe the earlier Part 0 stage only.

Power down before installing or moving wires. Each host has its own 3.3 V and GND
rails feeding only its two CAN modules. Do not join the hosts' 3.3 V rails. Power the
Nucleos through their ST-LINK USB connectors and the Pi through its own supply.
The CAN modules receive **3.3 V, never 5 V**. No XIAO processor board is needed.

Label the modules N2-A, N2-B, N3-A, N3-B, N1-A, N1-B. A/B is the eventual CAN bus.
For Part 0, leave every CANH/CANL screw terminal unconnected; no termination resistors
or inter-node bus wires are required. Keep P1 termination pads open for now.

## Handout correction: module MOSI/MISO

The Lab 3 v1.0 table swaps the module D9/D10 signal names. The manufacturer XIAO
pinout and CAN board schematic confirm **D10 = MOSI (MCP2515 SI)** and
**D9 = MISO (MCP2515 SO)**. The tables below use the actual hardware mapping.
Seeed schematic J3 pins 4/5 are MOSI/MISO respectively; D8 is SCK, D7 CS, D6 INT.
Sources:
- https://files.seeedstudio.com/wiki/xiao_can_bus_board/CAN_DEV_XIAO.zip
- https://wiki.seeedstudio.com/Seeeduino-XIAO/

## Both Nucleo-F401RE boards: identical wiring

Use the Nucleo's **Arduino header labels** in this table. `D13` means the contact
printed D13 on the Nucleo, NOT physical connector pin 13. MCU port names provide a
second check. Module D numbers belong to the XIAO footprint and do NOT match the
same-numbered Arduino pin on the Nucleo.

| Signal | CAN module A contact | CAN module B contact | Nucleo destination |
| --- | --- | --- | --- |
| Supply | 3V3 | 3V3 | Power header **3V3** |
| Ground | GND | GND | Power header **GND** |
| SPI clock | D8 / SCK | D8 / SCK | **D13 / PA5** |
| Host to module data | D10 / MOSI | D10 / MOSI | **D11 / PA7** |
| Module to host data | D9 / MISO | D9 / MISO | **D12 / PA6** |
| A chip select | D7 / CS | — | **D10 / PB6** |
| B chip select | — | D7 / CS | **D9 / PC7** |
| A interrupt | D6 / INT | — | **D2 / PA10** |
| B interrupt | — | D6 / INT | **D4 / PB5** |

Fan out SCK, MOSI, MISO, 3V3, and GND to both modules. CS and INT must remain separate.
MOSI connects to MOSI, MISO to MISO (do not cross them like UART TX/RX).
Leave the module's 5V pin and all other unused footprint pins disconnected.

The firmware disables USART1 (PB6), the PA5 LED alias, and TIM2/PWM ownership of PA5.
The physical onboard LED remains attached to PA5 and may react to SPI clock activity;
it is not a pass/fail indicator. ST-LINK serial remains USART2 at 115200 baud.

## Raspberry Pi 4B: physical 40-pin header numbers

**Physical pin** means position on the Pi's 2x20 header, not BCM GPIO number.
Find pin 1 (3.3 V; square pad on the PCB); pin 2 is directly opposite it.
One column counts 1,3,5,...,39; the other 2,4,6,...,40.

| Signal | CAN module A contact | CAN module B contact | Pi physical pin | BCM/function |
| --- | --- | --- | --- | --- |
| Supply | 3V3 | 3V3 | **1** (fan out to both) | 3.3 V |
| Ground | GND | GND | **6** (fan out to both) | GND |
| SPI clock | D8 / SCK | D8 / SCK | **23** | GPIO11 / SPI0 SCLK |
| Host to module data | D10 / MOSI | D10 / MOSI | **19** | GPIO10 / SPI0 MOSI |
| Module to host data | D9 / MISO | D9 / MISO | **21** | GPIO9 / SPI0 MISO |
| A chip select | D7 / CS | — | **24** | GPIO8 / CE0 / spi0.0 |
| B chip select | — | D7 / CS | **26** | GPIO7 / CE1 / spi0.1 |
| A interrupt | D6 / INT | — | **22** | GPIO25 |
| B interrupt | — | D6 / INT | **18** | GPIO24 |

Disconnect the old Pi UART/timing wires. In particular, physical pin 18 / GPIO24 is
now an input from CAN B INT, not the old CMD_TX timing output. Do not run the old
forwarder with `--trace-gpio`. The old car does not need to be dismantled.

## Module orientation and limits of Part 0

Use the labeled XIAO footprint contacts D6-D10, 3V3 and GND; do not count the
three screw terminals as SPI contacts. The screw terminals are CANH/CANL/GND for
later bus wiring. If your headers obscure the labels, consult the course pinout or
Seeed's hardware overview before applying power:
https://wiki.seeedstudio.com/xiao-can-bus-expansion/

The module crystal is **16 MHz**, confirmed for this kit. CAN bitrate is 500 kbit/s;
SPI is configured for a maximum of 1 MHz for initial breadboard testing. The STM32
clock divider can round SPI down; measure SCK before using it in a timing analysis.

Internal loopback checks the MCP2515 digital path, SPI, and interrupt handling. It
does not validate the transceiver, external CAN bus, termination, or physical bitrate.
Part 1 adds common ground between hosts, the two separately terminated buses, and
real inter-node send/receive tests. Two 120-ohm terminations per bus means four total.

Historical car wiring is in docs/lab2/WIRING.md; it is not the wiring for this build.
