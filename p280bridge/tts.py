"""Local text-to-speech with Piper (English + German voices)."""
import logging
import os
import re
import threading

log = logging.getLogger(__name__)
GERMAN_HINTS = {"der", "die", "das", "und", "ist", "nicht", "ich", "sie", "es", "ein", "eine", "mit",
                "für", "auf", "den", "dem", "zu", "von", "wir", "du", "bitte", "soll", "ja", "nein"}


def looks_german(text: str) -> bool:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    if not words:
        return False
    return sum(w in GERMAN_HINTS for w in words) / len(words) > 0.12


def clean_for_speech(text: str, limit: int) -> str:
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"[*_#>|~]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " ... see the terminal for the rest."
    return text


_voices: dict = {}
_lock = threading.Lock()


def load_voice(path: str):
    """Load a Piper voice once and keep it in memory (loading is the slow part)."""
    with _lock:
        if path not in _voices:
            if not os.path.exists(path):
                raise RuntimeError(f"Piper voice missing: {path} (run `p280-bridge init`)")
            from piper import PiperVoice
            _voices[path] = PiperVoice.load(path)
        return _voices[path]


def preload(cfg):
    for path in (cfg.piper_voice_en, cfg.piper_voice_de):
        try:
            load_voice(path)
        except Exception:
            log.exception("could not preload voice %s", path)


def speak(text: str, cfg, speaker, lang=None, abort=lambda: False):
    """Synthesize sentence by sentence and start playing as soon as the first one is ready."""
    text = clean_for_speech(text, cfg.max_spoken_chars)
    if not text:
        return
    german = (lang == "de") if lang else looks_german(text)
    voice = load_voice(cfg.piper_voice_de if german else cfg.piper_voice_en)
    chunks = voice.synthesize(text)
    speaker.play_stream(((c.audio_int16_bytes, c.sample_rate) for c in chunks), abort)
