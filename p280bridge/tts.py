"""Local text-to-speech with Piper (English + German voices)."""
import logging
import os
import re
import subprocess
import sys
import tempfile

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


def synthesize(text: str, cfg, lang: str | None, out_wav: str):
    german = (lang == "de") if lang else looks_german(text)
    voice = cfg.piper_voice_de if german else cfg.piper_voice_en
    if not os.path.exists(voice):
        raise RuntimeError(f"Piper voice missing: {voice} (run ./scripts/setup.sh)")
    subprocess.run([sys.executable, "-m", "piper", "-m", voice, "-f", out_wav],
                   input=text.encode(), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def speak(text: str, cfg, speaker, lang=None, abort=lambda: False):
    text = clean_for_speech(text, cfg.max_spoken_chars)
    if not text:
        return
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "speech.wav")
        synthesize(text, cfg, lang, wav)
        speaker.play(wav, abort)
