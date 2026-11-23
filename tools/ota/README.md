# Flashing over Bluetooth (no programmer)

The DemoBoard always boots through Sensirion's OTA loader
([sht43-demoboard-ota](https://github.com/Sensirion/sht43-demoboard-ota)), a
port of ST's BLE_Ota (ST AN5247). The loader sits below the application at
0x08000000 and is never overwritten by an application update.

**Status: untested on hardware.** `sht43_ota.py` implements the loader's
protocol as read from its source. `test_sht43_ota.py` checks it against a
simulation of the loader's flash handling.

You need:

- a phone with a generic BLE app, e.g. nRF Connect (iOS/Android)
- a laptop with Bluetooth and Python 3.9+, with `pip install bleak`
- the application `.bin` (not the `.hex`), e.g.
  `SHT43_DB-1_0_1-develop-alwayson.bin`

## 1. Reboot the board into the loader (phone)

The reboot characteristic needs an authenticated pairing. A write without
response to it is silently ignored on an unpaired link, so pair first.

1. Make sure Bluetooth is on (Bluetooth symbol on the display; if not, hold
   the button for 3 s). Close the MyAmbience app so it doesn't grab the
   connection.
2. In nRF Connect, scan and connect to the board (default name `SHT43 DB`).
3. Pair. In the Device Information service, read **Firmware Revision
   String** (0x2A26). It needs pairing, so the phone shows a pairing request
   with a 6-digit code, and the board shows the same code on its display.
   Accept on the phone, then **press the board's button within 30 s**. If
   the phone was already paired, the read just returns a version like
   `1.0.0`. Note it down.
4. In service `e6686821-f5b0-417f-aa27-89d1a1ae5425`, write to characteristic
   `0000fe11-8e22-4541-9d4c-21edae82ed19` as a byte array, write without
   response (command):

   ```text
   010713
   ```

   This means: reboot into the loader (`01`), erase from sector 7 (`07`),
   19 sectors (`13`). For a different image, get the bytes with
   `python3 sht43_ota.py --print-reboot-request image.bin`.
5. The phone disconnects and the display should go blank. The board now
   advertises as **`SHT43_OTA`**. If the display does not change, the write
   was ignored: repeat step 3, then step 4.

The loader has already erased the old application at this point.

## 2. Upload (laptop)

Turn off Bluetooth on the phone, or close nRF Connect, so it doesn't
reconnect. Then:

```bash
pip install bleak
python3 sht43_ota.py SHT43_DB-1_0_1-develop-alwayson.bin
```

It finds `SHT43_OTA`, uploads (seconds to a couple of minutes, depending on
the packet size the laptop negotiates), and the board reboots into the new
firmware. The display shows the ID screen, then readings. Reading the
Firmware Revision String should now give `1.0.1-dev.r`.

Use `--address` if more than one board is in loader mode. On macOS the
address is a UUID, as shown by the scan.

## If something goes wrong

- **Upload interrupted or failed:** the board stays in loader mode. Run
  step 2 again. The script writes the OTA keyword last (see
  `upload_plan()`), so an interrupted upload never leaves an app the loader
  would try to start.
- **Do not remove the battery while in loader mode** unless you have to.
  On power-up without a valid app, the loader erases the whole application
  area, *including the board's settings and data log*, and waits for an
  upload. It is still recoverable with step 2.
- **"OTA service is missing" on a Mac:** macOS cached the application's
  services for that address. Turn Bluetooth off and on, then retry.
- **Going back to stock:** any application `.bin` linked at 0x08007000 with
  the OTA keyword can be sent the same way.

## Protocol summary (from the loader source)

| Item | Value |
|---|---|
| Reboot request (app side) | `0000fe11-8e22-4541-9d4c-21edae82ed19`, 3 bytes: mode, first sector, sector count |
| OTA service | `0000fe20-cc7a-482a-984a-7f2ed5b3e58f` |
| Base address | `0000fe22-…`, 4 bytes: command (`02` app upload, `07` finished), address bits 23..0 MSB first |
| Confirmation | `0000fe23-…`, indication `01` after "finished"; the client's confirmation triggers the final flush and reboot |
| Raw data | `0000fe24-…`, write without response, up to 248 bytes, written to flash 8 bytes at a time |
| App valid if | `*(*(0x08007140)) == 0x94448A29` |
