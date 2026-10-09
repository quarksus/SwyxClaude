import json
import os
import tempfile
import unittest

import numpy as np

from p280bridge import notify, tts
from p280bridge.audio import capture_session, rms, to_16k, tone_pcm
from p280bridge.config import Config
from p280bridge.bridge import normalize
from p280bridge.hid import parse_input_report


class HidTests(unittest.TestCase):
    def test_hook(self):
        self.assertTrue(parse_input_report(bytes([1, 0x01]))["offhook"])
        self.assertFalse(parse_input_report(bytes([1, 0x00]))["offhook"])

    def test_buttons(self):
        r = parse_input_report(bytes([1, 0x02 | 0x10]))
        self.assertTrue(r["mute"])
        self.assertEqual(r["volume"], "up")
        self.assertEqual(parse_input_report(bytes([1, 0x30]))["volume"], "down")

    def test_other_report_ignored(self):
        self.assertIsNone(parse_input_report(bytes([5, 1])))


class NotifyTests(unittest.TestCase):
    def test_stop_uses_last_assistant_text(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "first"}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "x"}, {"type": "text", "text": "Which file?"}]}}) + "\n")
        note = notify.event_to_note({"hook_event_name": "Stop", "transcript_path": f.name})
        os.unlink(f.name)
        self.assertEqual(note, {"kind": "stop", "text": "Which file?"})

    def _write(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_transcript_ignores_previous_turn(self):
        """Stop fired before the new answer was flushed: must not return the old answer."""
        path = self._write([
            {"type": "user", "message": {"content": "first question"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "old answer"}]}},
            {"type": "user", "message": {"content": "second question"}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "..."}]}},
        ])
        self.assertEqual(notify.reply_since_last_prompt(path), "")

    def test_transcript_current_turn_after_tool_use(self):
        path = self._write([
            {"type": "user", "message": {"content": "q1"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "old"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "q2"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "let me look"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "new answer"}]}},
        ])
        self.assertEqual(notify.reply_since_last_prompt(path), "new answer")

    def test_stop_prefers_last_assistant_message(self):
        note = notify.event_to_note({"hook_event_name": "Stop", "last_assistant_message": "Fresh reply",
                                     "transcript_path": "/nonexistent"})
        self.assertEqual(note["text"], "Fresh reply")

    def test_idle_prompt_ignored(self):
        self.assertIsNone(notify.event_to_note(
            {"hook_event_name": "Notification", "notification_type": "idle_prompt"}))

    def test_permission_prompt(self):
        n = notify.event_to_note({"hook_event_name": "Notification",
                                  "notification_type": "permission_prompt", "message": "Allow Bash?"})
        self.assertEqual(n["kind"], "permission_prompt")


class FakeMic:
    """Stream object like UsbMic/PipeWireMic that plays back prepared PCM, then ends."""

    def __init__(self, pcm: bytes):
        self.pcm, self.pos = pcm, 0

    def open(self):
        pass

    def read(self, timeout):
        if self.pos >= len(self.pcm):
            return None
        chunk = self.pcm[self.pos:self.pos + 640]
        self.pos += 640
        return chunk

    def close(self):
        pass


class AudioTests(unittest.TestCase):
    def test_tone_length(self):
        self.assertEqual(len(tone_pcm([440, 660], seconds=0.1)), 2 * 2 * 1600)

    def test_to_16k(self):
        pcm = (np.sin(np.arange(22050) / 10) * 8000).astype(np.int16).tobytes()
        out = to_16k(pcm, 22050)
        self.assertAlmostEqual(len(out) / 2, 16000, delta=2)
        self.assertEqual(to_16k(pcm, 16000), pcm)

    def test_capture_session_splits_at_pauses_and_flushes_tail(self):
        loud = (np.sin(np.arange(16000) * 0.3) * 3000).astype(np.int16)  # 1 s of "speech"
        quiet = np.zeros(16000, dtype=np.int16)
        pcm = np.concatenate([quiet[:8000], loud, quiet, loud]).tobytes()  # speech, 1 s pause, speech, EOF
        chunks = []
        result = capture_session(FakeMic(pcm), Config(), lambda: False, chunks.append)
        self.assertEqual(result, "stopped")
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(len(c) >= 16000 for c in chunks))

    def test_capture_session_ignores_short_blips(self):
        blip = (np.sin(np.arange(1600) * 0.3) * 3000).astype(np.int16)  # 0.1 s
        pcm = np.concatenate([np.zeros(8000, dtype=np.int16), blip, np.zeros(32000, dtype=np.int16)]).tobytes()
        chunks = []
        capture_session(FakeMic(pcm), Config(), lambda: False, chunks.append)
        self.assertEqual(chunks, [])


class MiscTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("Ja!"), "ja")

    def test_german_detection(self):
        self.assertTrue(tts.looks_german("Soll ich die Datei für dich löschen?"))
        self.assertFalse(tts.looks_german("Should I delete the file?"))

    def test_clean_for_speech(self):
        out = tts.clean_for_speech("Use `ls`.\n```\nrm -rf /\n```\nSee https://x.io", 200)
        self.assertNotIn("rm -rf", out)
        self.assertNotIn("https", out)

    def test_rms(self):
        self.assertEqual(rms(np.zeros(480, dtype=np.int16).tobytes()), 0.0)
        self.assertGreater(rms((np.ones(480, dtype=np.int16) * 1000).tobytes()), 900)


if __name__ == "__main__":
    unittest.main()
