"""
Bit-banged SPI driver for the TI LMK04828 on the ADS54J40EVM.

SPI frame (datasheet section 9.5), 24 clocks, MSB first, SPI mode 0:

    bit 23      R/W   0 = write, 1 = read
    bits 22:21  W1 W0 (always 0)
    bits 20:8   register address A12..A0
    bits 7:0    data (write) / readback (read)

SDIO is latched on the rising SCK edge; readback data changes on the
falling edge.

Readback modes:

    3-wire (power-on default): the LMK drives the readback data onto SDIO.
    4-wire: R0[4] SPI_3WIRE_DIS = 1 and the RESET/GPO pin configured as
            SPI readback output (0x14A). On the EVM that pin is wired to
            FT245 D6, which is also how the driver issues a hardware reset.

The driver cannot ask the LMK which mode it is in, so it tracks the mode
itself: pass initial_mode to match the chip, and any write to R0 updates
the tracked mode automatically.
"""

import re
import time

from pyftdi.gpio import GpioAsyncController

from ti_adcevm.ftdi_gpio import ADC_SEN, DEFAULT_URL, LMK_CS, LMK_SDO, SCK, SDIO


class LMK04828:
    """Register-level interface to the LMK04828 via the EVM's FT245."""

    # Pins this driver drives. ADC SEN is driven high so the ADS54J40,
    # which shares SCK/SDIO, ignores LMK traffic. LMK_SDO (D6) stays an
    # input except during a hardware reset.
    OUTPUT_PINS = SCK | SDIO | LMK_CS | ADC_SEN
    IDLE = LMK_CS | ADC_SEN       # LMK deselected, ADC deselected
    ACTIVE = ADC_SEN              # LMK selected (CS low)

    # Wait after each GPIO change. Every write is a separate USB
    # transaction, which already dominates the timing; this is the
    # experimentally working value.
    HALF_PERIOD = 10e-6

    # Register 0x000
    R0 = 0x000
    RESET_BIT = 0x80
    SPI_3WIRE_DIS = 0x10

    # Register 0x14A: RESET_MUX[5:3], RESET_TYPE[2:0]
    RESET_PIN_CONFIG = 0x14A
    RESET_PIN_SPI_READBACK = 0x33   # MUX = 6 (SPI readback), TYPE = 3 (push-pull out)
    RESET_PIN_INPUT = 0x32          # MUX = 6, TYPE = 2 (input with pull-down)

    # Registers whose readback is not expected to equal what was written:
    # R0 (RESET self-clears), status/readback registers.
    NO_VERIFY = {0x000, 0x182, 0x183, 0x184, 0x185, 0x188}

    MAX_ADDRESS = 0x1FFF

    def __init__(self, url=DEFAULT_URL, initial_mode="3wire"):
        """
        Open the FTDI interface. Does not touch the LMK.

        initial_mode: "3wire" or "4wire", the readback mode the LMK is
        currently in. This only selects which pin the driver reads from;
        use enable_3wire_readback()/enable_4wire_readback() to change
        the chip's mode.
        """

        if initial_mode not in ("3wire", "4wire"):
            raise ValueError("initial_mode must be '3wire' or '4wire'")

        self.spi_3wire = initial_mode == "3wire"

        self.gpio = GpioAsyncController()
        self.gpio.configure(
            url,
            direction=self.OUTPUT_PINS,
            frequency=100_000,
            initial=self.IDLE,
        )

    @property
    def spi_mode(self):
        """Tracked readback mode as a string, "3-wire" or "4-wire"."""

        return "3-wire" if self.spi_3wire else "4-wire"

    # ==================================================================
    # Bit level
    # ==================================================================

    def _set(self, pins):
        """Drive the output pins and wait half an SPI clock period."""

        self.gpio.write(pins)
        time.sleep(self.HALF_PERIOD)

    def _send_bits(self, value, nbits):
        """Shift out nbits of value on SDIO, MSB first. Ends with SCK low."""

        for i in range(nbits - 1, -1, -1):
            data = self.ACTIVE | (SDIO if (value >> i) & 1 else 0)
            self._set(data)
            self._set(data | SCK)
        self._set(self.ACTIVE)

    def _read_bits(self, nbits, pin):
        """
        Clock in nbits from pin (SDIO or LMK_SDO), MSB first.

        The LMK changes readback data on the falling SCK edge, so each
        bit is sampled while SCK is high.
        """

        value = 0
        for _ in range(nbits):
            self._set(self.ACTIVE | SCK)
            bit = 1 if self.gpio.read(peek=True) & pin else 0
            value = (value << 1) | bit
            self._set(self.ACTIVE)
        return value

    # ==================================================================
    # Public register interface
    # ==================================================================

    def read_register(self, address):
        """Read one register using the currently tracked readback mode."""

        _check_address(address)
        command = (1 << 15) | address

        self._set(self.ACTIVE)
        try:
            self._send_bits(command, 16)

            if self.spi_3wire:
                # Release SDIO so the LMK can drive it.
                self.gpio.set_direction(SDIO, 0)
                try:
                    value = self._read_bits(8, SDIO)
                finally:
                    self.gpio.set_direction(SDIO, SDIO)
            else:
                value = self._read_bits(8, LMK_SDO)
        finally:
            self._set(self.IDLE)

        return value

    def write_register(self, address, value):
        """
        Write one register.

        Writing R0 also updates the tracked readback mode from R0[4].
        """

        _check_address(address)
        if not 0 <= value <= 0xFF:
            raise ValueError(f"Register value must be 0x00..0xFF, got 0x{value:X}")

        self._set(self.ACTIVE)
        try:
            self._send_bits((address << 8) | value, 24)
        finally:
            self._set(self.IDLE)

        if address == self.R0:
            self.spi_3wire = not value & self.SPI_3WIRE_DIS

    # ==================================================================
    # Readback mode
    # ==================================================================

    def enable_4wire_readback(self):
        """Switch the LMK to 4-wire readback on the RESET/GPO pin (FT245 D6)."""

        self.write_register(self.R0, self.SPI_3WIRE_DIS)
        self.write_register(self.RESET_PIN_CONFIG, self.RESET_PIN_SPI_READBACK)

    def enable_3wire_readback(self):
        """
        Switch the LMK back to 3-wire readback on SDIO.

        The RESET/GPO pin is first turned back into an input so the LMK
        stops driving the line to FT245 D6.
        """

        if not self.spi_3wire:
            self.write_register(self.RESET_PIN_CONFIG, self.RESET_PIN_INPUT)
        self.write_register(self.R0, 0x00)

    def reset(self, duration=0.01):
        """
        Hardware reset by pulsing the RESET/GPO pin high.

        Only allowed in 3-wire mode: in 4-wire mode the LMK itself drives
        that pin, and FT245 D6 would fight it.
        """

        if not self.spi_3wire:
            raise RuntimeError(
                "Hardware reset is only allowed in 3-wire mode. "
                "Call enable_3wire_readback() first."
            )

        self.gpio.set_direction(LMK_SDO, LMK_SDO)
        try:
            self.gpio.write(self.IDLE | LMK_SDO)
            time.sleep(duration)
            self.gpio.write(self.IDLE)
            time.sleep(duration)
        finally:
            self.gpio.set_direction(LMK_SDO, 0)

        self.spi_3wire = True

    # ==================================================================
    # Status
    # ==================================================================

    def read_status(self):
        """
        Read and decode the status registers 0x182..0x188.

        Returns a dict with PLL lock / lock-lost flags, the selected
        reference input, CLKin LOS flags, holdover state, the PLL1 DAC
        value and the raw register values.
        """

        raw = {a: self.read_register(a) for a in (0x182, 0x183, 0x184, 0x185, 0x188)}
        r184 = raw[0x184]

        if r184 & 0x20:
            reference = "CLKin2"
        elif r184 & 0x10:
            reference = "CLKin1"
        elif r184 & 0x08:
            reference = "CLKin0"
        else:
            reference = None

        return {
            "pll1_locked": bool(raw[0x182] & 0x02),     # RB_PLL1_LD
            "pll1_lock_lost": bool(raw[0x182] & 0x04),  # RB_PLL1_LD_LOST
            "pll2_locked": bool(raw[0x183] & 0x02),     # RB_PLL2_LD
            "pll2_lock_lost": bool(raw[0x183] & 0x04),  # RB_PLL2_LD_LOST
            "reference": reference,                     # RB_CLKinX_SEL
            "clkin1_los": bool(r184 & 0x02),            # RB_CLKin1_LOS
            "clkin0_los": bool(r184 & 0x01),            # RB_CLKin0_LOS
            "holdover": bool(raw[0x188] & 0x10),        # RB_HOLDOVER
            "dac": ((r184 & 0xC0) << 2) | raw[0x185],   # RB_DAC_VALUE[9:0]
            "raw": raw,
        }

    def clear_pll_lock_lost(self):
        """Clear the PLL1/PLL2 lock-lost flags (write CLR_PLLx_LD_LOST 1, then 0)."""

        for address in (0x182, 0x183):
            self.write_register(address, 0x01)
            self.write_register(address, 0x00)

    # ==================================================================
    # Configuration files
    # ==================================================================

    # TICS Pro export:  "R0 (INIT)   0x000090"   word = address << 8 | data
    _TICS_LINE = re.compile(r"^\s*R(\d+)(?:\s*\([^)]*\))?\s+(0x[0-9A-Fa-f]+)")
    # EVM GUI export:   "0x146 0x10"
    _EVM_LINE = re.compile(r"^\s*(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)")

    @classmethod
    def parse_config(cls, filename):
        """
        Parse a TICS Pro or EVM GUI register export.

        Returns the writes in file order as [(address, value), ...].
        Lines that match neither format (headers, comments) are ignored.
        """

        registers = []

        with open(filename, encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                where = f"{filename}:{line_number}"

                if match := cls._TICS_LINE.match(line):
                    word = int(match.group(2), 16)
                    address, value = word >> 8, word & 0xFF
                    if address != int(match.group(1)):
                        raise ValueError(
                            f"{where}: register name R{match.group(1)} does not "
                            f"match address 0x{address:03X} in the data word"
                        )
                elif match := cls._EVM_LINE.match(line):
                    address = int(match.group(1), 16)
                    value = int(match.group(2), 16)
                    if value > 0xFF:
                        raise ValueError(f"{where}: value 0x{value:X} is larger than one byte")
                else:
                    continue

                if address > cls.MAX_ADDRESS:
                    raise ValueError(f"{where}: address 0x{address:X} is out of range")

                registers.append((address, value))

        if not registers:
            raise ValueError(f"No LMK registers found in {filename}")

        return registers

    def load_config(self, filename, verify=False):
        """
        Write every register from a configuration file, in file order.

        verify: after all writes, read back each register (last written
        value wins) and raise RuntimeError listing all mismatches.
        Registers in NO_VERIFY are skipped. Verification happens at the
        end because a config usually starts with a reset (R0 = 0x90)
        that switches to 4-wire mode before 0x14A enables 4-wire readback.
        """

        registers = self.parse_config(filename)
        total = len(registers)

        for index, (address, value) in enumerate(registers, start=1):
            print(f"[{index:4d}/{total}] R0x{address:03X} <- 0x{value:02X}")
            self.write_register(address, value)

        if verify:
            expected = {a: v for a, v in registers if a not in self.NO_VERIFY}
            mismatches = []

            for address, value in expected.items():
                readback = self.read_register(address)
                if readback != value:
                    mismatches.append(
                        f"R0x{address:03X}: read 0x{readback:02X}, expected 0x{value:02X}"
                    )

            if mismatches:
                raise RuntimeError(
                    f"Verify failed for {len(mismatches)} of {len(expected)} "
                    f"registers ({self.spi_mode} readback):\n  " + "\n  ".join(mismatches)
                )

            print(f"Verified {len(expected)} registers ({self.spi_mode} readback).")

        print("Configuration successfully written.")

    # ==================================================================
    # Cleanup
    # ==================================================================

    def close(self):
        """Close the FTDI interface."""

        self.gpio.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def _check_address(address):
    if not 0 <= address <= LMK04828.MAX_ADDRESS:
        raise ValueError(f"Register address must be 0x000..0x1FFF, got 0x{address:X}")
