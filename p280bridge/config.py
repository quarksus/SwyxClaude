"""Settings: defaults, overridable via ~/.config/p280-bridge/config.toml."""
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "p280-bridge"
PIPER_DIR = DATA_DIR / "models" / "piper"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "p280-bridge" / "config.toml"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "p280-bridge"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "p280-bridge"
SOCKET_PATH = RUNTIME_DIR / "notify.sock"
INIT_MARKER = CONFIG_FILE.parent / "initialized"
VOICES = ("en_US-lessac-medium", "de_DE-thorsten-medium")


@dataclass
class Config:
    device_match: str = "Swyx_P280"        # substring of the PipeWire node names
    mic_match: str = ""                    # substring of the microphone node; empty = same as device_match
    whisper_model: str = "base"            # tiny/base/small/medium/large-v3 (small = more accurate, ~3x slower)
    language: str | None = None            # None = auto-detect (de/en/...)
    auto_submit: bool = True               # press Enter after typing the transcript
    silence_seconds: float = 0.8           # pause used to split long speech so it is transcribed while you talk
    idle_timeout_seconds: float = 10.0     # off-hook but silent this long = send what was said and end the call (0 = off)
    max_utterance_seconds: float = 60.0    # longest single chunk before it is split anyway
    min_rms: float = 120.0                 # lowest speech energy threshold (int16 RMS)
    ring_method: str = "auto"              # auto (HID, tone fallback) | hid | tone | both
    ring_seconds: float = 1.5
    speak_replies: bool = True             # read Claude's question/answer aloud after pickup
    max_spoken_chars: int = 700
    voice_permissions: bool = True         # answer permission dialogs by saying yes/no
    invert_hook: bool = False              # set by `init` if your phone reports hook state inverted
    piper_voice_en: str = str(PIPER_DIR / "en_US-lessac-medium.onnx")
    piper_voice_de: str = str(PIPER_DIR / "de_DE-thorsten-medium.onnx")


def load() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        data = tomllib.loads(CONFIG_FILE.read_text())
        known = {f.name for f in fields(Config)}
        for k, v in data.items():
            if k in known:
                setattr(cfg, k, v)
    return cfg


def is_initialized() -> bool:
    return INIT_MARKER.exists()


def save(**updates):
    """Merge key/values into config.toml (simple flat TOML writer)."""
    data = tomllib.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    data.update(updates)
    lines = []
    for k, v in data.items():
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, str):
            v = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(f"{k} = {v}")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text("\n".join(lines) + "\n")
