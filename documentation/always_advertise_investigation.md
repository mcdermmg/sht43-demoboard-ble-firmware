# Why the SHT43 DemoBoard stops advertising: investigation notes

Status: **hypothesis, not yet confirmed on hardware** (2026-11-23).
Code references are to upstream `Sensirion/sht43-demoboard-ble-firmware`
at `b277d61` (1.0.1-develop), before any changes in this fork.

Evidence is tagged:

- **[code]**: read directly from the firmware source
- **[datasheet]**: from a vendor document
- **[report]**: observed by the user
- **[inference]**: reasoning from the above, not yet tested

## 1. Symptom

- [report] Temperature/RH advertisements stop after roughly two days.
- [report] The display keeps running for months on the same CR2032.

## 2. Summary

The leading hypothesis is the **low-battery radio cut-off latching off**:

1. The firmware measures the supply voltage every application tick.
2. If the filtered value falls below 2.75 V, it switches the radio off.
3. Nothing switches the radio back on when the voltage recovers. Only a
   long button press does.

A CR2032 sags under load, and more so when it is cold or part-used, so a
cell with plenty of charge left can trip this for a short time. Once the
radio is off, the load drops, the voltage recovers and the display carries
on for months. This fits both observations.

Other causes found in the code are listed in section 5. The quickest way to
tell them apart is the **Bluetooth symbol on the display** (section 6).

## 3. How the stock firmware switches the radio off

### 3.1 Measurement

- [code] `BatteryMonitor.c:223`: the supply voltage is read on every
  application time tick.
- [code] The tick is 1 s after a user interaction, 2 s after 30 s, and 5 s
  after 5 minutes without interaction (`Presentation.c:324`,
  `AppDefines.h`). In steady state that means one reading every 5 s.
- [code] `BatteryMonitor.c:228`: readings pause after BLE events on the
  application bus, other than a disconnect, and resume on the next
  disconnect. In practice that means while a pairing request is open.
- [inference] Readings are timed by the CPU1 RTC tick. Radio events are
  scheduled independently by CPU2, so the two are not synchronised.

### 3.2 Filtering

- [code] `BatteryMonitor.c:89`: the last 4 readings are kept.
- [code] `BatteryMonitor.c:272-273`: they are sorted and the result is the
  mean of indices 2 and 3, i.e. **the two highest of the four**. The
  comment calls this a median.
- [code] `BatteryMonitor.c:177`: nothing is published until 4 readings
  exist.
- [inference] For the result to fall below a threshold, at least **3 of the
  last 4 readings** must be below it. At a 5 s tick that is about 15-20 s of
  low readings. A single dip during a transmit cannot trip it.

### 3.3 Thresholds

- [code] `BatteryMonitor.c:71-72`:
  - above 2750 mV: `NO_RESTRICTION`
  - 2500 to 2750 mV: `REDUCED_OPERATION` (comment: "~25% remaining")
  - below 2500 mV: `CRITICAL` (comment: "~5% remaining")
- [code] There is no hysteresis. The state follows the filtered value both
  up and down.

### 3.4 Action

- [code] `BleContext.c:196`: `automaticBleOff` starts as `true`.
- [code] `BleContext.c:626-656`: on a battery state change to
  `REDUCED_OPERATION` or `CRITICAL`, if `automaticBleOff` is set (or the
  state is critical), `BLE_INTERFACE_MSG_ID_BLE_OFF` is published.
- [code] `BleContext.c:825` `SwitchBleOff()`: stops advertising and swaps in
  `BleDisabledStateCb`. The bridge then forwards only button and battery
  events, so sensor values no longer reach the radio.
- [code] `Presentation.c:389`: the display clears its Bluetooth symbol.

### 3.5 The latch

- [code] `BleDisabledStateCb` (`BleContext.c:545`) acts only on a long
  press. No battery event re-enables the radio.
- [code] When the voltage recovers, the battery state goes back to
  `NO_RESTRICTION`, but the radio stays off.
- [code] `BleContext.c:557`: a long press while the state is
  `REDUCED_OPERATION` also sets `automaticBleOff = false`. From then on, only
  `CRITICAL` switches the radio off again. The flag is in RAM and is lost
  when the battery is removed.
