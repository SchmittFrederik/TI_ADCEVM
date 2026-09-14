#!/usr/bin/env python3

import time

from pyftdi.gpio import GpioAsyncController


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
    The ADS54J40-specific M/P/page-selection logic is handled here,
    so application code does not need to know about it.
    """

    # ==================================================================
    # FT245 GPIO pins
    # ==================================================================

    SCK = 0x01       # D0 -> SCLK
    SDIO = 0x02      # D1 -> SDIO
    SDO = 0x04       # D2 <- SDO
    SEN = 0x08       # D3 -> SEN

    OUTPUT_PINS = SCK | SDIO | SEN

    # 100 us half-period -> approximately 5 kHz SCLK.
    #
    # Deliberately slow during initial bring-up and well below the
    # ADS54J40 SPI maximum of 2 MHz.
    HALF_PERIOD = 100e-6

    # ==================================================================
    # ADS54J40 register/page structure
    # ==================================================================

    # Every entry describes a user-visible register page.
    #
    # "bank"       -> M bit
    # "page_access"-> P bit
    #
    # "selector" describes how this page is selected:
    #
    #   ("analog", value)
    #   ("jesd", value)
    #   ("offset", value)
    #
    # Pages with selector=None are directly accessible registers.
    #
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
        #
        # First:
        #
        #     JESD Bank Page Selection = 0x6100
        #
        # Then:
        #
        #     0x0000 -> Offset Read
        #     0x0500 -> Offset Load
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
        Initialize the FT245 GPIO interface.

        Parameters
        ----------
        url : str
            PyFtdi FT245 URL.

        half_period : float or None
            SPI half-period in seconds.
            Defaults to HALF_PERIOD.

        verbose : bool
            Print SPI transactions if True.
        """

        self.url = url
        self.verbose = verbose

        if half_period is None:
            self.half_period = self.HALF_PERIOD
        else:
            self.half_period = half_period

        self.gpio = GpioAsyncController()

        self.gpio.configure(
            url,
            direction=self.OUTPUT_PINS,
            frequency=100_000,
        )

        # Idle state:
        #
        # SEN = 1
        # SCK = 0
        # SDIO = 0
        #
        self._write_gpio(self.SEN)

    # ==================================================================
    # GPIO
    # ==================================================================

    def _write_gpio(self, value):
        """Write the complete FT245 GPIO output value."""
        self.gpio.write(value)

    # ==================================================================
    # SPI clocking
    # ==================================================================

    def _clock_out_bit(self, bit):
        """
        Output one SDIO bit.

        ADS54J40 samples SDIO on the rising edge of SCLK.
        """

        value = self.SDIO if bit else 0

        # Put data on SDIO.
        self._write_gpio(value)
        time.sleep(self.half_period)

        # Rising edge.
        self._write_gpio(value | self.SCK)
        time.sleep(self.half_period)

        # Falling edge.
        self._write_gpio(value)

    def _clock_in_bit(self):
        """
        Read one SDO bit.

        ADS54J40 updates SDO on the falling edge.
        The host samples SDO on the following rising edge.
        """

        # Rising edge.
        self._write_gpio(self.SCK)
        time.sleep(self.half_period)

        # Sample SDO.
        pins = self.gpio.read(peek=True)
        bit = 1 if (pins & self.SDO) else 0

        # Falling edge.
        self._write_gpio(0)
        time.sleep(self.half_period)

        return bit

    # ==================================================================
    # Bit-level transfers
    # ==================================================================

    def _send_bits(self, value, nbits):
        """Send nbits MSB first."""

        for bit_index in range(nbits - 1, -1, -1):
            bit = (value >> bit_index) & 1
            self._clock_out_bit(bit)

    def _read_bits(self, nbits):
        """Read nbits MSB first from SDO."""

        value = 0

        for _ in range(nbits):
            value = (value << 1) | self._clock_in_bit()

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
        """

        self._write_gpio(0)
        time.sleep(self.half_period)

    def _end(self):
        """
        Finish SPI transaction and deassert SEN.
        """

        # Ensure SCK is low.
        self._write_gpio(0)
        time.sleep(self.half_period)

        # Deassert SEN.
        self._write_gpio(self.SEN)
        time.sleep(self.half_period)

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
            raise ValueError(
                "read must be 0 or 1"
            )

        if bank not in (0, 1):
            raise ValueError(
                "bank must be 0 or 1"
            )

        if page_access not in (0, 1):
            raise ValueError(
                "page_access must be 0 or 1"
            )

        if channel not in (0, 1):
            raise ValueError(
                "channel must be 0 or 1"
            )

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

        SEN is asserted only for this transaction.
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
        self._send_bits(word, 24)
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

        # Send read header.
        self._send_bits(header, 16)

        # Complete SCK-low period before first read clock.
        time.sleep(self.half_period)

        # Receive register value.
        value = self._read_bits(8)

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

        self._send_bits(header, 16)

        # Complete SCK-low period before first read clock.
        time.sleep(self.half_period)

        return self._read_bits(8)

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

        selector:

            0x6800 -> Main Digital
            0x6900 -> JESD Digital
            0x6A00 -> JESD Analog
            0x6100 -> Offset page selection
        """

        low = selector & 0xFF
        high = (selector >> 8) & 0xFF

        # JESD Bank Page Selection
        #
        # M = 1
        # P = 0
        #
        # 0x003 = low byte
        # 0x004 = high byte

        self._raw_write_active(
            0x003,
            low,
            bank=1,
            page_access=0,
            channel=0,
        )

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

        This first selects 0x6100 through 0x003/0x004,
        then selects either:

            0x0000 -> Offset Read
            0x0500 -> Offset Load

        through 0x001/0x002.
        """

        low = selector & 0xFF
        high = (selector >> 8) & 0xFF

        # First select the Offset Read/Load selection page:
        #
        # 0x003 = 0x00
        # 0x004 = 0x61
        #
        # => 0x6100

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

        # Then select the actual offset subpage:
        #
        # 0x001 = low byte
        # 0x002 = high byte

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

        # Directly accessible page/register group.
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

        Parameters
        ----------
        page : str
            One of:

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

        address : int
            Register address.

        channel : int
            ADS54J40 CH bit, 0 or 1.

        Returns
        -------
        int
            8-bit register value.
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

        # If this is a paged register, select the page.
        self._select_page_active(page)

        value = self._raw_read_active(
            address,
            bank=spec["bank"],
            page_access=spec["page_access"],
            channel=channel,
        )

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

        Parameters
        ----------
        page : str
            User-visible ADS54J40 page name.

        address : int
            Register address.

        value : int
            8-bit register value.

        channel : int
            ADS54J40 CH bit, 0 or 1.
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

        # Select page if necessary.
        self._select_page_active(page)

        self._raw_write_active(
            address,
            value,
            bank=spec["bank"],
            page_access=spec["page_access"],
            channel=channel,
        )

        self._end()

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

        Or with explicit channel:

            [
                ("jesd_digital", 0x06, 0),
                ("jesd_digital", 0x06, 1),
            ]

        Returns a list of dictionaries:

            [
                {
                    "page": "analog_master",
                    "address": 0x20,
                    "channel": 0,
                    "value": 0x00,
                },
                ...
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

        Or with explicit channel:

            [
                ("jesd_digital", 0x06, 0x02, 0),
            ]

        If verify=True, each value is read back after writing.

        Returns a list of dictionaries describing the result.
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
    # Configuration
    # ==================================================================

    def apply_config(
        self,
        config,
        *,
        verify=False,
        verbose=None,
    ):
        """
        Apply a register configuration.

        The configuration is an iterable containing either:

            (page, address, value)

        or:

            (page, address, value, channel)

        Example:

            config = [
                ("analog_master", 0x20, 0x01),
                ("jesd_main", 0x10, 0x02),
                ("jesd_digital", 0x06, 0x40),
            ]

            adc.apply_config(
                config,
                verify=True,
            )
        """

        return self.write_many(
            config,
            verify=verify,
            verbose=verbose,
        )

    # ==================================================================
    # Convenience page-selection methods
    # ==================================================================

    def select_analog_page(self, page):
        """
        Explicitly select an analog page.

        page:
            "master"
            "adc"

        This is mainly useful for interactive/debugging work.

        Normal register access should preferably use
        read_register() / write_register().
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

        self._select_analog_page_active(
            selector
        )

        self._end()

    def select_jesd_page(self, page):
        """
        Explicitly select a normal JESD page.

        page:
            "main"
            "digital"
            "analog"

        Normal register access should preferably use
        read_register() / write_register().
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

        self._select_jesd_page_active(
            selector
        )

        self._end()

    def select_offset_page(self, page):
        """
        Explicitly select an offset page.

        page:
            "offset_read"
            "offset_load"

        Normal register access should preferably use
        read_register() / write_register().
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

        self._select_offset_page_active(
            selector
        )

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

    # ==================================================================
    # Cleanup
    # ==================================================================

    def close(self):
        """Close the FT245 GPIO interface."""

        self.gpio.close()

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()    