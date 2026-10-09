"""Audio I/O through PipeWire/PulseAudio CLI tools: playback, tones, utterance capture."""
import logging
import math
import os
import select
import struct
import subprocess
import tempfile
import time
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


def list_sources() -> list[str]:
    """Names of all real (non-monitor) capture nodes."""
    out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
    return [l.split("\t")[1] for l in out.splitlines() if not l.split("\t")[1].endswith(".monitor")]


def tone_pcm(freqs, seconds=0.15, volume=0.4) -> bytes:
    """Consecutive sine tones as 16 kHz mono s16 PCM (with short fades to avoid clicks)."""
    n = int(RATE * seconds)
    i = np.arange(n)
    fade = np.minimum(1.0, np.minimum(i / 160, (n - i) / 160))
    parts = [(32767 * volume * np.sin(2 * math.pi * f * i / RATE) * fade).astype(np.int16) for f in freqs]
    return np.concatenate(parts).tobytes() if parts else b""


def tone_wav(path: str, freqs, seconds=0.15, volume=0.4):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(tone_pcm(freqs, seconds, volume))


def to_16k(pcm: bytes, rate: int) -> bytes:
    """Resample mono s16 PCM to 16 kHz (linear interpolation; fine for speech)."""
    if rate == RATE or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(len(x) * RATE / rate)
    pos = np.arange(n_out) * (rate / RATE)
    return np.interp(pos, np.arange(len(x)), x).astype(np.int16).tobytes()


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

    def play_stream(self, chunks, abort=lambda: False):
        """Play (pcm_bytes, sample_rate) chunks as they are produced; False if aborted."""
        proc = None
        try:
            for data, rate in chunks:
                if abort():
                    return False
                if proc is None:
                    proc = subprocess.Popen(
                        ["pw-play", "--raw", "--target", self.sink, "--rate", str(rate), "--channels", "1",
                         "--format", "s16", "-"], stdin=subprocess.PIPE)
                    self._proc = proc
                try:
                    proc.stdin.write(data)
                    proc.stdin.flush()
                except BrokenPipeError:
                    return False
            if proc is None:
                return True
            proc.stdin.close()
            while proc.poll() is None:
                if abort():
                    return False
                try:
                    proc.wait(0.1)
                except subprocess.TimeoutExpired:
                    pass
            return True
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()

    def tone(self, freqs, **kw):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.wav")
            tone_wav(p, freqs, **kw)
            self.play(p)


def rms(frame: bytes) -> float:
    a = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


MIN_VOICED_MS = 300  # ignore clicks/blips shorter than this


class PipeWireMic:
    """Stream object for capture_session backed by `parec`."""

    def __init__(self, node: str):
        self.node = node
        self.proc = None

    def open(self):
        self.proc = subprocess.Popen(
            ["parec", f"--device={self.node}", f"--rate={RATE}", "--channels=1",
             "--format=s16le", "--latency-msec=30"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    def read(self, timeout: float):
        """Bytes read ('' if none yet), or None at end of stream."""
        if not select.select([self.proc.stdout], [], [], timeout)[0]:
            return b""
        return os.read(self.proc.stdout.fileno(), 4096) or None

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()


class AudioIO:
    """The phone's microphone source and speaker, plus a close() for the chosen backend."""

    def __init__(self, mic, speaker, close=lambda: None):
        self.mic, self.speaker, self.close = mic, speaker, close


def open_audio(cfg) -> AudioIO:
    """PipeWire (default) or direct USB (cfg.audio_backend == "usb") audio for the phone."""
    if cfg.audio_backend == "usb":
        from .usbaudio import UsbPhoneAudio, UsbSpeaker
        usb = UsbPhoneAudio()
        usb.start()
        mic = cfg.mic_match and find_node("sources", cfg.mic_match) or usb.mic()
        return AudioIO(mic, UsbSpeaker(usb), usb.close)
    return AudioIO(find_node("sources", cfg.mic_match or cfg.device_match),
                   Speaker(find_node("sinks", cfg.device_match)))


def capture_session(source, cfg, should_stop, on_chunk, stats=None, idle_timeout=None):
    """Listen on one continuous capture stream until should_stop() is true.

    Speech is cut into chunks at pauses (cfg.silence_seconds) and handed to on_chunk(samples)
    as int16 arrays, so transcription can run while the user is still talking. Whatever is
    still being said when should_stop() fires is flushed as a last chunk.
    stats (optional dict) receives the peak level and speech threshold for diagnostics.
    If idle_timeout (seconds) is set and nobody speaks for that long, the last chunk is flushed
    and "timeout" is returned; otherwise returns "stopped".
    """
    stream = PipeWireMic(source) if isinstance(source, str) else source
    stream.open()
    buf = b""
    frames: list[bytes] = []
    preroll: list[bytes] = []
    noise: list[float] = []
    threshold = cfg.min_rms
    loud = voiced = silent = 0
    speaking = False
    last_voice = time.monotonic()

    def flush():
        nonlocal frames, speaking, loud, voiced, silent
        if speaking and voiced * FRAME_MS >= MIN_VOICED_MS:
            on_chunk(np.frombuffer(b"".join(frames), dtype=np.int16))
        frames, speaking, loud, voiced, silent = [], False, 0, 0, 0
        preroll.clear()

    try:
        while not should_stop():
            if idle_timeout and time.monotonic() - last_voice > idle_timeout:
                flush()
                return "timeout"
            chunk = stream.read(0.1)
            if chunk is None:
                break
            if not chunk:
                continue
            buf += chunk
            while len(buf) >= FRAME_BYTES:
                frame, buf = buf[:FRAME_BYTES], buf[FRAME_BYTES:]
                level = rms(frame)
                if level > threshold:
                    last_voice = time.monotonic()
                if stats is not None:
                    stats["peak"] = max(stats.get("peak", 0.0), level)
                    stats["threshold"] = threshold
                if len(noise) < 8:  # calibrate on the first ~240 ms (median ignores a stray click)
                    noise.append(level)
                    threshold = max(cfg.min_rms, 2.5 * float(np.median(noise)))
                    preroll.append(frame)
                    continue
                if not speaking:
                    preroll.append(frame)
                    del preroll[:-12]
                    loud = loud + 1 if level > threshold else 0
                    if loud >= 3:
                        speaking, frames, silent, voiced = True, list(preroll), 0, loud
                    continue
                frames.append(frame)
                if level < threshold * 0.6:
                    silent += 1
                else:
                    silent, voiced = 0, voiced + 1
                if silent * FRAME_MS / 1000 >= cfg.silence_seconds or \
                        len(frames) * FRAME_MS / 1000 >= cfg.max_utterance_seconds:
                    flush()
        flush()
        return "stopped"
    finally:
        stream.close()