- [code] A long press while the state is back at `NO_RESTRICTION` turns the
  radio on but leaves `automaticBleOff` set, so the next dip turns it off
  again.
- [code] `BleContext.c:630`: once `CRITICAL` has been seen, battery state
  tracking stops and a long press can no longer turn the radio on.

### 3.6 Why the display is unaffected

- [code] The presentation state machine and sensor readout do not depend on
  the radio. Readings and the display update continue every tick.
- [code] `Presentation.c:62,371`: the low-battery symbol follows the
  *current* battery state. If the voltage recovers above 2.75 V, **the
  symbol goes away again**, while the Bluetooth symbol stays off.

## 4. Why a cell with charge left can trip it

- [datasheet] The radio module runs from 1.71-3.6 V. The board appears to
  run straight off the cell; the README supplies it on the `3V` pad with
  the battery removed.
- [datasheet] ST quotes about 5 mA radio transmit current for the STM32WB.
  A transmit lasts milliseconds, every 5-6 s in steady state.
- [inference] A CR2032's internal resistance is high and rises with age and
  low temperature, so its loaded voltage is noticeably below its open-circuit
  voltage.
- [inference] Because readings are not synchronised with transmits, three
  low readings out of four are unlikely to come from hitting transmit
  pulses. More likely:
  - the cell's loaded voltage sits near 2.75 V for a period (cold night,
    part-used cell)
  - the cell has not fully recovered between pulses
  - the reading coincides with CPU1's own wake-up and sensor measurement
    current
- [inference] With the radio off, the average load falls and the voltage
  recovers. The cell then looks healthy and the display keeps going.
- [inference] Nothing in the firmware records that the cut-off happened.
  Only the missing Bluetooth symbol shows it.

## 5. Other causes considered

| # | Cause | Evidence | Display when stopped |
|---|---|---|---|
| A | **Low-battery cut-off latch** (section 3) | [code] path confirmed; trigger [inference] | Bluetooth symbol **off**; low-battery symbol on or off |
| B | **Silent advertising after a failed start**: `BleGap.c:156` sets the state to ADVERTISING *before* `aci_gap_set_discoverable()` (`:167`). If that call fails, later requests with the same mode only update the advertising data, and nothing retries. | [code] path confirmed; how often the call fails is unknown | Bluetooth symbol **on**, readings normal, nothing on air |
| C | **Unrecoverable error**: `System.c:242-243` disables interrupts and loops forever | [code] | `Err n` on display |
| D | **Hard fault**: `InterruptHandler.c:49` loops forever | [code] | Display frozen |
| E | **Accidental long press** (>3 s) | [code] | Bluetooth symbol off |
| F | **A central stays connected**: no advertising while connected | [code] | Bluetooth symbol on |

Ruled out:

- **Time-based shut-off.** No timer in `source/` stops the radio. The timers
  are the readout tick, battery-symbol blink, button, sensor and pairing
  (30 s) timers. The advertising rate steps down after 30 s and 5 min but
  never stops. `FAST_ADV_TIMEOUT` and `INITIAL_ADV_TIMEOUT` in
  `BleInterface.c` are defined but unused.
- **Data log filling up.** The log is 32 flash pages × 4 KB, holding 8-byte
  items of 2 samples, logged every 10 minutes by default
  (`SettingsController.c:65`). It wraps after roughly 7 months, not days,
  and wrapping is handled by erasing a page.

## 6. Tests to confirm

In order of effort:

1. **Look at the display when it has stopped.**
   - Bluetooth symbol off, no `Err`: points to A (or E).
   - Bluetooth symbol on: points to B or F.
   - `Err n` or frozen display: points to C or D.
2. **Long press for about 3 s.** If the Bluetooth symbol and advertisements
   return, the radio had been switched off in software (A or E). Whether it
   then *stays* on depends on the battery state at the time of the press
   (section 3.5).
