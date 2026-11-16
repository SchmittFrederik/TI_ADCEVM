---
name: ti-adcevm
description: How to work with this repo, which drives the TI ADS54J40EVM (LMK04828 clock chip + ADS54J40 ADC over SPI through the board's FT245). Use for any task that touches the LMK04828 or ADS54J40, their configs/*.txt register files, the scripts/ wrappers, PLL lock or SYSREF/JESD204B clocking, or SPI readback problems.
---

# TI ADS54J40EVM tools

The Python package `ti_adcevm` (in `src/`) bit-bangs SPI to two chips through the EVM's FT245RL
using pyftdi: the **LMK04828** jitter cleaner/clock generator and the **ADS54J40** ADC.
`scripts/` holds small CLI wrappers. Datasheets and the EVM schematic are in `docs/`
(`lmk04828.pdf`, `ads54j40.pdf`, `ADS54J40EVM_ADS54J60EVM-SCH_D.pdf`; sheet 4 = clock, sheet 7 = USB/FT245).
Use `pdftotext -layout` to search them.

## Hard constraints

- **Python 3.8.10.** The lab PC runs an old Ubuntu and can't be upgraded. Don't use `X | None`, builtin
  generics in annotations (`list[int]`), `match`, `str.removeprefix`, dict `|`, or 3.12 f-string quote
  reuse. Use `typing.Optional`/`List`. Keep `requires-python = ">=3.8"` and
  `license = {text = "MIT"}` in pyproject.toml. Check with
  `python3 -c "import ast,glob; [ast.parse(open(f).read(), f, feature_version=(3,8)) for f in glob.glob('src/ti_adcevm/*.py')+glob.glob('scripts/*.py')]"`.
- **`configs/LMKConfig.txt` is the only git-tracked config** and the working one. Change that file
  when asked to modify the clock config. Other files in `configs/` are untracked experiments/TICS exports.
- The dev machine has no hardware. Hardware results come from the user running scripts on the lab PC.

## Scripts (run from repo root)

| Script | Purpose |
|---|---|
| `lmk_flash.py CONFIG [--verify]` | Write a TICS Pro / EVM GUI register file in file order. `--verify` reads all back at the end |
| `lmk_read.py ADDR`, `lmk_write.py ADDR VALUE [--verify]` | Single LMK register |
| `lmk_status.py` | PLL1/PLL2 lock + lock-lost, selected ref, holdover, DAC |
| `lmk_clearpll.py` | Clear lock-lost flags, wait, re-check |
| `lmk_pll1_diag.py [--seconds N]` | Log PLL1 lock + VCXO tuning voltage (DAC) over time, with interpretation |
| `lmk_readback_test.py CONFIG` | Read back a flashed file in 3 readback modes (open drain / push-pull / 4-wire) |
| `lmk_checkconfig.py CONFIG` | Offline: decode a register file into PLL2/VCO, SYSREF and output frequencies |
| `translateticsfile.py IN OUT` | Offline: annotate a TICS file with decoded address/value |
| `ads_flash.py [--k 8] [-v]` | ADC reset sequence (datasheet Table 9-1) + JESD config LMFS 4244, subclass 1. Checks K/SYSREF validity first, verifies each write |
| `ads_read.py PAGE ADDR [-c CH]`, `ads_write.py PAGE ADDR VALUE [-c CH] [--verify]` | Single ADC register |

LMK scripts take `--mode 3wire|4wire` (which readback path the chip is currently in, default 3wire).
ADC scripts take `-v`/`-vv`. ADC pages: `analog_general, analog_master, analog_adc, jesd_general,
jesd_main, jesd_digital, jesd_analog, offset_read, offset_load` (see `ADS54J40.PAGES`).

Typical bring-up: `lmk_flash.py configs/LMKConfig.txt --verify` → wait a few s → `lmk_clearpll.py` →
`lmk_status.py` → `ads_flash.py`.

## Hardware facts (from the schematic)

- FT245 pins (`ftdi_gpio.py`): D0 SCK, D1 SDIO (both **shared** by LMK and ADC; the ADC side goes through
  a TXB0104 level shifter), D2 ADC SDOUT, D3 ADC SEN, D4 LMK CS, D6 LMK RESET/GPO pin (4-wire SDO or
  hardware reset). Each driver holds the other chip's select high.
- LMK_SDIO has **no pull-up**, and the TXB0104 weakly holds it low → LMK **open-drain readback fails**
  (leading 1s read as 0). `LMKConfig.txt` sets 0x149 = 0x02 (push-pull). Right after power-up the chip
  is open drain (POR default), so reads before the first flash are unreliable.
- LEDs: D1 = PLL1 locked (STATUS_LD1), D2 = PLL2 locked (STATUS_LD2). They give lock state without SPI.
- CLKin1 (J6 SMA): 10 MHz reference, single-ended, AC coupled, **unterminated** (R42 not fitted).
  Spec at the pin: 0.35–2.4 Vpp. OSCin: 122.88 MHz VCXO (Y1). PLL1 loop filter is slow (39 kΩ / 68 µF).
- Outputs: DCLKout0 → FPGA JESD clock, SDCLKout1 → FPGA SYSREF, DCLKout2 → ADC sample clock,
  SDCLKout3 → ADC SYSREF (AC coupled), DCLKout12 → FPGA CLK_LAO0.
- **DCLKout2 and SDCLKout3 must be LVPECL** (0x10F = 0x66, LVPECL 2000 mV): the ADC clock/SYSREF nets
  have 240 Ω emitter resistors (R43/R44, R46/R47). TI's GUI configs use this. LVDS (0x11) there gave a
  degraded ADC clock and JESD-block registers (jesd_analog page, jesd_digital 0x000) read back 0.
- **TI's EVM GUI configs** in `configs/evmgui/Configuration Files/` (`ADS54J40_LMF_4244.cfg` etc.) are the
  best reference for tested ADC write sequences, including "always write 1" bits the datasheet tables
  hide (jesd_digital 0x016 = 0x80, jesd_analog 0x012 = 0x02, offset_read 0x068 = 0x02).

## Current clock plan (`LMKConfig.txt`)

VCO0 2400 MHz (PLL2: OSCin/1536 × 2 × 15000, 80 kHz PDF; TI's EVM default). PLL1: 10 MHz/125 vs
122.88 MHz/1536 = 80 kHz. DCLKout0 100 MHz, DCLKout2 800 MHz, DCLKout12 100 MHz, SDCLKout1/3 continuous
SYSREF 3.125 MHz (SYSREF_DIV 768). The file ends with a divider SYNC sequence (datasheet 9.3.2.1.1)
and `R0 = 0x00` (back to 3-wire). Run `lmk_checkconfig.py` to see it decoded.

## Lessons learned (don't rediscover these)

- **Holdover broke PLL1 lock.** With 0x150 HOLDOVER_EN + HITLESS_SWITCH (POR default 0x03), selecting
  CLKin1 during a flash enters holdover: PLL1 runs open-loop with the VCXO at MAN_DAC (512) and only
  exits by luck. `LMKConfig.txt` uses 0x150 = 0x00. Healthy lock: DAC/Vtune ≈ 590 / 1.9 V.
- **Lock-lost flags are set after every flash and power-up.** That's normal. Clear with `lmk_clearpll.py`.
  DAC reads 512 until the tracking DAC updates (every ~26 s, only while PLL1 is locked).
- **The LMK04828 has only one SYSREF divider**: all SDCLKouts in SYSREF mode share one frequency.
  SYSREF must be LMFC / 2^n. LMFS 4244 at 800 MSPS, K=8: LMFC = (fS/4)/K = 25 MHz, SYSREF 3.125 = LMFC/8.
- **TICS Pro exports may end in 4-wire mode** (`R0 0x000010`) with 0x14A not set to SPI readback, which
  makes readback impossible. Append `R0 0x000000`. `load_config()` warns and refuses `--verify` then.
- **Readback must use synchronous bit-bang.** The LMK driver once used pyftdi async mode (write +
  sleep + peek), which gave ~25 % random read errors. Both drivers now use `SyncGpioController.exchange()`,
  where input samples are hardware-timed (`inputs[k]` = pins as set by `outputs[k-1]`).
- Registers that never read back as written (in `LMK04828.NO_VERIFY`): R0 (self-clearing RESET),
  read-only IDs 0x003–0x006/0x00C/0x00D (0x006 reads 0x20), status 0x182–0x18B, SPI_LOCK 0x1FFD–0x1FFF.
- LMK04828 RESET pin (D6) hardware reset only works in 3-wire mode (in 4-wire the LMK drives that pin).
- ADS54J40: opening the driver does **not** reset the chip; call `initialize()` explicitly (`ads_flash.py`
  does). Every JESD-bank access writes 4-005h = 01 (per-channel, broadcast off) plus the page selects.
  JESD link: **LMFS 4244** (L=4, M=2, F=4, S=4, N'=16), 8.0 Gbps/lane at 800 MSPS, FPGA core clock
  200 MHz (lane rate/40), scrambling off (ADC default). JESD MODE = 010 (0x02), PLL MODE 40X. The
  datasheet's Table 8-17 example wrongly labels 0x02 as 4211. TI's GUI files confirm 4244 = 0x02 and
  4211 = 0x04, matching Table 8-14.
- **Active lanes for 4244/4211: DA1, DA2, DB1, DB2 = FMC DP4, DP6, DP0, DP2** (EVM sheet 6, VITA 57.1
  pins). They span DP0–DP7, so on carriers that put DP0–3 and DP4–7 on separate GT quads the link needs
  two quads. Full map: DB1 DP0, DB3 DP1, DB2 DP2, DB0 DP3, DA1 DP4, DA3 DP5, DA2 DP6, DA0 DP7.

## Code layout

- `src/ti_adcevm/ftdi_gpio.py`: pin map, `SyncGpioController` (patient `exchange`).
- `src/ti_adcevm/lmk04828.py`: `LMK04828` with `read_register/write_register`, readback-mode tracking
  (any R0 write updates it), `enable_3wire_readback/enable_4wire_readback`, `reset()`, `read_status()`,
  `clear_pll_lock_lost()`, `parse_config()/load_config(verify=)`.
- `src/ti_adcevm/ads54j40.py`: `ADS54J40` with page table `PAGES`, `read_register/write_register(page, addr, ...)`,
  `read_many/write_many`, `reset()`, `initialize()`.

There are no unit tests in the repo. When changing bit-level code, test against a simulated SPI slave
that decodes the FTDI waveform (sync mode: sample before applying each output byte) before asking the
user to try hardware.
