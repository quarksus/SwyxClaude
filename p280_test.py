#!/usr/bin/env python3
"""Small hardware test for the Swyx P280 USB phone: speaker and buttons/hook switch.

Usage:
    ./p280_test.py            # speaker test, then button test
    ./p280_test.py speaker
    ./p280_test.py buttons
"""
import glob
import math
import os
import select
import struct
import subprocess
import sys
import tempfile
import time
import wave

VID_PID = "2603:0280"  # Swyx P280 USB-Phone
SINK = "alsa_output.usb-Swyx_P280_USB-Phone-00.mono-fallback"
RATE = 16000


def write_tone(path, freqs, seconds=0.4):
    """Write a mono 16-bit WAV with consecutive sine tones."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        for f in freqs:
            n = int(RATE * seconds)
            frames = b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * f * i / RATE)
                                      * min(1, i / 200, (n - i) / 200)))
                for i in range(n)
            )
            w.writeframes(frames)


def test_speaker():
    print("== Speaker test ==")
    print("Playing a rising 3-tone beep (440/660/880 Hz) on the P280...")
    print("(Lift the handset to hear it in the earpiece; otherwise it may use the base speaker.)")
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "beep.wav")
        write_tone(wav, [440, 660, 880])
        # Under sudo, root cannot reach the user's PipeWire socket: play as the real user.
        prefix = []
        user = os.environ.get("SUDO_USER")
        if os.geteuid() == 0 and user:
            uid = int(os.environ.get("SUDO_UID", 1000))
            prefix = ["sudo", "-u", user, "env", f"XDG_RUNTIME_DIR=/run/user/{uid}"]
        r = subprocess.run(prefix + ["pw-play", "--target", SINK, wav])
        if r.returncode != 0:
            print("pw-play failed; trying paplay...")
            subprocess.run(prefix + ["paplay", f"--device={SINK}", wav])
    heard = input("Did you hear the beeps on the P280? [y/n] ").strip().lower()
    print("SPEAKER:", "PASS" if heard.startswith("y") else "FAIL")


def find_hidraw():
    vid, pid = VID_PID.split(":")
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = open(os.path.join(h, "device/uevent")).read().upper()
        if f"{int(vid, 16):08X}:{int(pid, 16):08X}" in uevent:
            return "/dev/" + os.path.basename(h)
    return None


def test_buttons(duration=30):
    print("== Button / hook switch test ==")
    dev = find_hidraw()
    if not dev:
        print("P280 HID device not found.")
        return
    print(f"Found {dev}")
    try:
        fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError:
        print(f"No permission to read {dev}. Fix once with:\n"
              f"  echo 'KERNEL==\"hidraw*\", ATTRS{{idVendor}}==\"2603\", ATTRS{{idProduct}}==\"0280\", "
              f"MODE=\"0660\", TAG+=\"uaccess\"' | sudo tee /etc/udev/rules.d/99-swyx-p280.rules\n"
              f"  sudo udevadm control --reload && sudo udevadm trigger\n"
              f"then replug the phone. Or run this script with sudo.")
        return
    print(f"Press buttons, lift/replace the handset. Listening for {duration}s (Ctrl+C to stop).")
    print("Raw HID reports are shown in hex, with the bytes that changed marked.\n")
    last = None
    seen = set()
    end = time.time() + duration
    try:
        while time.time() < end:
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            data = os.read(fd, 64)
            hexs = data.hex(" ")
            if last is not None and len(last) == len(data):
                changed = [i for i, (a, b) in enumerate(zip(last, data)) if a != b]
                note = f"  (changed bytes: {changed})" if changed else ""
            else:
                note = ""
            print(f"{time.strftime('%H:%M:%S')}  {hexs}{note}")
            seen.add(hexs)
            last = data
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)
    print(f"\nBUTTONS: {'PASS' if seen else 'FAIL'} - {len(seen)} distinct report(s) received")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "speaker"):
        test_speaker()
    if which in ("all", "buttons"):
        test_buttons()