3. **Bench reproduction of the latch** (stock firmware, deterministic):
   1. Remove the cell and power the `3V` pad from a bench supply at 3.0 V.
   2. Wait more than 5 minutes so the tick settles at 5 s.
   3. Drop the supply to 2.60 V for at least 60 s. Expected: low-battery
      symbol on, Bluetooth symbol off, advertisements stop.
   4. Restore 3.0 V. Expected: low-battery symbol clears within about
      20-30 s, and **the Bluetooth symbol and advertisements stay off**.

   Repeating this with this fork's firmware should show advertisements
   continuing throughout. Optionally, add a series resistor of tens of ohms
   to imitate a coin cell's resistance and watch how the measured voltage
   behaves during transmits.
4. **Swap the coin cell** for a CR123A or 2×AA on the stock firmware
   (section 8). If advertising then runs for weeks, that supports A.
5. **Measure** the average current at the `3V` pad (PPK2, Joulescope) with
   the radio on and off, and the cell's loaded voltage where the device
   lives.

Cause B has no reliable trigger. Confirming it would need fault injection,
for example a debug build that forces `aci_gap_set_discoverable()` to fail
once.

## 7. Power budget (estimates, not measurements)

| Item | Figure | Basis |
|---|---|---|
| Sleep (STOP2, RTC on) | ~2 µA | [datasheet] STM32WB55 |
| Display (segment glass, internal driver, high-drive on) | possibly 10-20 µA | One ST forum report on the same module: about 20 µA vs 2-3 µA with the LCD off. The poster called it abnormal. |
| Advertising, every 5-6 s at 0 dBm | ~4-5 µA | Scaled from an ST CubeMX estimate quoted on ST's forum: 4 ms at 6.33 mA per event |
| Sensor readout every 5 s | not estimated | |

[inference] The display and advertising are probably in the same
microamp range, with the display likely the larger. Keeping the radio on
should shorten coin-cell life somewhat, but not by half. Only a measurement
will settle this.

Sources:

- STM32WB5MMG datasheet: <https://www.st.com/resource/en/datasheet/stm32wb5mmg.pdf>
- LCD current report: <https://community.st.com/stm32-mcus-wireless-35/stm32wb5mmg-lcd-module-abnormal-consumption-153650>
- Advertising estimate: <https://community.st.com/stm32-mcus-wireless-35/stm32cubemx-power-profiler-on-stm32wb-ble-configuration-is-not-averaged-correctly-3910>

## 8. Supply options

The supply must stay at or below 3.6 V, with no regulator assumed.

| Cell | Voltage | Typical capacity | Verdict |
|---|---|---|---|
| CR2032 (stock) | 3.0 V | ~220 mAh | high internal resistance |
| CR2477 | 3.0 V | ~1000 mAh | fine |
| CR123A | 3.0 V | ~1500 mAh | fine, low internal resistance |
| 2×AA alkaline | 3.0-3.2 V fresh | 2000+ mAh | fine |
| 2×AA lithium (Li-FeS2) | ~3.6 V fresh | | at the limit, avoid |
| Li-SOCl2 (ER14505) | 3.6 V nominal | | above 3.6 V open-circuit, avoid |
| Li-ion / LiPo | up to 4.2 V | | needs a regulator |

Connect any external cell to the `3V` and `GND` pads with the coin cell
removed. Never connect both. The battery percentage reported over BLE
assumes a coin-cell discharge curve and will be wrong for other chemistries.

## 9. What this fork changes

Each change addresses one row of section 5 and can be switched off in
`source/utility/AppDefines.h`. See `ALWAYS_ADVERTISE.md`.

| Commit | Addresses |
|---|---|
| Only enter advertising state when advertising actually started | B |
| Option to keep BLE on regardless of battery level (`APP_DEFINE_KEEP_BLE_ON_LOW_BATTERY`) | A |
| Periodically restart advertising (`APP_DEFINE_ADVERTISE_REFRESH_INTERVAL_S`) | B, and unknown silent stops |
| Independent watchdog (`APP_DEFINE_WATCHDOG_ENABLED`) | C, D |

E (long press) and F (connected central) are left as they are, deliberately.

## 10. Open questions

- What is the cell's loaded voltage where the device is kept, and does the
  stop line up with cold nights?
- Does the stop happen on a fresh cell, or only once a cell is part-used?
- Do the bench test (section 6, step 3) and the display check agree on A?
