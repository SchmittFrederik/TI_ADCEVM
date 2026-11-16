# TI_ADCEVM
Python wrappers for the SPI interface to the clock and ADC chips on TIs evaluation board ADS54J40.

Both chips are bit-banged through the EVM's FT245RL (PyFtdi). SCK and SDIO
are shared between the LMK04828 and the ADS54J40; each driver keeps the
other chip's select line high.

## Install

    pip install -e .

## Library

```python
from ti_adcevm import ADS54J40, LMK04828

with LMK04828() as lmk:                      # LMK assumed in 3-wire readback
    lmk.load_config("configs/LMKConfig.txt", verify=True)
    print(lmk.read_status())

with ADS54J40() as adc:
    adc.initialize()                         # datasheet Table 9-1 reset sequence
    adc.write_register("jesd_analog", 0x16, 0x02, channel=1)
    print(hex(adc.read_register("jesd_analog", 0x16, channel=1)))
```

Opening an `ADS54J40` does not reset or configure the ADC; call
`initialize()` explicitly when you want the reset sequence.

## Scripts (`scripts/`)

| Script                 | Purpose                                                         |
|------------------------|-----------------------------------------------------------------|
| `lmk_flash.py`         | Write a TICS Pro / EVM GUI register file (`--verify` to check)  |
| `lmk_read.py`          | Read one LMK register                                           |
| `lmk_write.py`         | Write one LMK register                                          |
| `lmk_status.py`        | PLL lock, reference, LOS, holdover and DAC status               |
| `lmk_clearpll.py`      | Clear the PLL lock-lost flags and re-check                      |
| `lmk_checkconfig.py`   | Offline: decode a register file into PLL/SYSREF/output clocks   |
| `translateticsfile.py` | Offline: annotate a TICS Pro file with decoded address/value    |
| `ads_flash.py`         | Reset the ADC and configure the JESD204B link (LMFS 4211)       |
| `ads_read.py`          | Read one ADC register (`page address [-c channel]`)             |
| `ads_write.py`         | Write one ADC register (`page address value [-c channel]`)      |

The LMK scripts take `--mode 3wire|4wire` to tell the driver which readback
path the LMK is currently using (default 3-wire). The ADC scripts take `-v`
(log SPI frames) or `-vv` (also every sampled SDOUT bit).
