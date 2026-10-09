"""Orchestrator: pickup -> listen -> transcribe -> type into Claude; Claude waits -> ring."""
import json
import logging
import queue
import re
import socket
import threading

from . import audio, notify, tts
from .config import RUNTIME_DIR, SOCKET_PATH
from .hid import Phone
from .stt import Transcriber

log = logging.getLogger(__name__)
YES = {"yes", "yeah", "ja", "ok", "okay", "yes please", "ja bitte", "klar", "genau"}
NO = {"no", "nope", "nein", "stop", "abbrechen", "cancel"}


def normalize(text: str) -> str:
    return re.sub(r"[^a-zäöüß ]", "", text.lower()).strip()


class Bridge:
    def __init__(self, cfg, host):
        self.cfg = cfg
        self.host = host
        self.notes: queue.Queue = queue.Queue()
        self.wake = threading.Event()
        self.stt = Transcriber(cfg)
        self.phone = Phone(self._on_hook, self._on_button, cfg.invert_hook)
        self.source = audio.find_node("sources", cfg.mic_match or cfg.device_match)
        self.speaker = audio.Speaker(audio.find_node("sinks", cfg.device_match))
        self.awaiting_permission = False
        self._stop = threading.Event()

    # --- events -------------------------------------------------------------
    def _on_hook(self, offhook: bool):
        log.info("hook: %s", "off-hook" if offhook else "on-hook")
        self.wake.set()

    def _on_button(self, name: str):
        log.info("button: %s", name)

    def _serve_notifications(self):
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        SOCKET_PATH.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(SOCKET_PATH))
        srv.listen(8)
        while not self._stop.is_set():
            conn, _ = srv.accept()
            with conn:
                data = b""
                while chunk := conn.recv(65536):
                    data += chunk
            try:
                note = notify.event_to_note(json.loads(data))
            except Exception:
                log.exception("bad hook payload")
                continue
            if note:
                log.info("claude event: %s %r", note["kind"], note["text"][:80])
                self.notes.put(note)
                self.wake.set()

    # --- phone actions ------------------------------------------------------
    def ring(self):
        method = self.cfg.ring_method
        if method in ("auto", "hid", "both"):
            try:
                self.phone.ring(self.cfg.ring_seconds)
                if method != "both":
                    return
            except OSError as e:
                log.warning("HID ring failed (%s)", e)
                if method == "hid":
                    return
        self._ring_tone()

    def _ring_tone(self):
        for _ in range(2):
            self.speaker.tone([880, 660] * 3, seconds=0.12)
            self.stop_wait(0.25)

    def stop_wait(self, s):
        self._stop.wait(s)

    def say(self, text, lang=None):
        if not self.cfg.speak_replies:
            return
        try:
            tts.speak(text, self.cfg, self.speaker, lang, abort=lambda: not self.phone.offhook)
        except Exception:
            log.exception("TTS failed")

    # --- main loop ----------------------------------------------------------
    def run_forever(self):
        threading.Thread(target=self._serve_notifications, daemon=True, name="notify").start()
        threading.Thread(target=self.stt.load, daemon=True, name="whisper-preload").start()
        log.info("bridge ready")
        while not self._stop.is_set():
            self.wake.wait(0.5)
            self.wake.clear()
            if self.phone.offhook:
                self.session()
            elif not self.notes.empty():
                self.ring_for_pending()

    def ring_for_pending(self):
        """Ring once per batch of events; they stay queued until pickup."""
        if getattr(self, "_rang_for", 0) == self.notes.qsize():
            return
        self._rang_for = self.notes.qsize()
        self.ring()

    def pop_latest_note(self):
        note = None
        while True:
            try:
                note = self.notes.get_nowait()
            except queue.Empty:
                break
        self._rang_for = 0
        return note

    def session(self):
        log.info("session start")
        self.speaker.tone([660, 880])
        self.handle_note(self.pop_latest_note())
        while self.phone.offhook and not self._stop.is_set():
            reason, samples = audio.record_utterance(
                self.source, self.cfg, lambda: not self.phone.offhook,
                interrupt=lambda: not self.notes.empty())
            if reason == "interrupt":
                self.handle_note(self.pop_latest_note())
            elif reason == "speech":
                self.handle_speech(samples)
        log.info("session end")

    def handle_note(self, note):
        if not note:
            return
        self.awaiting_permission = note["kind"] == "permission_prompt"
        self.say(note["text"])

    def handle_speech(self, samples):
        try:
            text, lang = self.stt.transcribe(samples)
        except Exception:
            log.exception("transcription failed")
            self.speaker.tone([300, 200])
            return
        if not text:
            return
        if self.awaiting_permission and self.cfg.voice_permissions:
            self.awaiting_permission = False
            word = normalize(text)
            if word in YES:
                log.info("permission: yes")
                self.host.send(b"\r")
                self.speaker.tone([880])
                return
            if word in NO:
                log.info("permission: no")
                self.host.send(b"\x1b")
                self.speaker.tone([440])
                return
        self.awaiting_permission = False
        self.host.type_text(text, self.cfg.auto_submit)
        self.speaker.tone([880])

    def close(self):
        self._stop.set()
        self.phone.close()
        SOCKET_PATH.unlink(missing_ok=True)
