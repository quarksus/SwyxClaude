"""Swyx P280 HID interface: hook switch events and ring output.

Report layout (from the report descriptor, Telephony page):
  input report 1, 1 byte: bit0 hook switch (1 = off-hook), bit1 phone mute,
  bit2 speakerphone, bit3 vendor, bits4-5 volume (01 up / 11 down).
  output report 2, 1 byte of LEDs: bit0 ring, bit1 microphone, bit2 speaker, bit3 headset.
"""
import fcntl
import glob
import logging
import os
import select
import threading
import time

log = logging.getLogger(__name__)
VID, PID = 0x2603, 0x0280
LED_RING = 0x01


def find_hidraw() -> str | None:
    want = f"{VID:08X}:{PID:08X}"
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            if want in open(os.path.join(h, "device/uevent")).read().upper():
                return "/dev/" + os.path.basename(h)
        except OSError:
            pass
    return None


def parse_input_report(data: bytes) -> dict | None:
    if len(data) < 2 or data[0] != 1:
        return None
    b = data[1]
    vol = (b >> 4) & 3
    return {
        "offhook": bool(b & 0x01),
        "mute": bool(b & 0x02),
        "speaker": bool(b & 0x04),
        "volume": {1: "up", 3: "down"}.get(vol),
    }


def _hidiocgfeature(size: int) -> int:
    return (3 << 30) | (size << 16) | (ord("H") << 8) | 0x07


class Phone:
    """Reads the HID device in a thread; calls on_hook(offhook) and on_button(name)."""

    def __init__(self, on_hook, on_button=None, invert_hook=False):
        self.invert_hook = invert_hook
        self.on_hook = on_hook
        self.on_button = on_button or (lambda name: None)
        self.offhook = False
        self.fd = None
        self._thread = None
        self._stop = threading.Event()
        self._last = None

    def start(self):
        path = find_hidraw()
        if not path:
            raise RuntimeError("Swyx P280 not found (is it plugged in?)")
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise RuntimeError(
                f"No permission for {path}. Run `p280-bridge init` to set up access to the phone.")
        self._read_initial_state()
        self._thread = threading.Thread(target=self._run, daemon=True, name="hid")
        self._thread.start()
        log.info("P280 on %s, offhook=%s", path, self.offhook)

    def _read_initial_state(self):
        try:
            buf = bytearray([1, 0])
            fcntl.ioctl(self.fd, _hidiocgfeature(len(buf)), buf, True)
            rep = parse_input_report(bytes(buf))
            if rep:
                rep["offhook"] ^= self.invert_hook
                self.offhook = rep["offhook"]
                self._last = rep
        except OSError as e:
            log.debug("feature report read failed: %s", e)

    def _run(self):
        while not self._stop.is_set():
            try:
                if not select.select([self.fd], [], [], 0.5)[0]:
                    continue
                data = os.read(self.fd, 64)
            except OSError as e:
                log.error("HID read failed: %s", e)
                time.sleep(1)
                continue
            rep = parse_input_report(data)
            if not rep:
                continue
            rep["offhook"] ^= self.invert_hook
            last = self._last or {}
            self._last = rep
            if rep["offhook"] != self.offhook:
                self.offhook = rep["offhook"]
                self.on_hook(self.offhook)
            for name in ("mute", "speaker"):
                if rep[name] and not last.get(name):
                    self.on_button(name)
            if rep["volume"]:
                self.on_button("volume_" + rep["volume"])

    def set_leds(self, mask: int):
        os.write(self.fd, bytes([2, mask]))

    def ring(self, seconds: float):
        self.set_leds(LED_RING)
        time.sleep(seconds)
        self.set_leds(0)

    def close(self):
        self._stop.set()
        if self.fd is not None:
            try:
                self.set_leds(0)
            except OSError:
                pass
            os.close(self.fd)
