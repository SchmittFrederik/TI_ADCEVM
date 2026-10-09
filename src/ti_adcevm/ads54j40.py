#!/usr/bin/env python3

from ti_adcevm.ftdi_gpio import ADSGpioSyncController


class ADS54J40:
    """
    Bit-banged SPI driver for the ADS54J40EVM.

    FT245 GPIO mapping:

        D0 -> SCLK
        D1 -> SDIO
        D2 <- SDO
        D3 -> SEN


    ADS54J40 SPI header:

        bit 15      R/W
        bit 14      M
        bit 13      P
        bit 12      CH
        bits 11:0  address


    Write:

        [R/W M P CH A11...A0] [D7...D0]

        16-bit header + 8-bit data = 24 clocks


    Read:

        [R/W M P CH A11...A0] [SDO D7...D0]

        16 command clocks + 8 read clocks


    User-facing register pages:

        analog_general
        analog_page_select
        analog_master
        analog_adc

        jesd_general
        jesd_page_select
        jesd_page_select1

        jesd_main
        jesd_digital
        jesd_analog

        offset_page_select
        offset_read
        offset_load


    Example:

        with ADS54J40() as adc:

            value = adc.read_register(
                "analog_master",
                0x20,
            )

            adc.write_register(
                "jesd_analog",
                0x16,
                0x02,
            )


    The class intentionally keeps the low-level SPI interface generic.
    ADS54J40-specific M/P/page-selection logic is handled here, so
    application code does not need to know about it.
    """

    # ==================================================================
    # FT245 GPIO pins
    # ==================================================================

    SCK = 0x01       # D0 -> SCLK
    SDIO = 0x02      # D1 -> SDIO
    SDO = 0x04       # D2 <- SDO
    SEN = 0x08       # D3 -> SEN

    OUTPUT_PINS = SCK | SDIO | SEN

    # SPI clock half-period in seconds.
    #
    # 5 ms half-period -> approximately 100 Hz SCLK.
    #
    # Deliberately slow during initial bring-up and well below the
    # ADS54J40 SPI maximum of 2 MHz.
    HALF_PERIOD = 500e-9
    # ==================================================================
    # ADS54J40 register/page structure
    # ==================================================================

    PAGES = {

        # --------------------------------------------------------------
        # Analog Bank, M=0, P=0
        # --------------------------------------------------------------

        "analog_general": {
            "bank": 0,
            "page_access": 0,
            "selector": None,
            "address_min": 0x000,
            "address_max": 0x000,
        },

        "analog_page_select": {
            "bank": 0,
            "page_access": 0,
            "selector": None,
            "address_min": 0x011,
            "address_max": 0x011,
        },

        "analog_master": {
            "bank": 0,
            "page_access": 0,
            "selector": ("analog", 0x80),
            "address_min": 0x020,
            "address_max": 0x059,
        },

        "analog_adc": {
            "bank": 0,
            "page_access": 0,
            "selector": ("analog", 0x0F),
            "address_min": 0x05F,
            "address_max": 0x05F,
        },

        # --------------------------------------------------------------
        # JESD Bank, M=1, P=0
        # --------------------------------------------------------------

        "jesd_general": {
            "bank": 1,
            "page_access": 0,
            "selector": None,
            "address_min": 0x005,
            "address_max": 0x005,
        },

        "jesd_page_select": {
            "bank": 1,
            "page_access": 0,
            "selector": None,
            "address_min": 0x003,
            "address_max": 0x004,
        },

        "jesd_page_select1": {
            "bank": 1,
            "page_access": 0,
            "selector": None,
            "address_min": 0x001,
            "address_max": 0x002,
        },

        # --------------------------------------------------------------
        # JESD pages, M=1, P=1
        # --------------------------------------------------------------

        "jesd_main": {
            "bank": 1,
            "page_access": 1,
            "selector": ("jesd", 0x6800),
            "address_min": 0x000,
            "address_max": 0x0F7,
        },

        "jesd_digital": {
            "bank": 1,
            "page_access": 1,
            "selector": ("jesd", 0x6900),
            "address_min": 0x000,
            "address_max": 0x032,
        },

        "jesd_analog": {
            "bank": 1,
            "page_access": 1,
            "selector": ("jesd", 0x6A00),
            "address_min": 0x012,
            "address_max": 0x01B,
        },

        # --------------------------------------------------------------
        # Offset pages
        # --------------------------------------------------------------

        "offset_page_select": {
            "bank": 1,
            "page_access": 0,
            "selector": None,
            "address_min": 0x001,
            "address_max": 0x002,
        },

        "offset_read": {
            "bank": 1,
            "page_access": 1,
            "selector": ("offset", 0x0000),
            "address_min": 0x068,
            "address_max": 0x07B,
        },

        "offset_load": {
            "bank": 1,
            "page_access": 1,
            "selector": ("offset", 0x0500),
            "address_min": 0x000,
            "address_max": 0x00D,
        },
    }

    # ==================================================================
    # Initialization
    # ==================================================================

    def __init__(
        self,
        url="ftdi://ftdi:232r/1",
        *,
        half_period=None,
        verbose=True,
    ):
        """
        Initialize the FT232R synchronous GPIO interface.
        """

        self.url = url
        self.verbose = verbose

        if half_period is None:
            self.half_period = self.HALF_PERIOD
        else:
            self.half_period = half_period

        if self.half_period <= 0:
            raise ValueError("half_period must be positive")

        # GpioSyncController outputs one GPIO sample per configured
        # frequency period. Two samples are used for each SPI clock
        # cycle: one with SCK low and one with SCK high.
        self.gpio_frequency = 1.0 / self.half_period

        self.gpio = ADSGpioSyncController()
        self.gpio.configure(
            self.url,
            direction=self.OUTPUT_PINS,
            frequency=self.gpio_frequency,
            initial=self.SEN,
        )
    # ==================================================================
    # GPIO
    # ==================================================================
    def _exchange(self, outputs):
        """Output a GPIO waveform and return the sampled GPIO inputs."""

        if not outputs:
            return b""

        outputs = bytes(outputs)
        inputs = self.gpio.exchange(outputs)

        if len(inputs) != len(outputs):
            raise RuntimeError(
                f"FTDI exchange length mismatch: "
                f"sent {len(outputs)} bytes, received {len(inputs)} bytes"
            )

        return inputs

    def _write_gpio(self, value):
        """
        Write one GPIO state.

        Kept for compatibility with the existing driver.
        """

        self._exchange(bytes([value]))

    # ==================================================================
    # SPI clocking
    # ==================================================================

    def _clock_out_bit(self, bit):
        """
        Output one SDIO bit.

        ADS54J40 samples SDIO on the rising edge of SCLK.
        """

        value = self.SDIO if bit else 0

        self._exchange(
            bytes([
                value,
                value | self.SCK,
            ])
        )

    def _clock_in_bit(self):
        """
        Read one SDO bit.

        ADS54J40 updates SDO on the falling edge.
        The host samples SDO on the following rising edge.
        """

        inputs = self._exchange(
            bytes([
                0,
                self.SCK,
            ])
        )

        # The second sample corresponds to the rising edge.
        return 1 if (inputs[1] & self.SDO) else 0

    # ==================================================================
    # Bit-level transfers
    # ==================================================================

    def _send_bits(self, value, nbits):
        """
        Send nbits MSB first using one synchronous FTDI transfer.

        For each SPI bit:

            state 0: SCLK low, SDIO valid
            state 1: SCLK high, SDIO valid

        The FTDI hardware generates the timing between states.
        """

        outputs = bytearray(2 * nbits)

        pos = 0

        for bit_index in range(nbits - 1, -1, -1):
            bit = (value >> bit_index) & 1
            data = self.SDIO if bit else 0

            # SCLK low, data already valid.
            outputs[pos] = data

            # Rising edge.
            outputs[pos + 1] = data | self.SCK

            pos += 2

        # Return to SCLK low.
        outputs.append(0)

        self._exchange(outputs)

    def _read_bits(self, nbits):
        """
        Read nbits MSB first.

        ADS54J40 SDOUT changes on the SCLK falling edge,
        so sample after the falling edge.
        """

        outputs = bytearray()

        for _ in range(nbits):
            # Rising edge
            outputs.append(self.SCK)

            # Falling edge
            outputs.append(0)

        inputs = self._exchange(outputs)

        value = 0

        if getattr(self, "verbose", False):
            print("SDO samples:")

        for bit_index in range(nbits):
            # Sample corresponding to the low-SCLK state after
            # the falling edge.
            sample = inputs[2 * bit_index + 1]

            bit = 1 if (sample & self.SDO) else 0

            if getattr(self, "verbose", False):
                print(
                    f"  bit {nbits - 1 - bit_index}: "
                    f"FTDI input=0x{sample:02X} "
                    f"SDO={'HIGH' if bit else 'LOW'}"
                )

            value = (value << 1) | bit

        if getattr(self, "verbose", False):
            print(f"  reconstructed value = 0x{value:02X}")

        return value    
    # ==================================================================
    # SEN control
    # ==================================================================

    def _begin(self):
        """
        Assert SEN.

        Result:
            SEN = 0
            SCK = 0
            SDIO = 0

        Holds SEN low for a couple of quiet samples before the first
        SCLK edge (tSLOADS >= 100 ns).
        """
        self._exchange(bytes([0, 0, 0]))

    def _end(self):
        """
        Finish SPI transaction and deassert SEN.

        Returns to SCLK low, holds it quiet, then deasserts SEN
        (tSLOADH >= 100 ns).
        """
        self._exchange(bytes([0, 0, self.SEN]))    
    # ==================================================================
    # SPI header
    # ==================================================================

    @staticmethod
    def _make_header(
        read,
        bank,
        page_access,
        channel,
        address,
    ):
        """
        Construct the 16-bit ADS54J40 SPI header.

        bit 15      R/W
        bit 14      M
        bit 13      P
        bit 12      CH
        bits 11:0  address
        """

        if not 0 <= address <= 0xFFF:
            raise ValueError(
                f"Address must be 12-bit, got 0x{address:X}"
            )

        if read not in (0, 1):
            raise ValueError("read must be 0 or 1")

        if bank not in (0, 1):
            raise ValueError("bank must be 0 or 1")

        if page_access not in (0, 1):
            raise ValueError("page_access must be 0 or 1")

        if channel not in (0, 1):
            raise ValueError("channel must be 0 or 1")

        return (
            (read << 15)
            | (bank << 14)
            | (page_access << 13)
            | (channel << 12)
            | address
        )

    # ==================================================================
    # Low-level SPI transactions
    # ==================================================================

    def raw_write(
        self,
        address,
        value,
        *,
        bank=0,
        page_access=0,
        channel=0,
    ):
        """
        Perform one complete 24-bit SPI write.

        SEN is asserted for the complete transaction:

            SEN low
            16-bit header
            8-bit data
            SEN high
        """

        if not 0 <= value <= 0xFF:
            raise ValueError(
                f"Value must be 8-bit, got 0x{value:X}"
            )

        header = self._make_header(
            read=0,
            bank=bank,
            page_access=page_access,
            channel=channel,
            address=address,
        )

        word = (header << 8) | value

        if self.verbose:
            print(
                f"WRITE: "
                f"R/W=0 "
                f"M={bank} "
                f"P={page_access} "
                f"CH={channel} "
                f"ADDR=0x{address:03X} "
                f"DATA=0x{value:02X} "
                f"WORD=0x{word:06X}"
            )

        self._begin()

        try:
            self._send_bits(word, 24)
        finally:
            self._end()

    def raw_read(
        self,
        address,
        *,
        bank=0,
        page_access=0,
        channel=0,
    ):
        """
        Perform one complete ADS54J40 SPI read.

        Sends:

            16-bit read header

        followed by:

            8 read clocks.

        SEN remains low for the entire transaction.
        """

        header = self._make_header(
            read=1,
            bank=bank,
            page_access=page_access,
            channel=channel,
            address=address,
        )

        if self.verbose:
            print(
                f"READ:  "
                f"R/W=1 "
                f"M={bank} "
                f"P={page_access} "
                f"CH={channel} "
                f"ADDR=0x{address:03X} "
                f"HEADER=0x{header:04X}"
            )

        self._begin()

        try:
            self._send_bits(header, 16)

            value = self._read_bits(8)
        finally:
            self._end()

        if self.verbose:
            print(
                f"       DATA=0x{value:02X}"
            )

        return value

    # ==================================================================
    # Active-SEN transactions
    # ==================================================================

    def _raw_write_active(
        self,
        address,
        value,
        *,
        bank=0,
        page_access=0,
        channel=0,
    ):
        """
        Write one register while SEN is already low.

        Does not assert or deassert SEN.
        """

        if not 0 <= value <= 0xFF:
            raise ValueError(
                f"Value must be 8-bit, got 0x{value:X}"
            )
        header = self._make_header(
            read=0,
            bank=bank,
            page_access=page_access,
            channel=channel,
            address=address,
        )

        word = (header << 8) | value

        self._send_bits(word, 24)

        if self.verbose:
            print(
                f"WRITE: "
                f"R/W=0 "
                f"M={bank} "
                f"P={page_access} "
                f"CH={channel} "
                f"ADDR=0x{address:03X} "
                f"DATA=0x{value:02X} "
                f"WORD=0x{word:06X}"
            )

    def _raw_read_active(
        self,
        address,
        *,
        bank=0,
        page_access=0,
        channel=0,
    ):
        """
        Read one register while SEN is already low.

        Does not assert or deassert SEN.
        """

        header = self._make_header(
            read=1,
            bank=bank,
            page_access=page_access,
            channel=channel,
            address=address,
        )

        if self.verbose:
            print(
                f"READ:  "
                f"R/W=1 "
                f"M={bank} "
                f"P={page_access} "
                f"CH={channel} "
                f"ADDR=0x{address:03X} "
                f"HEADER=0x{header:04X}"
            )

        self._send_bits(header, 16)

        value = self._read_bits(8)

        if self.verbose:
            print(
                f"       DATA=0x{value:02X}"
            )

        return value

    # ==================================================================
    # Page selection helpers
    # ==================================================================

    def _select_analog_page_active(self, selector):
        """
        Select an analog page while SEN is already low.

        selector:

            0x80 -> Master
            0x0F -> ADC
        """

        self._raw_write_active(
            0x011,
            selector,
            bank=0,
            page_access=0,
            channel=0,
        )

    def _select_jesd_page_active(self, selector):
        """
        Select a normal JESD page while SEN is already low.
        """

        low = selector & 0xFF
        high = (selector >> 8) & 0xFF

        # Select JESD page.
        self._raw_write_active(
            0x003,
            low,
            bank=1,
            page_access=0,
            channel=0,
        )

        # Enable independent channel control.
        self._raw_write_active(
            0x005,
            0x01,
            bank=1,
            page_access=0,
            channel=0,
        )

        # Select JESD page.
        self._raw_write_active(
            0x004,
            high,
            bank=1,
            page_access=0,
            channel=0,
        )


    def _select_offset_page_active(self, selector):
        """
        Select an offset page while SEN is already low.

        First selects 0x6100 through 0x003/0x004.

        Then:

            0x0000 -> Offset Read
            0x0500 -> Offset Load

        through 0x001/0x002.
        """

        low = selector & 0xFF
        high = (selector >> 8) & 0xFF

        # Select 0x6100.

        self._raw_write_active(
            0x003,
            0x00,
            bank=1,
            page_access=0,
            channel=0,
        )

        self._raw_write_active(
            0x004,
            0x61,
            bank=1,
            page_access=0,
            channel=0,
        )

        # Select offset subpage through 0x001/0x002.

        self._raw_write_active(
            0x001,
            low,
            bank=1,
            page_access=0,
            channel=0,
        )

        self._raw_write_active(
            0x002,
            high,
            bank=1,
            page_access=0,
            channel=0,
        )

    def _select_page_active(self, page):
        """
        Select a user-visible page.

        SEN must already be low.
        """

        if page not in self.PAGES:
            raise ValueError(
                f"Unknown page '{page}'. "
                f"Available pages: {', '.join(self.PAGES)}"
            )

        selector = self.PAGES[page]["selector"]

        # Directly accessible register group.
        if selector is None:
            return

        selector_type, selector_value = selector

        if selector_type == "analog":
            self._select_analog_page_active(
                selector_value
            )

        elif selector_type == "jesd":
            self._select_jesd_page_active(
                selector_value
            )

        elif selector_type == "offset":
            self._select_offset_page_active(
                selector_value
            )

        else:
            raise RuntimeError(
                f"Unknown selector type '{selector_type}'"
            )

    # ==================================================================
    # Page validation
    # ==================================================================

    def _validate_page(self, page, address):
        """
        Validate page name and register address.
        """

        if page not in self.PAGES:
            raise ValueError(
                f"Unknown page '{page}'. "
                f"Available pages:\n"
                f"  " + "\n  ".join(self.PAGES)
            )

        spec = self.PAGES[page]

        if not 0 <= address <= 0xFFF:
            raise ValueError(
                f"Address must be 12-bit, got 0x{address:X}"
            )

        if not (
            spec["address_min"]
            <= address
            <= spec["address_max"]
        ):
            raise ValueError(
                f"Address 0x{address:03X} is outside "
                f"page '{page}' range "
                f"0x{spec['address_min']:03X}"
                f"..0x{spec['address_max']:03X}"
            )

        return spec

    # ==================================================================
    # Generic register access
    # ==================================================================

    def read_register(
        self,
        page,
        address,
        *,
        channel=0,
    ):
        """
        Read an arbitrary ADS54J40 register.
        """

        spec = self._validate_page(
            page,
            address,
        )

        if channel not in (0, 1):
            raise ValueError(
                "channel must be 0 or 1"
            )

        if self.verbose:
            print(
                f"READ REGISTER: "
                f"{page} "
                f"0x{address:03X}"
            )

        self._begin()

        try:
            self._select_page_active(page)

            value = self._raw_read_active(
                address,
                bank=spec["bank"],
                page_access=spec["page_access"],
                channel=channel,
            )

        finally:
            self._end()

        if self.verbose:
            print(
                f"               "
                f"{page} "
                f"0x{address:03X} "
                f"= 0x{value:02X}"
            )

        return value

    def write_register(
        self,
        page,
        address,
        value,
        *,
        channel=0,
    ):
        """
        Write an arbitrary ADS54J40 register.
        """

        spec = self._validate_page(
            page,
            address,
        )

        if not 0 <= value <= 0xFF:
            raise ValueError(
                f"Value must be 8-bit, got 0x{value:X}"
            )

        if channel not in (0, 1):
            raise ValueError(
                "channel must be 0 or 1"
            )

        if self.verbose:
            print(
                f"WRITE REGISTER: "
                f"{page} "
                f"0x{address:03X} "
                f"= 0x{value:02X}"
            )

        self._begin()

        try:
            self._select_page_active(page)

            self._raw_write_active(
                address,
                value,
                bank=spec["bank"],
                page_access=spec["page_access"],
                channel=channel,
            )

        finally:
            self._end()

    # ==================================================================
    # Reset
    # ==================================================================

    def reset(self):
        """
        Perform an ADS54J40 software reset.

        Register 0x000 is the global software-reset register.

        0x81 -> software reset.
        """

        if self.verbose:
            print("RESET: ADS54J40 software reset")

        self.raw_write(
            address=0x000,
            value=0x81,
            bank=0,
            page_access=0,
            channel=0,
        )

    # ==================================================================
    # Multiple-register access
    # ==================================================================

    def read_many(
        self,
        registers,
        *,
        verbose=None,
    ):
        """
        Read multiple registers.

        Input:

            [
                ("analog_master", 0x20),
                ("analog_master", 0x21),
                ("jesd_digital", 0x06),
                ("jesd_analog", 0x16),
            ]

        Or:

            [
                ("jesd_digital", 0x06, 0),
                ("jesd_digital", 0x06, 1),
            ]
        """

        old_verbose = self.verbose

        if verbose is not None:
            self.verbose = verbose

        results = []

        try:
            for entry in registers:

                if len(entry) == 2:
                    page, address = entry
                    channel = 0

                elif len(entry) == 3:
                    page, address, channel = entry

                else:
                    raise ValueError(
                        "Register entry must contain "
                        "(page, address) or "
                        "(page, address, channel)"
                    )

                value = self.read_register(
                    page,
                    address,
                    channel=channel,
                )

                results.append({
                    "page": page,
                    "address": address,
                    "channel": channel,
                    "value": value,
                })

        finally:
            self.verbose = old_verbose

        return results

    def write_many(
        self,
        registers,
        *,
        verify=False,
        verbose=None,
    ):
        """
        Write multiple registers.

        Input:

            [
                ("analog_master", 0x20, 0x01),
                ("jesd_digital", 0x06, 0x02),
                ("jesd_analog", 0x16, 0x40),
            ]

        Or:

            [
                ("jesd_digital", 0x06, 0x02, 0),
            ]

        If verify=True, read each register back.
        """

        old_verbose = self.verbose

        if verbose is not None:
            self.verbose = verbose

        results = []

        try:
            for entry in registers:

                if len(entry) == 3:
                    page, address, value = entry
                    channel = 0

                elif len(entry) == 4:
                    page, address, value, channel = entry

                else:
                    raise ValueError(
                        "Register entry must contain "
                        "(page, address, value) or "
                        "(page, address, value, channel)"
                    )

                self.write_register(
                    page,
                    address,
                    value,
                    channel=channel,
                )

                result = {
                    "page": page,
                    "address": address,
                    "channel": channel,
                    "written": value,
                }

                if verify:
                    readback = self.read_register(
                        page,
                        address,
                        channel=channel,
                    )

                    result["readback"] = readback
                    result["verified"] = (
                        readback == value
                    )

                    if readback != value:
                        raise RuntimeError(
                            f"Readback mismatch: "
                            f"{page} "
                            f"0x{address:03X} "
                            f"CH={channel}: "
                            f"wrote 0x{value:02X}, "
                            f"read 0x{readback:02X}"
                        )

                results.append(result)

        finally:
            self.verbose = old_verbose

        return results

    # ==================================================================
    # Convenience page-selection methods
    # ==================================================================

    def select_analog_page(self, page):
        """
        Explicitly select an analog page.

        page:
            "master"
            "adc"

        Normally use read_register() / write_register().
        """

        page_map = {
            "master": "analog_master",
            "adc": "analog_adc",
        }

        if page not in page_map:
            raise ValueError(
                "Analog page must be 'master' or 'adc'"
            )

        selector = self.PAGES[
            page_map[page]
        ]["selector"][1]

        self._begin()

        try:
            self._select_analog_page_active(
                selector
            )
        finally:
            self._end()

    def select_jesd_page(self, page):
        """
        Explicitly select a normal JESD page.

        page:
            "main"
            "digital"
            "analog"

        Normally use read_register() / write_register().
        """

        page_map = {
            "main": "jesd_main",
            "digital": "jesd_digital",
            "analog": "jesd_analog",
        }

        if page not in page_map:
            raise ValueError(
                "JESD page must be "
                "'main', 'digital' or 'analog'"
            )

        selector = self.PAGES[
            page_map[page]
        ]["selector"][1]

        self._begin()

        try:
            self._select_jesd_page_active(
                selector
            )
        finally:
            self._end()

    def select_offset_page(self, page):
        """
        Explicitly select an offset page.

        page:
            "offset_read"
            "offset_load"

        Normally use read_register() / write_register().
        """

        if page not in (
            "offset_read",
            "offset_load",
        ):
            raise ValueError(
                "Offset page must be "
                "'offset_read' or 'offset_load'"
            )

        selector = self.PAGES[
            page
        ]["selector"][1]

        self._begin()

        try:
            self._select_offset_page_active(
                selector
            )
        finally:
            self._end()

    # ==================================================================
    # Backwards-compatible convenience functions
    # ==================================================================

    def read_analog_register(
        self,
        address,
        page="master",
    ):
        """
        Compatibility wrapper.

        Prefer:

            read_register("analog_master", address)

        or:

            read_register("analog_adc", address)
        """

        page_map = {
            "master": "analog_master",
            "adc": "analog_adc",
        }

        if page not in page_map:
            raise ValueError(
                "Analog page must be 'master' or 'adc'"
            )

        return self.read_register(
            page_map[page],
            address,
        )

    def read_jesd_register(
        self,
        address,
        page="main",
        channel=0,
    ):
        """
        Compatibility wrapper.

        Prefer:

            read_register("jesd_main", address)

            read_register("jesd_digital", address)

            read_register("jesd_analog", address)
        """

        page_map = {
            "main": "jesd_main",
            "digital": "jesd_digital",
            "analog": "jesd_analog",
        }

        if page not in page_map:
            raise ValueError(
                "JESD page must be "
                "'main', 'digital' or 'analog'"
            )

        return self.read_register(
            page_map[page],
            address,
            channel=channel,
        )
    # #####################################################
    # Startup sequence
    ######################################################

    def initialize(self):
        """
        Datasheet Table 9-1 startup sequence (LMFS = 8224).
        Run once after power-up before accessing registers.
        """
        if self.verbose:
            print("INIT: ADS54J40 startup sequence")

        # 1. Software reset (general register 0x000, M=0, P=0)
        self.raw_write(0x000, 0x81, bank=0, page_access=0, channel=0)

        # 2. Clear unused JESD pages + select main digital page (4-001h..4-004h)
        self._begin()
        self._raw_write_active(0x001, 0x00, bank=1, page_access=0, channel=0)
        self._raw_write_active(0x002, 0x00, bank=1, page_access=0, channel=0)
        self._raw_write_active(0x003, 0x00, bank=1, page_access=0, channel=0)
        self._raw_write_active(0x004, 0x68, bank=1, page_access=0, channel=0)
        self._end()

        # 3. DIG RESET the JESD bank (channel A, 6-0F7h)
        self.write_register("jesd_main", 0x0F7, 0x01, channel=0)

        # 4. Pulse reset for channel A (6-000h: 01 then 00)
        self.write_register("jesd_main", 0x000, 0x01, channel=0)
        self.write_register("jesd_main", 0x000, 0x00, channel=0)

        # 5. Select master page, set ALWAYS WRITE 1 (0-059h)
        self.write_register("analog_master", 0x059, 0x20)

    # ==================================================================
    # Cleanup
    # ==================================================================

    def close(self):
        """Close the FT245 GPIO interface."""
        self.gpio.close()

    def __enter__(self):
        self.initialize()
        return self
    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()


