"""
Bit-banged SPI driver for the TI ADS54J40 on the ADS54J40EVM.

SPI frame (datasheet section 8.4.2, Figure 8-12), 24 clocks, MSB first:

    bit 15      R/W   0 = write, 1 = read
    bit 14      M     0 = analog bank, 1 = JESD bank
    bit 13      P     JESD bank only: 0 = page-select access, 1 = register access
    bit 12      CH    JESD bank only: 0 = channel A, 1 = channel B
    bits 11:0   register address
    bits 7:0    data (write) / don't care (read, data appears on SDOUT)

SDIN is latched on the rising SCLK edge; SDOUT changes on the falling edge.

The register space is paged (datasheet Figure 8-27). Callers only name a
page and an address; this driver writes the page-select registers in the
same SEN-low cycle before the actual access. Example:

    with ADS54J40() as adc:
        value = adc.read_register("analog_master", 0x59)
        adc.write_register("jesd_analog", 0x16, 0x02, channel=1)
"""

from dataclasses import dataclass
from typing import Optional

from ti_adcevm.ftdi_gpio import (
    ADC_SDO,
    ADC_SEN,
    DEFAULT_URL,
    LMK_CS,
    SCK,
    SDIO,
    SyncGpioController,
)


@dataclass(frozen=True)
class Page:
    """One user-visible register page."""

    m: int                 # M bit: 0 = analog bank, 1 = JESD bank
    p: int                 # P bit used for the register access itself
    addresses: range       # valid register addresses on this page
    analog_page: Optional[int] = None   # value for 0-011h (analog bank)
    jesd_page: Optional[int] = None     # value for 4-003h/4-004h
    jesd_page1: int = 0x0000            # value for 4-001h/4-002h


