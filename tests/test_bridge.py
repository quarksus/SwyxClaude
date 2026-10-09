import json
import os
import tempfile
import unittest

import numpy as np

from p280bridge import notify, tts
from p280bridge.audio import rms
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

    def test_idle_prompt_ignored(self):
        self.assertIsNone(notify.event_to_note(
            {"hook_event_name": "Notification", "notification_type": "idle_prompt"}))

    def test_permission_prompt(self):
        n = notify.event_to_note({"hook_event_name": "Notification",
                                  "notification_type": "permission_prompt", "message": "Allow Bash?"})
        self.assertEqual(n["kind"], "permission_prompt")


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
