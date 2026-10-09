"""Settings: defaults, overridable via ~/.config/p280-bridge/config.toml."""
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "p280-bridge" / "config.toml"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "p280-bridge"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "p280-bridge"
SOCKET_PATH = RUNTIME_DIR / "notify.sock"


@dataclass
class Config:
    device_match: str = "Swyx_P280"        # substring of the PipeWire node names
    whisper_model: str = "small"           # tiny/base/small/medium/large-v3
    language: str | None = None            # None = auto-detect (de/en/...)
    auto_submit: bool = True               # press Enter after typing the transcript
    silence_seconds: float = 1.5           # end of utterance after this much silence
    max_utterance_seconds: float = 90.0
    min_rms: float = 250.0                 # lowest speech energy threshold (int16 RMS)
    ring_method: str = "auto"              # auto (HID, tone fallback) | hid | tone | both
    ring_seconds: float = 1.5
    speak_replies: bool = True             # read Claude's question/answer aloud after pickup
    max_spoken_chars: int = 700
    voice_permissions: bool = True         # answer permission dialogs by saying yes/no
    piper_voice_en: str = str(PROJECT_DIR / "models/piper/en_US-lessac-medium.onnx")
    piper_voice_de: str = str(PROJECT_DIR / "models/piper/de_DE-thorsten-medium.onnx")


def load() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        data = tomllib.loads(CONFIG_FILE.read_text())
        known = {f.name for f in fields(Config)}
        for k, v in data.items():
            if k in known:
                setattr(cfg, k, v)
    return cfg
