#!/usr/bin/env python3
"""Record 6 s from the Swyx P280 microphone straight over USB and transcribe it.

Lift the handset and say a sentence right after starting, e.g.:
    sudo /home/jskenderi/SwyxClaude/.venv/bin/python tools/usb-mic-record.py
Writes /tmp/p280-usb.wav (readable by everyone). Replug the phone afterwards.
"""
import os
import struct
import sys
import time
import wave

import usb1

EP_IN, IFACE_OUT, IFACE_IN = 0x82, 1, 2
PKT, NPKT, NXFER = 64, 10, 6
SECONDS = 6


def main():
    ctx = usb1.USBContext()
    h = ctx.openByVendorIDAndProductID(0x2603, 0x0280, skip_on_error=True)
    if h is None:
        sys.exit("P280 not found / no permission (run with sudo)")
    h.setAutoDetachKernelDriver(True)
    h.claimInterface(IFACE_IN)
    h.claimInterface(IFACE_OUT)
    data = bytearray()
    done = [False]

    def cb(t):
        if done[0]:
            return
        for status, buf in t.iterISO():
            if status == 0:
                data.extend(buf)
        try:
            t.submit()
        except usb1.USBError:
            pass

    try:
        h.setInterfaceAltSetting(IFACE_IN, 0)
        h.setInterfaceAltSetting(IFACE_OUT, 1)   # the phone only streams the mic while this is active
        time.sleep(0.3)
        h.setInterfaceAltSetting(IFACE_IN, 1)
        h.controlWrite(0x22, 0x01, 0x0100, EP_IN, struct.pack("<I", 16000)[:3], 1000)
        xfers = []
        for _ in range(NXFER):
            t = h.getTransfer(iso_packets=NPKT)
            t.setIsochronous(EP_IN, PKT * NPKT, callback=cb, timeout=2000)
            t.submit()
            xfers.append(t)
        print(f"Recording {SECONDS} s - speak now!", flush=True)
        end = time.time() + SECONDS
        while time.time() < end:
            ctx.handleEventsTimeout(0.1)
        done[0] = True
        for t in xfers:
            try:
                t.cancel()
            except usb1.USBError:
                pass
        for _ in range(5):
            ctx.handleEventsTimeout(0.05)
        h.setInterfaceAltSetting(IFACE_IN, 0)
        h.setInterfaceAltSetting(IFACE_OUT, 0)
    finally:
        h.releaseInterface(IFACE_IN)
        h.releaseInterface(IFACE_OUT)
        h.close()
    pcm = bytes(data[: len(data) // 2 * 2])
    with wave.open("/tmp/p280-usb.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    os.chmod("/tmp/p280-usb.wav", 0o644)
    s = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    rms = (sum(x * x for x in s) / max(1, len(s))) ** 0.5
    print(f"got {len(pcm)} bytes = {len(pcm) / 32000:.1f} s of audio, rms={rms:.0f}, peak={max(map(abs, s), default=0)}")
    print("saved /tmp/p280-usb.wav - replug the phone to restore normal audio.")


if __name__ == "__main__":
    main()
