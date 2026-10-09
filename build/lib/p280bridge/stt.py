"""Local speech-to-text with faster-whisper."""
import logging
import threading

import numpy as np

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                log.info("loading whisper model %s", self.cfg.whisper_model)
                self._model = WhisperModel(self.cfg.whisper_model, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, samples: np.ndarray) -> tuple[str, str]:
        model = self.load()
        audio = samples.astype(np.float32) / 32768.0
        segments, info = model.transcribe(
            audio, language=self.cfg.language, beam_size=1, vad_filter=True,
            condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("transcribed (%s): %r", info.language, text)
        return text, info.language