class ADS54J40:
    """Register-level interface to the ADS54J40 via the EVM's FT245."""

    # Registers that are not behind a page select are exposed as the
    # "analog_general" (0-000h, 0-011h) and "jesd_general" (4-001h..4-005h)
    # pages, mainly for debugging.
    PAGES = {
        "analog_general": Page(m=0, p=0, addresses=range(0x000, 0x012)),
        "analog_master":  Page(m=0, p=0, addresses=range(0x020, 0x05A), analog_page=0x80),
        "analog_adc":     Page(m=0, p=0, addresses=range(0x05F, 0x060), analog_page=0x0F),
        "jesd_general":   Page(m=1, p=0, addresses=range(0x001, 0x006)),
        "jesd_main":      Page(m=1, p=1, addresses=range(0x000, 0x0F8), jesd_page=0x6800),
        "jesd_digital":   Page(m=1, p=1, addresses=range(0x000, 0x033), jesd_page=0x6900),
        "jesd_analog":    Page(m=1, p=1, addresses=range(0x012, 0x01C), jesd_page=0x6A00),
        "offset_read":    Page(m=1, p=1, addresses=range(0x068, 0x07C), jesd_page=0x6100, jesd_page1=0x0000),
        "offset_load":    Page(m=1, p=1, addresses=range(0x000, 0x079), jesd_page=0x6100, jesd_page1=0x0500),
    }

    # General registers
    REG_RESET = 0x000           # analog bank, bits 7 and 0: software reset
    REG_ANALOG_PAGE = 0x011     # analog bank page select
    REG_JESD_PAGE1_LO = 0x001   # JESD bank, JESD BANK PAGE SEL1[7:0]
    REG_JESD_PAGE1_HI = 0x002   # JESD bank, JESD BANK PAGE SEL1[15:8]
    REG_JESD_PAGE_LO = 0x003    # JESD bank, JESD BANK PAGE SEL[7:0]
    REG_JESD_PAGE_HI = 0x004    # JESD bank, JESD BANK PAGE SEL[15:8]
    REG_BROADCAST = 0x005       # JESD bank, bit 0: DISABLE BROADCAST

    # Pins this driver drives. LMK CS is driven high so the LMK04828,
    # which shares SCK/SDIO, ignores ADC traffic.
    OUTPUT_PINS = SCK | SDIO | ADC_SEN | LMK_CS
    IDLE = ADC_SEN | LMK_CS     # SEN high, LMK deselected, SCLK low

    # Duration of one GPIO sample. One SCLK period takes two samples,
    # so 500 ns gives ~1 MHz SCLK (datasheet max 2 MHz, setup/hold 100 ns).
    HALF_PERIOD = 500e-9

    def __init__(self, url=DEFAULT_URL, *, half_period=HALF_PERIOD, verbose=0):
        """
        Open the FTDI interface. Does not touch the ADC.

        Call initialize() once after power-up (datasheet Table 9-1)
        before configuring the device.

        verbose: 0 = quiet, 1 = log every SPI frame,
                 2 = additionally dump every sampled SDOUT bit.
        """

        if half_period <= 0:
            raise ValueError("half_period must be positive")

        self.verbose = verbose
        self.gpio = SyncGpioController()
        self.gpio.configure(
            url,
            direction=self.OUTPUT_PINS,
            frequency=1.0 / half_period,
            initial=self.IDLE,
        )

    # ==================================================================
    # Waveform level
    # ==================================================================
    #
    # In FTDI synchronous bit-bang mode, every output byte produces one
    # input byte, and that input byte is the pin state sampled just
    # *before* the output byte is applied. inputs[k] therefore shows the
    # pins as set by outputs[k - 1].

    def _exchange(self, outputs):
        """Clock out a GPIO waveform and return the sampled inputs."""

        outputs = bytes(outputs)
        inputs = self.gpio.exchange(outputs)

        if len(inputs) != len(outputs):
            raise RuntimeError(
                f"FTDI exchange length mismatch: sent {len(outputs)} bytes, "
                f"received {len(inputs)} bytes"
            )
        return inputs

    def _begin(self):
        """Pull SEN low and wait a few samples (tSLOADS >= 100 ns)."""

        self._exchange([LMK_CS] * 3)

    def _end(self):
        """Hold SCLK low for a couple of samples (tSLOADH), then release SEN."""

        self._exchange([LMK_CS, LMK_CS, self.IDLE])

    def _send_bits(self, value, nbits):
        """
        Shift out nbits of value, MSB first, and end with SCLK low.

        Each bit is two samples: SCLK low with data valid, then SCLK high
        (the ADC latches SDIN on this rising edge).
        """

        outputs = bytearray()
        for i in range(nbits - 1, -1, -1):
            data = LMK_CS | (SDIO if (value >> i) & 1 else 0)
            outputs += bytes([data, data | SCK])
        outputs.append(LMK_CS)

        self._exchange(outputs)

    def _read_bits(self, nbits):
        """
        Clock in nbits from SDOUT, MSB first.

        The ADC updates SDOUT on each falling SCLK edge (the first bit
        after the falling edge that ends the header), so it is stable
        while SCLK is high. Bit i is in inputs[2*i + 1], which is the pin
        state while SCLK is high during clock i.
        """

        outputs = bytes([LMK_CS | SCK, LMK_CS] * nbits)
        inputs = self._exchange(outputs)

        value = 0
        for i in range(nbits):
            sample = inputs[2 * i + 1]
            bit = 1 if sample & ADC_SDO else 0
            value = (value << 1) | bit

            if self.verbose >= 2:
                print(
                    f"  SDO bit {nbits - 1 - i}: "
                    f"pins=0x{sample:02X} -> {bit}"
                )

        return value

    # ==================================================================
    # SPI frames (SEN must already be low)
    # ==================================================================

    @staticmethod
    def _make_header(read, m, p, channel, address):
        """Build the 16-bit R/W, M, P, CH, A[11:0] header."""

        for name, bit in (("read", read), ("m", m), ("p", p), ("channel", channel)):
            if bit not in (0, 1):
                raise ValueError(f"{name} must be 0 or 1, got {bit!r}")

        if not 0 <= address <= 0xFFF:
            raise ValueError(f"Address must be 12-bit, got 0x{address:X}")

        return (read << 15) | (m << 14) | (p << 13) | (channel << 12) | address

    def _write_frame(self, address, value, *, m=0, p=0, channel=0):
        """One 24-clock register write."""

        _check_byte(value)
        header = self._make_header(0, m, p, channel, address)

        if self.verbose:
            print(
                f"  SPI WRITE M={m} P={p} CH={channel} "
                f"ADDR=0x{address:03X} DATA=0x{value:02X}"
            )

        self._send_bits((header << 8) | value, 24)

    def _read_frame(self, address, *, m=0, p=0, channel=0):
        """One 16-clock read header followed by 8 read clocks."""

        header = self._make_header(1, m, p, channel, address)

        self._send_bits(header, 16)
        value = self._read_bits(8)

        if self.verbose:
            print(
                f"  SPI READ  M={m} P={p} CH={channel} "
                f"ADDR=0x{address:03X} DATA=0x{value:02X}"
            )

        return value

    def _select_page(self, page):
        """Write the page-select registers for page (SEN already low)."""

        if page.analog_page is not None:
            self._write_frame(self.REG_ANALOG_PAGE, page.analog_page)

        if page.jesd_page is not None:
            # Datasheet Table 9-3 order. 4-005h = 01h disables broadcast so
            # the CH bit addresses channel A and B individually.
            self._write_frame(self.REG_BROADCAST, 0x01, m=1)
            self._write_frame(self.REG_JESD_PAGE_HI, page.jesd_page >> 8, m=1)
            self._write_frame(self.REG_JESD_PAGE_LO, page.jesd_page & 0xFF, m=1)
            self._write_frame(self.REG_JESD_PAGE1_HI, page.jesd_page1 >> 8, m=1)
            self._write_frame(self.REG_JESD_PAGE1_LO, page.jesd_page1 & 0xFF, m=1)

    def _get_page(self, page_name, address):
        """Look up a page by name and check that address belongs to it."""

        try:
            page = self.PAGES[page_name]
        except KeyError:
            raise ValueError(
                f"Unknown page '{page_name}'. "
                f"Available pages: {', '.join(self.PAGES)}"
            ) from None

        if address not in page.addresses:
            raise ValueError(
                f"Address 0x{address:03X} is outside page '{page_name}' "
                f"(0x{page.addresses.start:03X}..0x{page.addresses.stop - 1:03X})"
            )

        return page

    # ==================================================================
    # Public register interface
    # ==================================================================

    def read_register(self, page, address, *, channel=0):
        """Read one register. channel (0 = A, 1 = B) only matters for JESD pages."""

        spec = self._get_page(page, address)

        self._begin()
        try:
            self._select_page(spec)
            value = self._read_frame(address, m=spec.m, p=spec.p, channel=channel)
        finally:
            self._end()

        return value

    def write_register(self, page, address, value, *, channel=0):
        """Write one register. channel (0 = A, 1 = B) only matters for JESD pages."""

        spec = self._get_page(page, address)
        _check_byte(value)

        self._begin()
        try:
            self._select_page(spec)
            self._write_frame(address, value, m=spec.m, p=spec.p, channel=channel)
        finally:
            self._end()

    def read_many(self, registers):
        """
        Read several registers.

        registers: iterable of (page, address) or (page, address, channel).
        Returns a list of dicts with keys page, address, channel, value.
        """

        results = []
        for entry in registers:
            page, address, channel = (*entry, 0) if len(entry) == 2 else entry
            value = self.read_register(page, address, channel=channel)
            results.append(
                {"page": page, "address": address, "channel": channel, "value": value}
            )
        return results

    def write_many(self, registers, *, verify=False):
        """
        Write several registers.

        registers: iterable of (page, address, value) or
                   (page, address, value, channel).
        With verify=True every register is read back and a RuntimeError
        is raised on the first mismatch.
        """

        for entry in registers:
            page, address, value, channel = (*entry, 0) if len(entry) == 3 else entry
            self.write_register(page, address, value, channel=channel)

            if verify:
                readback = self.read_register(page, address, channel=channel)
                if readback != value:
                    raise RuntimeError(
                        f"Readback mismatch: {page} 0x{address:03X} CH={channel}: "
                        f"wrote 0x{value:02X}, read 0x{readback:02X}"
                    )

    # ==================================================================
    # Reset / start-up
    # ==================================================================

    def reset(self):
        """
        Software reset of the analog bank (0-000h = 81h, self-clearing).

        This does not reset the JESD bank; use initialize() for that.
        """

        self._begin()
        try:
            self._write_frame(self.REG_RESET, 0x81)
        finally:
            self._end()

    def initialize(self):
        """
        Reset sequence from datasheet Table 9-1, steps 2 and 3.

        Afterwards the device is in its default state (LMFS = 8224, no
        decimation). Any previous configuration is lost.
        """

        if self.verbose:
            print("ADS54J40: initialization (datasheet Table 9-1)")

        self.reset()

        self._begin()
        try:
            # Re-enable broadcast (the power-on default) so the JESD bank
            # resets below hit both channels. This driver disables
            # broadcast on every JESD access, and the analog-bank reset
            # above does not restore it.
            self._write_frame(self.REG_BROADCAST, 0x00, m=1)

            # Clear the unused page-select registers, select main digital.
            self._write_frame(self.REG_JESD_PAGE1_LO, 0x00, m=1)
            self._write_frame(self.REG_JESD_PAGE1_HI, 0x00, m=1)
            self._write_frame(self.REG_JESD_PAGE_LO, 0x00, m=1)
            self._write_frame(self.REG_JESD_PAGE_HI, 0x68, m=1)

            # DIG RESET (self-clearing), then pulse PULSE RESET.
            self._write_frame(0x0F7, 0x01, m=1, p=1)
            self._write_frame(0x000, 0x01, m=1, p=1)
            self._write_frame(0x000, 0x00, m=1, p=1)

            # Master page: set the ALWAYS WRITE 1 bit in 0-059h.
            self._write_frame(self.REG_ANALOG_PAGE, 0x80)
            self._write_frame(0x059, 0x20)
        finally:
            self._end()

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


def _check_byte(value):
    if not 0 <= value <= 0xFF:
        raise ValueError(f"Value must be 8-bit, got 0x{value:X}")
