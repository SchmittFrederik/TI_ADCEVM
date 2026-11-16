"""
FT245RL pin map of the ADS54J40EVM and a small PyFtdi helper.

The FT245RL data bus is used in bit-bang mode. SCK and SDIO are shared
between the LMK04828 and the ADS54J40 (the ADC side goes through a
TXB0104 level shifter), so whichever chip is not being talked to must
keep its chip select deasserted (high).

See docs/ADS54J40EVM_ADS54J60EVM-SCH_D.pdf, FT245RL / TXB0104 sheet.
"""

from pyftdi.gpio import GpioSyncController

DEFAULT_URL = "ftdi://ftdi:232r/1"

SCK = 0x01      # D0 -> LMK SCK and ADC SCLK (shared)
SDIO = 0x02     # D1 <-> LMK SDIO, -> ADC SDIN (shared)
ADC_SDO = 0x04  # D2 <- ADC SDOUT
ADC_SEN = 0x08  # D3 -> ADC SEN (active low)
LMK_CS = 0x10   # D4 -> LMK CS* (active low)
LMK_SDO = 0x40  # D6 <-> LMK RESET/GPO pin (4-wire SDO, or hardware reset)


class ADSGpioSyncController(GpioSyncController):
    """
    GpioSyncController with a more patient exchange().

    PyFtdi's stock exchange() gives up after 4 read attempts, which is
    not enough for the longer waveforms used here. This version waits
    up to 50 attempts for all input samples to arrive.
    """

    def exchange(self, out):
        """Output a GPIO waveform and return one input sample per output byte."""

        if not self.is_connected:
            raise RuntimeError("GPIO controller is not connected")

        out = bytes(out)
        if not out:
            return b""

        self._ftdi.write_data(out)
        return bytes(self._ftdi.read_data_bytes(len(out), attempt=50))
