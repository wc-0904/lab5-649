# Pi 4B: Lab 3 Part 0

Run these commands on the Pi from this repository's root. The kernel driver handles
SPI and interrupts; no custom SPI program or python-can dependency is needed.
Do not run the old `pi/proxy_receiver` (GPIO24 is now CAN B INT). It was removed
from this directory during the Lab 5 cleanup and remains in `lab3-649/`.

1. Power off before wiring both modules according to ../../WIRING.md.
   Leave all CANH/CANL terminals disconnected. Boot the Pi.
2. Record the environment and install tools:

   ```sh
   cat /etc/os-release
   uname -r
   sudo apt update
   sudo apt install can-utils iproute2
   ```

3. Inspect `/boot/firmware/config.txt` (or `/boot/config.txt` on older Raspberry Pi OS).
   Back up that file, then append the block from `pi/part0/config.txt` once.
   Do not replace the whole boot configuration. Remove conflicting CAN/SPI0 overlays;
   each CE pin must belong to only one device. The `[all]` section ensures the block
   is not accidentally scoped to another Pi model. Both modules are 16 MHz and use
   a conservative 1 MHz maximum SPI clock.

   ```sh
   sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.before-lab3
   sudo nano /boot/firmware/config.txt
   sudo reboot
   ```

   On older OS versions, substitute `/boot/config.txt` in both commands.
   Reconnect over SSH after reboot.

4. Inspect, bring up, and verify both controllers:

   ```sh
   python3 pi/part0/can_check.py inspect
   sudo python3 pi/part0/can_check.py up
   python3 pi/part0/can_check.py check
   ```

   The script resolves CAN A by **spi0.0 / CE0**, CAN B by **spi0.1 / CE1**.
   Linux probe order can swap `can0` and `can1`; trust the printed physical mapping.
   Both interfaces must show `PASS`, `ERROR-ACTIVE`, and `bitrate 500000`.
   `state UNKNOWN` in the generic link header is normal for CAN; the CAN-specific
   state must be `ERROR-ACTIVE`. Re-run `up` after each reboot (no service installed).

No CAN frames are sent. Do not run `cansend` yet: a disconnected controller has no
other node to acknowledge its normal-mode transmissions. This checkpoint does not
prove external bus wiring, transceiver operation, or oscillator accuracy.

If interfaces are missing, inspect `sudo dmesg | tail -n 80`, both 3.3 V supplies,
CS wires, MOSI/MISO direction, and the correct boot config file. If an MCP2515 init
failed during boot, fix wiring with power off and reboot. An idle state check alone
cannot prove the interrupt wire works; the later bus receive test is still required.

Configuration parameters follow the Raspberry Pi kernel overlay documentation:
https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/README
