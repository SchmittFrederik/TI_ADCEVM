from pyftdi.gpio import GpioSyncController

class ADSGpioSyncController(GpioSyncController):

    def exchange(self, out):
        """Output a GPIO waveform and wait for all input samples."""

        if not self.is_connected:
            raise RuntimeError("GPIO controller is not connected")

        out = bytes(out)

        if not out:
            return b""

        self._ftdi.write_data(out)

        return bytes(
            self._ftdi.read_data_bytes(
                len(out),
                attempt=50,
            )
        )