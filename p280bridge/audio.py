"""Audio I/O through PipeWire/PulseAudio CLI tools: playback, tones, utterance capture."""
import logging
import math
import os
import select
import struct
import subprocess
import tempfile
import wave

import numpy as np

log = logging.getLogger(__name__)
RATE = 16000
FRAME_MS = 30
FRAME_BYTES = RATE * 2 * FRAME_MS // 1000


def find_node(kind: str, match: str) -> str:
    """Return the PipeWire node name of the phone ('sources' or 'sinks')."""
    out = subprocess.run(["pactl", "list", "short", kind], capture_output=True, text=True).stdout
    for line in out.splitlines():
        name = line.split("\t")[1]
        if match in name and not name.endswith(".monitor"):
            return name
    raise RuntimeError(f"No {kind} matching {match!r} found")


def tone_wav(path: str, freqs, seconds=0.15, volume=0.4):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        for f in freqs:
            n = int(RATE * seconds)
            w.writeframes(b"".join(
                struct.pack("<h", int(32767 * volume * math.sin(2 * math.pi * f * i / RATE)
                                      * min(1, i / 160, (n - i) / 160)))
                for i in range(n)))


class Speaker:
    def __init__(self, sink: str):
        self.sink = sink
        self._proc = None

    def play(self, path: str, abort=lambda: False):
        """Play a wav file; returns False if aborted (e.g. phone put down)."""
        self._proc = subprocess.Popen(["pw-play", "--target", self.sink, path])
        while self._proc.poll() is None:
            if abort():
                self._proc.kill()
                self._proc.wait()
                return False
            try:
                self._proc.wait(0.1)
            except subprocess.TimeoutExpired:
                pass
        return True

    def tone(self, freqs, **kw):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.wav")
            tone_wav(p, freqs, **kw)
            self.play(p)


def rms(frame: bytes) -> float:
    a = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def record_utterance(source, cfg, should_stop, interrupt=lambda: False):
    """Capture one spoken utterance with an energy VAD.

    Returns (reason, samples): reason is 'speech', 'stopped' (phone hung up),
    or 'interrupt' (interrupt() fired before the user started talking).
    """
    proc = subprocess.Popen(
        ["parec", f"--device={source}", f"--rate={RATE}", "--channels=1",
         "--format=s16le", "--latency-msec=30"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    buf = b""
    frames: list[bytes] = []
    preroll: list[bytes] = []
    noise = []
    threshold = cfg.min_rms
    loud = 0
    speaking = False
    silent = 0
    try:
        while True:
            if should_stop():
                return "stopped", None
            if not speaking and interrupt():
                return "interrupt", None
            if not select.select([proc.stdout], [], [], 0.1)[0]:
                continue
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                return "stopped", None
            buf += chunk
            while len(buf) >= FRAME_BYTES:
                frame, buf = buf[:FRAME_BYTES], buf[FRAME_BYTES:]
                level = rms(frame)
                if len(noise) < 8:  # calibrate on the first ~240 ms
                    noise.append(level)
                    threshold = max(cfg.min_rms, 3 * float(np.mean(noise)))
                    preroll.append(frame)
                    continue
                if not speaking:
                    preroll.append(frame)
                    preroll = preroll[-12:]
                    loud = loud + 1 if level > threshold else 0
                    if loud >= 3:
                        speaking = True
                        frames = list(preroll)
                        silent = 0
                else:
                    frames.append(frame)
                    silent = silent + 1 if level < threshold * 0.6 else 0
                    if silent * FRAME_MS / 1000 >= cfg.silence_seconds or \
                            len(frames) * FRAME_MS / 1000 >= cfg.max_utterance_seconds:
                        return "speech", np.frombuffer(b"".join(frames), dtype=np.int16)
    finally:
        proc.kill()
        proc.wait()
