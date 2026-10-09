#!/usr/bin/env python3
"""Read the Swyx P280 microphone endpoint directly over USB (bypassing snd-usb-audio).

Shows what the phone really sends on isochronous endpoint 0x82: packet statuses, packet sizes
and signal level, for a few ways of starting the stream (with/without the sample-rate request,
with the playback interface active). Lift the handset and keep talking, then run:

    sudo /home/jskenderi/SwyxClaude/.venv/bin/python tools/usb-mic-probe.py
"""
import collections
import struct
import sys
import time

import usb1

EP_IN, IFACE_OUT, IFACE_IN = 0x82, 1, 2
PKT, NPKT, NXFER = 64, 10, 6


def set_rate(h, hz):
    # UAC1 SET_CUR sampling frequency control on the endpoint
    h.controlWrite(0x22, 0x01, 0x0100, EP_IN, struct.pack("<I", hz)[:3], 1000)


def capture(h, seconds=2.0):
    stats = collections.Counter()
    sizes = collections.Counter()
    data = bytearray()
    done = [False]

    def cb(t):
        if done[0]:
            return
        for status, buf in t.iterISO():
            stats[status] += 1
            sizes[len(buf)] += 1
            if status == 0:
                data.extend(buf)
        try:
            t.submit()
        except usb1.USBError:
            pass

    xfers = []
    for _ in range(NXFER):
        t = h.getTransfer(iso_packets=NPKT)
        t.setIsochronous(EP_IN, PKT * NPKT, callback=cb, timeout=2000)
        t.submit()
        xfers.append(t)
    end = time.time() + seconds
    while time.time() < end:
        h.getContext().handleEventsTimeout(0.1)
    done[0] = True
    for t in xfers:
        try:
            t.cancel()
        except usb1.USBError:
            pass
    for _ in range(5):
        h.getContext().handleEventsTimeout(0.05)
    names = {0: "OK", 1: "ERROR", 2: "TIMED_OUT", 3: "CANCELLED", 4: "STALL", 5: "NO_DEVICE", 6: "OVERFLOW"}
    st = ", ".join(f"{names.get(k, k)}={v}" for k, v in stats.items())
    sz = ", ".join(f"{k}B x{v}" for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])[:5])
    level = 0.0
    if len(data) >= 2:
        s = struct.unpack("<%dh" % (len(data) // 2), bytes(data[: len(data) // 2 * 2]))
        level = (sum(x * x for x in s) / len(s)) ** 0.5
    return f"status[{st}] sizes[{sz}] audio_bytes={len(data)} rms={level:.0f}"


def main():
    ctx = usb1.USBContext()
    h = ctx.openByVendorIDAndProductID(0x2603, 0x0280, skip_on_error=True)
    if h is None:
        sys.exit("P280 not found / no permission (run with sudo)")
    h.setAutoDetachKernelDriver(True)
    h.claimInterface(IFACE_IN)
    h.claimInterface(IFACE_OUT)
    try:
        modes = [
            ("alt1, no rate request", dict(rate=None, play=False)),
            ("alt1, SET_CUR 16000", dict(rate=16000, play=False)),
            ("alt1, SET_CUR 16000 + playback interface active", dict(rate=16000, play=True)),
            ("alt1, SET_CUR 8000", dict(rate=8000, play=False)),
            ("alt1, SET_CUR 48000", dict(rate=48000, play=False)),
        ]
        for label, m in modes:
            h.setInterfaceAltSetting(IFACE_IN, 0)
            h.setInterfaceAltSetting(IFACE_OUT, 1 if m["play"] else 0)
            time.sleep(0.3)
            h.setInterfaceAltSetting(IFACE_IN, 1)
            try:
                if m["rate"]:
                    set_rate(h, m["rate"])
            except usb1.USBError as e:
                print(f"  (rate request failed: {e})")
            print(f"{label}: {capture(h)}", flush=True)
        h.setInterfaceAltSetting(IFACE_IN, 0)
        h.setInterfaceAltSetting(IFACE_OUT, 0)
    finally:
        h.releaseInterface(IFACE_IN)
        h.releaseInterface(IFACE_OUT)
        h.close()
    print("Done. Replug the phone to restore normal audio.")


if __name__ == "__main__":
    main()
