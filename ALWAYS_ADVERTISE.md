# SHT43 DemoBoard firmware — always-advertise variant

Based on Sensirion/sht43-demoboard-ble-firmware `main` @ b277d61 (1.0.1-develop).
Goal: keep broadcasting temperature/RH in the advertisement indefinitely.

Root-cause analysis, evidence and tests:
[documentation/always_advertise_investigation.md](documentation/always_advertise_investigation.md)

## What can stop advertising in the stock firmware

| Cause | Stock behaviour | Visible sign |
|---|---|---|
| Battery < ~2.75 V (3 of last 4 readings) | Radio switched off automatically; never switched back on when the voltage recovers | BLE symbol gone; low-battery symbol may have cleared again |
| Battery < ~2.5 V | Radio off, long press can no longer re-enable it | Low-battery symbol blinking |
| `aci_gap_set_discoverable()` fails once | State set to ADVERTISING *before* the call, so the failure is never retried; data updates continue into a silent radio | BLE symbol still on, nothing on air |
| Unrecoverable error / hard fault | Halts until battery is re-inserted | `Err n` on display (hard fault: frozen display) |

## Changes

All switchable in `source/utility/AppDefines.h`:

- `APP_DEFINE_KEEP_BLE_ON_LOW_BATTERY 1` — no automatic radio-off at any
  battery level; long press can always re-enable. Long press still turns the
  radio off manually.
- `BleGap_AdvertiseRequest()` — only enters ADVERTISING when the controller
  accepted the start; on failure it stops/retries once, otherwise stays IDLE
  so the next sensor reading retries.
- `APP_DEFINE_ADVERTISE_REFRESH_INTERVAL_S 600` — stop/start a running
  advertisement every 10 min as a backstop.
- `APP_DEFINE_WATCHDOG_ENABLED 1` — IWDG (~32 s, LSI/256, reload 4095),
  reloaded on every application time tick (≤ 5 s). Any hang resets the
  device; an unrecoverable error shows `Err n` for ~30 s then reboots.
  A software reset does not wipe the data log (only a power-on reset does).

## Trade-offs

- Below ~2.5 V a coin cell can brown out under radio load. Expect resets near
  end of life; a brown-out reset clears the on-board log.
- If an error recurs on every boot (dead sensor, wrong CPU2 stack), the
  device reboots every ~35 s instead of halting.

## Build

Toolchain as in README (gcc-arm-none-eabi 10.3-2021.10). Same commands:

```bash
cmake -B build/release -DCMAKE_MAKE_PROGRAM=ninja -DCMAKE_BUILD_TYPE=release -G Ninja
cmake --build build/release
```

Output: `build/release/SHT43_DB-1_0_1-develop-alwayson.{hex,bin}`.

## Flash

The app is linked at 0x08007000 (OTA loader below it); the .hex carries the
address. Program with STM32CubeProgrammer over SWD (see README, *Setup wired
connection*). Built against the same CPU2 wireless stack as stock 1.0.x
(BLE full stack 1.20.x); a board running 1.0.x already has it.
