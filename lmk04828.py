#!/usr/bin/env python3
"""
Linux/PyFtdi driver for the TI LMK04828 on the ADS54J40 EVM.

The FT245 GPIO connections are:

    D0 -> LMK SCK
    D1 <-> LMK SDIO
    D4 -> LMK CS
    D6 <- LMK SDO / RESET/GPO

The driver supports:

    - LMK04828 register reads
    - LMK04828 register writes
    - 3-wire SPI readback
    - 4-wire SPI readback
    - switching between 3-wire and 4-wire readback
    - loading TICS Pro-style register configuration files
    - optional register verification
    - hardware reset via the LMK RESET/GPO pin

SPI:
    - SPI mode 0
    - MSB first
    - conservative ~5 kHz clock
    - LMK register transactions use 16-bit address/command
      followed by 8-bit register data/readback
"""

import re
import time

from pyftdi.gpio import GpioAsyncController


class LMK04828:
    """Simple LMK04828 register interface using the ADS54J40 EVM FT245."""

    # ------------------------------------------------------------------
    # FT245 GPIO assignments
    # ------------------------------------------------------------------

    SCK = 0x01       # D0 -> LMK SCK
    SDIO = 0x02      # D1 <-> LMK SDIO
    CS = 0x10        # D4 -> LMK CS
    SDO = 0x40       # D6 <- LMK SDO / RESET/GPO

    FTDI_URL = "ftdi://ftdi:232r/1"

    # D6 must remain an input during normal operation.
    OUTPUT_PINS = SCK | SDIO | CS

    # ------------------------------------------------------------------
    # SPI timing
    # ------------------------------------------------------------------

    # Experimentally working timing:
    # 100 us half-period -> 5 kHz SPI clock.
    HALF_PERIOD = 100e-6

    # ------------------------------------------------------------------
    # LMK register definitions
    # ------------------------------------------------------------------

    R0 = 0x000
    RESET_MUX_REGISTER = 0x14A

    # R0[4]
    SPI_3WIRE_DIS = 0x10

    # R0x14A
    RESET_MUX_MASK = 0x38
    RESET_TYPE_MASK = 0x07

    # RESET_MUX = 6 -> SPI readback
    # RESET_TYPE = 3 -> push-pull output
    SPI_READBACK_4WIRE = 0x33

    # RESET_MUX = 6 -> SPI readback
    # RESET_TYPE = 2 -> input with pulldown
    #
    # Used when we need to access the physical RESET pin without
    # the LMK actively driving it.
    SPI_READBACK_INPUT = 0x32

    def __init__(
        self,
        url=FTDI_URL,
        initial_mode="3wire",
    ):
        """
        Open the FTDI interface.

        Parameters
        ----------
        url : str
            PyFtdi device URL.

        initial_mode : {"3wire", "4wire"}
            Readback mode the driver assumes the LMK is currently in.

            This does NOT configure the LMK. It only tells the driver
            which physical readback path to use.
        """

        if initial_mode not in ("3wire", "4wire"):
            raise ValueError(
                "initial_mode must be '3wire' or '4wire'"
            )

        self.gpio = GpioAsyncController()

        self.gpio.configure(
            url,
            direction=self.OUTPUT_PINS,
            frequency=100_000,
            initial=self.CS,
        )

        # D6/SDO is intentionally left as an input.
        self.spi_3wire = initial_mode == "3wire"

    # ==================================================================
    # Low-level SPI
    # ==================================================================

    def _clock_write(self, bit):
        """Write one SPI bit using SPI mode 0."""

        value = self.SDIO if bit else 0

        # Data valid before rising edge.
        self.gpio.write(value)
        time.sleep(self.HALF_PERIOD)

        # Rising edge.
        self.gpio.write(value | self.SCK)
        time.sleep(self.HALF_PERIOD)

        # Falling edge.
        self.gpio.write(value)

    def _clock_read_sdio(self):
        """
        Read one bit from SDIO in 3-wire mode.

        The bit is sampled while SCK is high, after the rising
        edge and before the falling edge.

        This timing has been experimentally verified on the EVM.
        """

        # Low phase.
        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        # Rising edge.
        self.gpio.write(self.SCK)
        time.sleep(self.HALF_PERIOD)

        # Sample while SCK is high.
        pins = self.gpio.read(peek=True)
        bit = 1 if pins & self.SDIO else 0

        # Falling edge.
        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        return bit

    def _clock_read_sdo(self):
        """
        Read one bit from SDO in 4-wire mode.

        SDO is connected to FT245 D6 and is always configured
        as an FTDI input.
        """

        # Low phase.
        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        # Rising edge.
        self.gpio.write(self.SCK)
        time.sleep(self.HALF_PERIOD)

        # Sample while SCK is high.
        pins = self.gpio.read(peek=True)
        bit = 1 if pins & self.SDO else 0

        return bit

    # ------------------------------------------------------------------

    def _write_register(self, address, value):
        """Perform one LMK 24-clock register write."""

        command = address

        # CS low.
        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        # 16-bit command/address.
        for i in range(15, -1, -1):
            self._clock_write((command >> i) & 1)

        # 8-bit register data.
        for i in range(7, -1, -1):
            self._clock_write((value >> i) & 1)

        # CS high.
        self.gpio.write(self.CS)
        time.sleep(self.HALF_PERIOD)

    # ------------------------------------------------------------------

    def _read_register_3wire(self, address):
        """Read one register using 3-wire bidirectional SDIO."""

        command = (1 << 15) | address

        # CS low.
        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        # Send 16-bit read command.
        for i in range(15, -1, -1):
            self._clock_write((command >> i) & 1)

        # Release SDIO so the LMK can drive it.
        self.gpio.set_direction(self.SDIO, 0x00)

        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        # Read 8 bits.
        data = 0

        for _ in range(8):
            data = (data << 1) | self._clock_read_sdio()

        # Return SDIO to output.
        self.gpio.set_direction(self.SDIO, self.SDIO)

        # CS high.
        self.gpio.write(self.CS)

        return data

    # ------------------------------------------------------------------

    def _read_register_4wire(self, address):
        """Read one register using the dedicated SDO readback pin."""

        command = (1 << 15) | address

        # CS low.
        self.gpio.write(0)
        time.sleep(self.HALF_PERIOD)

        # Send 16-bit read command on SDIO.
        for i in range(15, -1, -1):
            self._clock_write((command >> i) & 1)

        # D6 is already an input.
        data = 0

        for _ in range(8):
            data = (data << 1) | self._clock_read_sdo()

            # _clock_read_sdo leaves SCK high.
            self.gpio.write(0)
            time.sleep(self.HALF_PERIOD)

        # CS high.
        self.gpio.write(self.CS)

        return data

    # ==================================================================
    # Public register interface
    # ==================================================================

    def read_register(self, address):
        """
        Read an LMK04828 register using the currently selected mode.
        """

        self._validate_address(address)

        if self.spi_3wire:
            return self._read_register_3wire(address)

        return self._read_register_4wire(address)

    def write_register(self, address, value):
        """
        Write an LMK04828 register.

        Writes are identical in 3-wire and 4-wire mode.

        The Python-side SPI mode is automatically updated when R0
        is written.
        """

        self._validate_address(address)
        self._validate_value(value)

        self._write_register(address, value)

        # R0[4] controls 3-wire SPI disable.
        if address == self.R0:
            self.spi_3wire = not bool(value & self.SPI_3WIRE_DIS)

    # ==================================================================
    # SPI mode control
    # ==================================================================

    def enable_4wire_readback(self):
        """
        Configure the LMK for 4-wire SPI readback.

        Sequence:

            R0    = 0x10
                disable 3-wire SPI

            R14A  = 0x33
                RESET_MUX  = SPI readback
                RESET_TYPE = push-pull output

        After this call, LMK SDO is available on FT245 D6.
        """

        self.write_register(self.R0, self.SPI_3WIRE_DIS)
        self.write_register(
            self.RESET_MUX_REGISTER,
            self.SPI_READBACK_4WIRE,
        )

        self.spi_3wire = False

    def enable_3wire_readback(self):
        """
        Return the LMK to 3-wire SPI readback.

        This clears R0[4], which enables bidirectional SDIO.

        The RESET/GPO pin is returned to its normal input state first,
        so the physical D6 connection is not driven by the LMK.
        """

        # If currently in 4-wire mode, stop the LMK from actively
        # driving the physical RESET/GPO pin.
        if not self.spi_3wire:
            self.write_register(
                self.RESET_MUX_REGISTER,
                self.SPI_READBACK_INPUT,
            )

        # Clear SPI_3WIRE_DIS.
        self.write_register(self.R0, 0x00)

        self.spi_3wire = True

    # ==================================================================
    # Configuration files
    # ==================================================================

    @staticmethod
    def parse_config(filename):
        """
        Parse a TICS Pro-style LMK register configuration.

        Example:

            R0 (INIT)   0x000090
            R0          0x000010
            R2          0x000200
            R3          0x000306

        TICS format:

            bits 23:8 = register address
            bits  7:0 = register value

        Returns
        -------
        list of tuple
            [(address, value), ...]
        """

        registers = []

        pattern = re.compile(
            r"^\s*R(\d+)"
            r"(?:\s*\([^)]*\))?"
            r"\s+(0x[0-9A-Fa-f]+)"
        )

        with open(filename, "r", encoding="utf-8") as file:

            for line_number, line in enumerate(file, start=1):

                match = pattern.match(line)

                if match is None:
                    continue

                address_from_name = int(match.group(1))
                word = int(match.group(2), 16)

                address = (word >> 8) & 0x1FFF
                value = word & 0xFF

                if address != address_from_name:
                    raise ValueError(
                        f"{filename}:{line_number}: "
                        f"register name R{address_from_name} "
                        f"does not match address "
                        f"0x{address:03X}"
                    )

                registers.append((address, value))

        if not registers:
            raise ValueError(
                f"No LMK registers found in configuration file: "
                f"{filename}"
            )

        return registers

    def load_config(self, filename, verify=False):
        """
        Load a TICS Pro-style configuration file.

        Parameters
        ----------
        filename : str
            TICS Pro configuration file.

        verify : bool
            Read every register back after writing.

        Notes
        -----
        Verification must use the correct physical readback mode.

        In particular, once R0[4] is set, 3-wire readback is disabled.
        """

        registers = self.parse_config(filename)

        total = len(registers)

        for index, (address, value) in enumerate(
            registers,
            start=1,
        ):

            print(
                f"[{index:4d}/{total}] "
                f"R0x{address:03X} <- 0x{value:02X}"
            )

            self.write_register(address, value)

            if verify:

                readback = self.read_register(address)

                if readback != value:
                    raise RuntimeError(
                        f"VERIFY FAILED: "
                        f"R0x{address:03X}: "
                        f"read 0x{readback:02X}, "
                        f"expected 0x{value:02X}"
                    )

        print("Configuration successfully written.")

    # ==================================================================
    # Hardware reset
    # ==================================================================

    def reset(self, duration=0.01):
        """
        Hardware-reset the LMK using pin 5.

        Important:
            LMK pin 5 is also used as the 4-wire SDO output.

        Therefore hardware reset is only allowed while the LMK is
        currently in 3-wire mode. In that state pin 5 is not being
        actively driven by the LMK.

        RESET:
            HIGH = reset
            LOW  = normal operation
        """

        if not self.spi_3wire:
            raise RuntimeError(
                "Hardware reset is only allowed in 3-wire mode. "
                "Switch back to 3-wire readback first."
            )

        # D6 is normally an input. Temporarily make it an output.
        self.gpio.set_direction(self.SDO, self.SDO)

        try:
            # Assert reset: CS high, RESET high.
            self.gpio.write(self.CS | self.SDO)
            time.sleep(duration)

            # Release reset.
            self.gpio.write(self.CS)
            time.sleep(duration)

        finally:
            # D6 must always return to input.
            self.gpio.set_direction(self.SDO, 0x00)

        # Hardware reset returns the LMK to its reset state.
        self.spi_3wire = True

    # ==================================================================
    # Validation / utility
    # ==================================================================

    @staticmethod
    def _validate_address(address):
        if not 0 <= address <= 0x1FFF:
            raise ValueError(
                f"Register address must be 0x000..0x1FFF, "
                f"got 0x{address:X}"
            )

    @staticmethod
    def _validate_value(value):
        if not 0 <= value <= 0xFF:
            raise ValueError(
                f"Register value must be 0x00..0xFF, "
                f"got 0x{value:X}"
            )

    @property
    def spi_mode(self):
        """Return the current readback mode as a human-readable string."""

        return "3-wire" if self.spi_3wire else "4-wire"

    def close(self):
        """Close the FTDI interface."""

        self.gpio.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()