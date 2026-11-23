#!/usr/bin/env python3
"""Offline test of sht43_ota.py against a simulated loader.

The simulated loader ports the flash handling of sht43-demoboard-ota
(otas_app.c: OTAS_STM_Notification, app_ble.c: Delete_Sectors) so the
protocol framing, address encoding and partial-doubleword handling can be
checked without hardware. Run: python3 test_sht43_ota.py [image.bin]
"""

import asyncio
import struct
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sht43_ota as ota  # noqa: E402

FLASH_SIZE = 0x100000


class SimLoader:
    def __init__(self, reboot_request: bytes):
        self.flash = bytearray(b"\x00" * FLASH_SIZE)  # "old" content
        mode, first, count = reboot_request
        assert mode == ota.REBOOT_ON_BLE_OTA_APP
        assert first >= ota.APP_START_SECTOR
        for s in range(first, first + count):  # Delete_Sectors
            self.flash[s * 0x1000:(s + 1) * 0x1000] = b"\xff" * 0x1000
        self.erased = (first, first + count)
        self.base = None
        self.value = bytearray(8)
        self.index = 0
        self.rebooted = False
        self.conf_cb = None
        self.disc_cb = None
        self.fail_after = None  # raw packets before a simulated link loss
        self.raw_count = 0

    def _write_dw(self, addr, dw):
        off = addr - ota.FLASH_BASE
        assert off % 8 == 0, "flash write not doubleword aligned"
        sector = off // 0x1000
        assert self.erased[0] <= sector < self.erased[1], \
            f"write to sector {sector} that was not erased"
        assert self.flash[off:off + 8] == b"\xff" * 8, "write to non-erased"
        self.flash[off:off + 8] = dw

    def base_addr(self, payload: bytes):
        assert len(payload) == 4, "base address characteristic is 4 bytes"
        cmd = payload[0]
        if cmd == ota.CMD_APPLICATION_UPLOAD:
            self.base = (ota.FLASH_BASE & 0xFF000000) | (
                payload[1] << 16 | payload[2] << 8 | payload[3])
            self.index = 0
        elif cmd == ota.CMD_UPLOAD_FINISHED:
            # indication; the client confirms, which triggers CONF_EVENT
            self.conf_cb(None, bytearray([ota.REBOOT_CONFIRMED]))
            self.confirmation()

    def raw(self, data: bytes):
        assert 1 <= len(data) <= 248
        if self.fail_after is not None and self.raw_count >= self.fail_after:
            raise ConnectionError("simulated link loss")
        self.raw_count += 1
        count, left = 0, len(data)
        while left >= 8 - self.index:
            n = 8 - self.index
            self.value[self.index:8] = data[count:count + n]
            self._write_dw(self.base, bytes(self.value))
            self.base += 8
            left -= n
            count += n
            self.index = 0
        if left:
            self.value[self.index:self.index + left] = data[count:count + left]
            self.index += left

    def confirmation(self):
        if self.index:
            self.value[self.index:] = b"\xff" * (8 - self.index)
            self._write_dw(self.base, bytes(self.value))
        self.rebooted = True
        self.disc_cb(None)

    def app_valid(self):
        """Port of CheckFwAppValidity() in the loader's system_stm32wbxx.c."""
        app = ota.APP_BASE - ota.FLASH_BASE
        (ptr,) = struct.unpack_from("<I", self.flash, app + 0x140)
        last_user_flash_address = ota.FLASH_BASE + FLASH_SIZE - 4
        if ptr < ota.FLASH_BASE or ptr > last_user_flash_address:
            return False
        (magic,) = struct.unpack_from("<I", self.flash, ptr - ota.FLASH_BASE)
        return magic == ota.MAGIC


def install_fake_bleak(loader: SimLoader, chunk_size: int):
    class Char:
        max_write_without_response_size = chunk_size

    class Services:
        def get_characteristic(self, uuid):
            return Char()

    class Device:
        name = ota.OTA_NAME
        address = "AA:BB:CC:DD:EE:FF"

    class Adv:
        local_name = ota.OTA_NAME

    class BleakScanner:
        @staticmethod
        async def find_device_by_filter(f, timeout=10):
            d = Device()
            return d if f(d, Adv()) else None

    class BleakClient:
        def __init__(self, device, disconnected_callback=None, **kw):
            loader.disc_cb = disconnected_callback
            self.services = Services()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def start_notify(self, uuid, cb):
            assert uuid == ota.CONF_CHAR
            loader.conf_cb = cb

        async def write_gatt_char(self, uuid, data, response=None):
            assert response is False
            if uuid == ota.BASE_ADDR_CHAR:
                loader.base_addr(bytes(data))
            elif uuid == ota.RAW_DATA_CHAR:
                loader.raw(bytes(data))
            else:
                raise AssertionError(uuid)

    sys.modules["bleak"] = types.SimpleNamespace(
        BleakScanner=BleakScanner, BleakClient=BleakClient)


def run(image: Path, chunk_size: int) -> None:
    data = image.read_bytes()
    loader = SimLoader(ota.reboot_request(len(data)))
    install_fake_bleak(loader, chunk_size)
    rc = asyncio.run(ota.upload(image, None, 0, 1))
    print()
    assert rc == 0, rc
    assert loader.rebooted
    app = ota.APP_BASE - ota.FLASH_BASE
    assert loader.flash[app:app + len(data)] == data, "flash != image"
    pad = (-len(data)) % 8
    assert loader.flash[app + len(data):app + len(data) + pad] == b"\xff" * pad
    assert loader.app_valid(), "loader would not start the app"
    print(f"chunk {chunk_size}: OK")


def main():
    image = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    assert ota.base_addr_command(2, 0x08007000) == bytes([2, 0x00, 0x70, 0x00])
    assert ota.base_addr_command(7) == bytes([7, 0, 0, 0])
    if image is None:
        print("pass an image to run the upload simulation")
        return
    req = ota.reboot_request(image.stat().st_size)
    print(f"reboot request: {req.hex().upper()}")
    for size in (20, 23, 61, 244):  # odd sizes exercise partial doublewords
        run(image, size)
    # A link loss at any point must leave an app the loader will NOT start,
    # so the board stays in (or returns to) loader mode for a retry.
    data = image.read_bytes()
    size = 244
    total = sum(-(-len(seg) // size) for _, seg in
                ota.upload_plan(data, ota.check_image(data)["magic_addr"])[1])
    for cut in list(range(0, total, 7)) + [total - 1]:
        loader = SimLoader(ota.reboot_request(len(data)))
        loader.fail_after = cut
        install_fake_bleak(loader, size)
        try:
            asyncio.run(ota.upload(image, None, 0, 1))
            raise AssertionError("upload should have failed")
        except ConnectionError:
            pass
        assert not loader.app_valid(), f"app valid after cut at {cut}/{total}"
    print(f"\nlink loss at any of {total} packets leaves the app invalid: OK")
    # an image without the OTA keyword is rejected before anything is sent
    try:
        bad = bytearray(data)
        (ptr,) = struct.unpack_from("<I", bad, 0x140)
        bad[ptr - ota.APP_BASE] ^= 0xFF
        ota.check_image(bytes(bad))
        raise AssertionError("bad keyword accepted")
    except ota.ImageError as e:
        print(f"bad keyword rejected: {e}")
    print("all OK")


if __name__ == "__main__":
    main()
