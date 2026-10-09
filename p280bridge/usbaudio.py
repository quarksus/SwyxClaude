"""Direct-USB audio for the Swyx P280 (bypasses the Linux snd-usb-audio driver).

Why: the P280 only streams its microphone while the playback interface is active, and it then
sends 64-byte isochronous packets that the kernel driver has sized too small, so every capture
frame fails with -EOVERFLOW. Reading the endpoints ourselves with libusb works.

While this is active the phone's ALSA/PipeWire sound card is gone (the kernel driver is detached);
speaker and microphone are served from here. Format: 16 kHz, mono, signed 16-bit.
"""
import atexit
import logging
import struct
import threading
import time
import wave

import numpy as np

from .audio import RATE, to_16k, tone_pcm

log = logging.getLogger(__name__)
VID, PID = 0x2603, 0x0280
EP_IN, EP_OUT = 0x82, 0x01
IF_OUT, IF_IN = 1, 2
IN_PKT, IN_NPKT, IN_NXFER = 64, 10, 6
OUT_PKT, OUT_NPKT, OUT_NXFER = 32, 10, 6          # 32 bytes = 1 ms of 16 kHz mono s16
OUT_LATENCY_S = OUT_NPKT * OUT_NXFER / 1000


class UsbPhoneAudio:
    def __init__(self):
        self._ctx = None
        self._h = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._mic_buf = bytearray()
        self._capturing = False
        self._out = bytearray()
        self._xfers = []

    # --- lifecycle ----------------------------------------------------------
    def start(self):
        import usb1
        self._usb1 = usb1
        self._ctx = usb1.USBContext()
        self._h = self._ctx.openByVendorIDAndProductID(VID, PID, skip_on_error=True)
        if self._h is None:
            raise RuntimeError("Cannot open the P280 over USB (not plugged in, or no permission: "
                               "run `p280-bridge init` to install the access rule).")
        h = self._h
        h.setAutoDetachKernelDriver(True)
        h.claimInterface(IF_IN)
        h.claimInterface(IF_OUT)
        h.setInterfaceAltSetting(IF_IN, 0)
        h.setInterfaceAltSetting(IF_OUT, 0)
        time.sleep(0.2)
        h.setInterfaceAltSetting(IF_OUT, 1)   # the mic only streams while playback is active
        h.setInterfaceAltSetting(IF_IN, 1)
        for ep in (EP_IN, EP_OUT):
            try:
                h.controlWrite(0x22, 0x01, 0x0100, ep, struct.pack("<I", RATE)[:3], 1000)
            except usb1.USBError as e:
                log.warning("sample-rate request for ep %#x failed: %s", ep, e)
        self._running = True
        for _ in range(IN_NXFER):
            t = h.getTransfer(iso_packets=IN_NPKT)
            t.setIsochronous(EP_IN, IN_PKT * IN_NPKT, callback=self._on_in, timeout=2000)
            t.submit()
            self._xfers.append(t)
        for _ in range(OUT_NXFER):
            t = h.getTransfer(iso_packets=OUT_NPKT)
            t.setIsochronous(EP_OUT, bytes(OUT_PKT * OUT_NPKT), callback=self._on_out, timeout=2000)
            t.submit()
            self._xfers.append(t)
        self._thread = threading.Thread(target=self._events, daemon=True, name="usb-audio")
        self._thread.start()
        atexit.register(self.close)
        log.info("USB audio started")

    def _events(self):
        while self._running:
            try:
                self._ctx.handleEventsTimeout(0.1)
            except self._usb1.USBError:
                if self._running:
                    log.exception("USB event loop error")
                    time.sleep(0.2)

    def close(self):
        if self._h is None:
            return
        self._running = False
        if self._thread:
            self._thread.join(1)
        for t in self._xfers:
            try:
                t.cancel()
            except self._usb1.USBError:
                pass
        for _ in range(10):
            try:
                self._ctx.handleEventsTimeout(0.05)
            except self._usb1.USBError:
                break
        h, self._h = self._h, None
        for fn in (lambda: h.setInterfaceAltSetting(IF_IN, 0), lambda: h.setInterfaceAltSetting(IF_OUT, 0),
                   lambda: h.releaseInterface(IF_IN), lambda: h.releaseInterface(IF_OUT), h.close):
            try:
                fn()
            except self._usb1.USBError:
                pass  # re-attaches snd-usb-audio, so the normal sound card comes back
        log.info("USB audio stopped")

    # --- transfers (run in the event thread) --------------------------------
    def _on_in(self, t):
        if not self._running:
            return
        with self._cond:
            for status, buf in t.iterISO():
                if status == 0 and buf and self._capturing:
                    self._mic_buf.extend(buf)
            self._cond.notify_all()
        try:
            t.submit()
        except self._usb1.USBError:
            pass

    def _on_out(self, t):
        if not self._running:
            return
        n = OUT_PKT * OUT_NPKT
        with self._lock:
            chunk = bytes(self._out[:n])
            del self._out[:n]
        t.setIsochronous(EP_OUT, chunk.ljust(n, b"\0"), callback=self._on_out, timeout=2000)
        try:
            t.submit()
        except self._usb1.USBError:
            pass

    # --- microphone ---------------------------------------------------------
    def mic(self):
        return UsbMic(self)

    def _read_mic(self, timeout: float):
        with self._cond:
            if not self._mic_buf:
                self._cond.wait(timeout)
            data = bytes(self._mic_buf)
            self._mic_buf.clear()
        return data

    # --- speaker ------------------------------------------------------------
    def enqueue(self, pcm16k: bytes):
        with self._lock:
            self._out.extend(pcm16k)

    def pending(self) -> int:
        with self._lock:
            return len(self._out)

    def clear_out(self):
        with self._lock:
            self._out.clear()


class UsbMic:
    """Stream object for audio.capture_session: open()/read()/close()."""

    def __init__(self, usb: UsbPhoneAudio):
        self.usb = usb

    def open(self):
        with self.usb._cond:
            self.usb._mic_buf.clear()
            self.usb._capturing = True

    def read(self, timeout: float):
        return self.usb._read_mic(timeout)

    def close(self):
        self.usb._capturing = False


class UsbSpeaker:
    """Same interface as audio.Speaker, played through the P280's USB output."""

    def __init__(self, usb: UsbPhoneAudio):
        self.usb = usb

    def _drain(self, abort) -> bool:
        while self.usb.pending() > 0:
            if abort():
                self.usb.clear_out()
                return False
            time.sleep(0.02)
        time.sleep(OUT_LATENCY_S + 0.05)  # let what is already in the USB queue finish
        return True

    def play_pcm(self, pcm16k: bytes, abort=lambda: False) -> bool:
        self.usb.enqueue(pcm16k)
        return self._drain(abort)

    def play(self, path: str, abort=lambda: False) -> bool:
        with wave.open(path) as w:
            data, rate = w.readframes(w.getnframes()), w.getframerate()
        return self.play_pcm(to_16k(data, rate), abort)

    def play_stream(self, chunks, abort=lambda: False) -> bool:
        for data, rate in chunks:
            if abort():
                self.usb.clear_out()
                return False
            self.usb.enqueue(to_16k(data, rate))
        return self._drain(abort)

    def tone(self, freqs, **kw):
        self.play_pcm(tone_pcm(freqs, **kw))
