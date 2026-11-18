> Scope and LED pin map, including the Lab 5 outputs: [docs/DEMO_WIRING.md](docs/DEMO_WIRING.md).
> Sections describing diagnostic bring-up are historical.

# Wiring: two independent CAN controllers per host

Carried forward from Lab 3 unchanged. The vehicle, the two buses and every
host-to-module pin below are the same in Lab 5; what Lab 5 adds is five test
outputs per Nucleo, listed under "Pins claimed by firmware" and mapped in
docs/DEMO_WIRING.md.

Join the three A modules' CANH terminals to the A_H rail and CANL terminals to
the A_L rail; repeat separately for B. Fit 120 ohms across H/L at both ends of each bus, four total.
100 + 20 ohms in series is a valid 120-ohm termination. Keep P1 pads open and
measure approximately 60 ohms H-to-L on each bus with power off. Connect all host
grounds together. See README.md for traffic and branch-disconnection tests.
The disconnected-bus instructions below describe the earlier Part 0 stage only.

Power down before installing or moving wires. Each host has its own 3.3 V and GND
rails feeding only its two CAN modules. Do not join the hosts' 3.3 V rails. Power the
Nucleos through their ST-LINK USB connectors and the Pi through its own supply.
The CAN modules receive **3.3 V, never 5 V**. No XIAO processor board is needed.

Label the modules N2-A, N2-B, N3-A, N3-B, N1-A, N1-B, where A and B are the two CAN
buses.

> Historical, Part 0 only: that stage left every CANH/CANL screw terminal
> unconnected, with no terminators and no inter-node bus wires. Do not follow it
> now; both buses are built and terminated as described above.

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

The firmware disables USART1 (PB6), the PA5 LED alias, and the default TIM2/PWM
ownership of PA5. TIM2 is not unused: channel 2 is reassigned to **PA1**, which
carries SRV on N2 and PWM_OUT on N3. The physical onboard LED remains attached to
PA5 and may react to SPI clock activity; it is not a pass/fail indicator. ST-LINK
serial remains USART2 at 115200 baud.

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

## Pins claimed by firmware

Before connecting anything to a free-looking pin, check it here. Driving a pin the
firmware also drives, or tapping one it reads, is the usual cause of a board that
boots but behaves strangely.

| Pin | Claimed by | Note |
|---|---|---|
| PA5, PA6, PA7 | SPI1 SCK, MISO, MOSI | Shared by both CAN modules |
| PB6, PC7 | Chip select A, B | |
| PA10, PB5 | Interrupt A, B | Inputs from the modules |
| PA1 | TIM2 CH2 PWM | SRV on N2, PWM_OUT on N3 |
| PC0..PC6, PC8..PC12, PC14 | Lab 3 test points and LEDs | See docs/DEMO_WIRING.md |
| PC13 | Blue USER button | Input |
| PB12, PB13, PB14, PB15 | Lab 5 LEADER, SYNC, REF_RX, HZ | Silkscreen labels these SPI2; the overlay disables SPI2 |
| PA0 | Lab 5 PPS | Also **Arduino A0**, and TIM5 CH1 for the P7 compare |
| PA2, PA3 | USART2, the ST-LINK console | Do not repurpose |
| PA13, PA14, PB3 | SWD and SWO | Do not repurpose |

**PA0 was the Lab 2 left-motor current-sensor input.** If any ACS712 divider output
is still physically attached to A0, remove it before running firmware that drives
PA0 as an output.

## Module orientation and limits of Part 0

Use the labeled XIAO footprint contacts D6-D10, 3V3 and GND; do not count the
three screw terminals as SPI contacts. The screw terminals are CANH/CANL/GND for
later bus wiring. If your headers obscure the labels, consult the course pinout or
Seeed's hardware overview before applying power:
https://wiki.seeedstudio.com/xiao-can-bus-expansion/

The module crystal is **16 MHz**, confirmed for this kit, and the CAN bitrate is
500 kbit/s. Both match `boards/nucleo_f401re.overlay`. The overlay requests an SPI
ceiling of **4 MHz**; the STM32 divides its 84 MHz peripheral clock by the nearest
power of two, so SCK actually runs at about **2.625 MHz**. Measure SCK before using
it in a timing analysis rather than assuming either number.

Internal loopback checks the MCP2515 digital path, SPI, and interrupt handling. It
does not validate the transceiver, external CAN bus, termination, or physical bitrate.
Part 1 adds common ground between hosts, the two separately terminated buses, and
real inter-node send/receive tests. Two 120-ohm terminations per bus means four total.

Historical Lab 2 car wiring is not in this directory; it was removed with the rest of
the Lab 2 carry-over. Recover it from `lab3-649/docs/lab2/WIRING.md` if it is needed.
It is not the wiring for this build.
