#!/usr/bin/env python3
"""Upload an application image to an SHT43 DemoBoard over Bluetooth.

The DemoBoard always boots through Sensirion's OTA loader
(https://github.com/Sensirion/sht43-demoboard-ota), a port of ST's BLE_Ota
application (ST AN5247). This script implements the upload side of that
protocol, as read from the loader's source (otas_stm.c, otas_app.c,
app_ble.c, system_stm32wbxx.c).

Two steps:

1. Put the board into the loader. The running firmware reboots into the
   loader when the OTA reboot characteristic is written. That characteristic
   needs an authenticated pairing (numeric comparison on the board's LCD),
   which is easiest from a phone. See tools/ota/README.md. Use
   `--print-reboot-request` to get the bytes to write.

2. Upload (this script). The loader advertises as "SHT43_OTA" and its
   characteristics need no pairing.

Requires: Python 3.9+, `pip install bleak`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import struct
import sys
import time
from pathlib import Path

# Flash layout (sht43-demoboard-ota: CFG_APP_START_SECTOR_INDEX = 7;
# ble firmware: item store starts at page 65).
FLASH_BASE = 0x08000000
SECTOR_SIZE = 0x1000
APP_START_SECTOR = 7
APP_BASE = FLASH_BASE + APP_START_SECTOR * SECTOR_SIZE
FIRST_DATA_SECTOR = 65  # settings + data log; never erase these
RAM_START, RAM_END = 0x20000000, 0x20030000

# The loader checks *(*(APP_BASE + 0x140)) == MAGIC before jumping to the app.
MAGIC_POINTER_OFFSET = 0x140
MAGIC = 0x94448A29

OTA_NAME = "SHT43_OTA"
REBOOT_CHAR = "0000fe11-8e22-4541-9d4c-21edae82ed19"
OTA_SERVICE = "0000fe20-cc7a-482a-984a-7f2ed5b3e58f"
BASE_ADDR_CHAR = "0000fe22-8e22-4541-9d4c-21edae82ed19"
CONF_CHAR = "0000fe23-8e22-4541-9d4c-21edae82ed19"
RAW_DATA_CHAR = "0000fe24-8e22-4541-9d4c-21edae82ed19"

CMD_APPLICATION_UPLOAD = 0x02
CMD_UPLOAD_FINISHED = 0x07
REBOOT_ON_BLE_OTA_APP = 0x01
REBOOT_CONFIRMED = 0x01

MAX_CHUNK = 244  # raw data characteristic holds 248 bytes; MTU 247 -> 244


class ImageError(Exception):
    pass


def check_image(data: bytes) -> dict:
    """Sanity-check an application .bin linked at APP_BASE."""
    if len(data) < MAGIC_POINTER_OFFSET + 4:
        raise ImageError("file too small to be an application image")
    sp, reset = struct.unpack_from("<II", data, 0)
    if not RAM_START <= sp <= RAM_END:
        raise ImageError(f"initial stack pointer 0x{sp:08x} is not in RAM; "
                         "is this a .bin linked at 0x08007000?")
    if not (APP_BASE <= reset < APP_BASE + len(data)) or not reset & 1:
        raise ImageError(f"reset vector 0x{reset:08x} is outside the image; "
                         "is this a .bin linked at 0x08007000?")
    (magic_addr,) = struct.unpack_from("<I", data, MAGIC_POINTER_OFFSET)
    magic_off = magic_addr - APP_BASE
    if not 0 <= magic_off <= len(data) - 4:
        raise ImageError(f"OTA magic keyword pointer 0x{magic_addr:08x} is "
                         "outside the image; the loader would not start it")
    (magic,) = struct.unpack_from("<I", data, magic_off)
    if magic != MAGIC:
        raise ImageError(f"OTA magic keyword is 0x{magic:08x}, expected "
                         f"0x{MAGIC:08x}; the loader would not start it")
    return {"size": len(data), "sp": sp, "reset": reset,
            "magic_addr": magic_addr}


def sectors_to_erase(image_len: int) -> int:
    n = math.ceil(image_len / SECTOR_SIZE) + 1  # one spare
    if APP_START_SECTOR + n > FIRST_DATA_SECTOR:
        raise ImageError("image would overlap the settings/data-log pages")
    return n


def reboot_request(image_len: int) -> bytes:
    """3 bytes for the reboot characteristic: mode, first sector, count."""
    return bytes([REBOOT_ON_BLE_OTA_APP, APP_START_SECTOR,
                  sectors_to_erase(image_len + (-image_len) % 8)])


def base_addr_command(command: int, address: int = FLASH_BASE) -> bytes:
    """4 bytes for the base address characteristic.

    The loader rebuilds the address as FLASH_BASE with its low three bytes
    taken from bytes 1..3 of the payload, most significant first.
    """
    offset = address - FLASH_BASE
    if not 0 <= offset < 0x01000000:
        raise ValueError("address out of range")
    return bytes([command, (offset >> 16) & 0xFF, (offset >> 8) & 0xFF,
                  offset & 0xFF])


def chunks(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield i, data[i:i + size]


def upload_plan(data: bytes, magic_addr: int):
    """Split the image so the OTA keyword is the last thing written.

    The loader starts the app as soon as the keyword is in flash. In the
    linked image it sits ~4 KB before the end (.data initialisers follow it),
    so a sequential upload interrupted in that last stretch would leave a
    "valid" but incomplete app that crashes before it can be told to reboot
    into the loader again. Instead we send the part after the keyword first,
    then the part up to and including it. Each segment is padded to whole
    doublewords (the loader writes flash 8 bytes at a time and only flushes
    a partial doubleword at the very end), so restarting the base address
    between segments loses nothing.
    """
    padded = data + b"\xff" * ((-len(data)) % 8)
    magic_off = magic_addr - APP_BASE
    split = (magic_off + 4 + 7) // 8 * 8
    segments = [(split, padded[split:]), (0, padded[:split])]
    return padded, [(off, seg) for off, seg in segments if seg]


async def find_loader(address: str | None, timeout: float):
    from bleak import BleakScanner

    def match(device, adv):
        if address:
            return device.address.lower() == address.lower()
        return (adv.local_name or device.name or "") == OTA_NAME

    return await BleakScanner.find_device_by_filter(match, timeout=timeout)


async def upload(path: Path, address: str | None, delay_ms: float,
                 scan_timeout: float) -> int:
    from bleak import BleakClient

    data = path.read_bytes()
    info = check_image(data)
    print(f"Image: {path.name}, {info['size']} bytes, reset vector "
          f"0x{info['reset']:08x}, OTA keyword OK")
    print(f"SHA-256 {hashlib.sha256(data).hexdigest()}")

    print(f"Scanning for {address or OTA_NAME} ...")
    device = await find_loader(address, scan_timeout)
    if device is None:
        print(f"Not found. Is the board in loader mode (display blank, "
              f"advertising as {OTA_NAME})? See tools/ota/README.md.")
        return 2
    print(f"Found {device.name} [{device.address}]")

    done = asyncio.Event()
    confirmed = asyncio.Event()

    def on_disconnect(_client):
        done.set()

    def on_conf(_char, value: bytearray):
        if value and value[0] == REBOOT_CONFIRMED:
            confirmed.set()

    async with BleakClient(device, on_disconnect,
                           winrt={"use_cached_services": False}) as client:
        raw = client.services.get_characteristic(RAW_DATA_CHAR)
        if raw is None or client.services.get_characteristic(
                BASE_ADDR_CHAR) is None:
            print("Connected, but the OTA service is missing. If this is a "
                  "Mac, turn Bluetooth off and on to clear its cache, then "
                  "retry.")
            return 3
        size = max(20, min(MAX_CHUNK, raw.max_write_without_response_size))
        print(f"Chunk size {size} bytes")

        await client.start_notify(CONF_CHAR, on_conf)

        padded, plan = upload_plan(data, info["magic_addr"])
        start = time.monotonic()
        last_print = 0.0
        sent = 0
        for seg_off, segment in plan:
            await client.write_gatt_char(
                BASE_ADDR_CHAR,
                base_addr_command(CMD_APPLICATION_UPLOAD, APP_BASE + seg_off),
                response=False)
            await asyncio.sleep(0.2)
            for _, chunk in chunks(segment, size):
                await client.write_gatt_char(RAW_DATA_CHAR, chunk,
                                             response=False)
                if delay_ms:
                    await asyncio.sleep(delay_ms / 1000)
                sent += len(chunk)
                now = time.monotonic()
                if now - last_print > 0.5 or sent == len(padded):
                    last_print = now
                    print(f"\r  {100 * sent / len(padded):5.1f}%  "
                          f"{sent}/{len(padded)} bytes", end="", flush=True)
        print(f"\nSent in {time.monotonic() - start:.1f} s")

        await client.write_gatt_char(
            BASE_ADDR_CHAR, base_addr_command(CMD_UPLOAD_FINISHED),
            response=False)
        try:
            await asyncio.wait_for(confirmed.wait(), timeout=10)
        except asyncio.TimeoutError:
            print("No upload confirmation from the loader. The image is "
                  "probably incomplete; run the upload again.")
            return 4
        print("Loader confirmed; it now finishes writing and reboots into "
              "the new firmware.")
        try:
            await asyncio.wait_for(done.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass

    print("Done. The display should come back within a few seconds.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", type=Path, help="application .bin (not .hex)")
    ap.add_argument("--address", help="loader address (macOS: UUID); "
                    "default: first device named SHT43_OTA")
    ap.add_argument("--delay-ms", type=float, default=10.0,
                    help="pause between data packets (default 10)")
    ap.add_argument("--scan-timeout", type=float, default=20.0)
    ap.add_argument("--check", action="store_true",
                    help="only check the image and print the reboot request")
    ap.add_argument("--print-reboot-request", action="store_true",
                    help="print the hex to write to the reboot "
                    "characteristic, then exit")
    args = ap.parse_args(argv)

    data = args.image.read_bytes()
    try:
        check_image(data)
        req = reboot_request(len(data))
    except ImageError as e:
        print(f"Image check failed: {e}")
        return 1
    if args.print_reboot_request or args.check:
        print(f"Reboot request (write without response to {REBOOT_CHAR}): "
              f"{req.hex().upper()}")
        if args.check:
            print("Image check OK")
        return 0
    return asyncio.run(upload(args.image, args.address, args.delay_ms,
                              args.scan_timeout))


if __name__ == "__main__":
    sys.exit(main())
