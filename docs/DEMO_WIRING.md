# Relay demo scope / LED wiring

Keep all six CAN modules and both terminated backbones as already tested.
**No actuator, H-bridge, encoder, current sensor or servo is connected.** Use the
new test breadboards; do not reuse old Lab 2 output pin assignments blindly.
All grounds are common. Each LED needs its own series resistor (e.g. 1 kohm).
Scope grounds connect to GND, never to CANH/CANL.

STM32 pin names below apply identically on each board. CN7 is the left Morpho
header and CN10 is the right, as named on the Nucleo PCB. Use printed connector
pin numbers / the board pinout, not a guessed counting orientation.

| Test row | N2 signal | N2 MCU/header | N3 signal | N3 MCU/header |
|---|---|---|---|---|
| 1 | GND | GND | GND | GND |
| 2 | GND | GND | GND | GND |
| 3 | CAN_RX_A | PC0 / Arduino A5 | CAN_RX_A | PC0 / Arduino A5 |
| 4 | CAN_RX_B | PC1 / Arduino A4 | CAN_RX_B | PC1 / Arduino A4 |
| 5 | CAN_TX_A | PC2 / CN7 pin 35 | CAN_TX_A | PC2 / CN7 pin 35 |
| 6 | CAN_TX_B | PC3 / CN7 pin 37 | CAN_TX_B | PC3 / CN7 pin 37 |
| 7 | DEG_A LED | PC4 / CN10 pin 34 | DEG_A LED | PC4 / CN10 pin 34 |
| 8 | DEG_B LED | PC5 / CN10 pin 6 | DEG_B LED | PC5 / CN10 pin 6 |
| 9 | SRV (PWM) | PA1 / Arduino A1 | PWM_SET | PC6 / CN10 pin 4 |
| 10 | FL LED | PC10 / CN7 pin 1 | PWM_OUT | PA1 / Arduino A1 |
| 11 | FR LED | PC12 / CN7 pin 3 | DIR_A | PC8 / CN10 pin 2 |
| 12 | RELAY_TX (after successful can_send return) | PC9 / CN10 pin 1 | RL LED | PC10 / CN7 pin 1 |
| 13 | ERR (scope) | PC14 / CN7 pin 25 | RR LED | PC12 / CN7 pin 3 |
| extra | — | — | RELAY_TX (optional) | PC9 / CN10 pin 1 |
| extra | Selected task envelope | PC11 / CN7 pin 2 | Selected task envelope | PC11 / CN7 pin 2 |

## Lab 5 additions

Five new outputs per Nucleo, identical on N2 and N3. They are free of every pin
the overlay already claims, and PB12..PB15 are available because the overlay
disables SPI2 (the silkscreen labels them SPI2 NSS/SCK/MISO/MOSI).

| Test row | Signal | MCU pin | Connector | Purpose |
|---|---|---|---|---|
| 14 | LEADER LED | PB12 | CN10 | On while this node is the Raft leader |
| 15 | SYNC LED | PB13 | CN10 | On while the clock is synchronized to a master |
| 16 | REF_RX | PB14 | CN10 | Toggles on each received REF frame |
| 17 | HZ | PB15 | CN10 | Hazard phase marker, scope against FL and RL |
| 18 | PPS | PA0 | CN7, Arduino A0 | One rising edge per whole global second |

Confirm each printed connector pin number against `nucleo_f401re_morpho.png` and
the board silkscreen before wiring; the numbers are not inferable by counting.

PA0 is TIM5 channel 1. TIM5 is the second 32-bit timer on the F401 and TIM2 is
already taken by the PWM output on PA1, so the P7 hardware-timer compare that
drives PPS has a 32-bit counter to work with. Do not reassign PA0 to anything
else without revisiting that decision (TASKS.md D21).

LEADER, SYNC and HZ want an LED with its own series resistor like the existing
ones. REF_RX and PPS are scope points; an LED on REF_RX is harmless but will
only look dim, since it toggles at 25 Hz once REF runs at 20 ms.

On the Pi the same five signals are software state; only PPS needs a physical
pin, and which one is still open (TASKS.md D22).

PC0/PC1 are reliably identified on Arduino A5/A4; their Morpho routing can depend
on solder bridges (as noted in Zephyr's board connector map). PA1 uses TIM2 CH2,
not PA5. SPI stays on PA5/PA6/PA7; no output function takes these pins. I2C3 is
disabled to free PC9. PC13 remains the built-in blue USER button.

| Pi test row | Signal | BCM GPIO | Physical 40-pin header pin |
|---|---|---:|---:|
| 1 | GND | — | 6 |
| 2 | GND | — | 9 |
| 3 | UDP_RX | 23 | 16 |
| 4 | CMD_TX | 22 | 15 |
| 5 | CAN_TX_A | 17 | 11 |
| 6 | CAN_TX_B | 27 | 13 |
| 7 | DEG_A LED | 5 | 29 |
| 8 | DEG_B LED | 6 | 31 |

Pi GPIO24 (physical 18) remains CAN B INT; GPIO25 (physical 22) remains CAN A INT.
CMD_TX is deliberately GPIO22 physical15, **not GPIO24** from the historical UART
forwarder. BCM numbers and physical pin numbers are different numbering systems.

SPI/CAN connections remain:

| Function | Module pin | Nucleo | Pi physical pin (BCM) |
|---|---|---|---|
| Supply | 3V3 | 3V3 | 1 (3.3V) |
| Ground | GND | GND | 6 (GND) |
| SCK | D8 | PA5 / D13 | 23 (GPIO11) |
| MOSI | D10 | PA7 / D11 | 19 (GPIO10) |
| MISO | D9 | PA6 / D12 | 21 (GPIO9) |
| A CS | D7 | PB6 / D10 | 24 (GPIO8 CE0) |
| A INT | D6 | PA10 / D2 | 22 (GPIO25) |
| B CS | D7 | PC7 / D9 | 26 (GPIO7 CE1) |
| B INT | D6 | PB5 / D4 | 18 (GPIO24) |

D9/D10 in the module column follow the actual module signal mapping; the handout
has MOSI/MISO reversed. The module D10 is unrelated to Arduino D10 (CAN A CS).
