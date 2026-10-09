"""Hook -> daemon channel: Claude Code hooks send events over a unix socket."""
import json
import socket
import sys
import time

from .config import SOCKET_PATH


def client_main():
    """Entry point of the Claude Code hook command: forward stdin JSON to the daemon."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(2)
        s.connect(str(SOCKET_PATH))
        s.sendall((json.dumps(payload) + "\n").encode())
        s.close()
    except OSError:
        pass  # daemon not running: never break Claude Code
    return 0


def last_assistant_text(transcript_path: str) -> str:
    """Final assistant text message of a Claude Code transcript (JSONL)."""
    for attempt in range(5):
        text = ""
        try:
            with open(transcript_path) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    parts = entry.get("message", {}).get("content", [])
                    if isinstance(parts, str):
                        t = parts
                    else:
                        t = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
                    if t.strip():
                        text = t.strip()
        except OSError:
            pass
        if text:
            return text
        time.sleep(0.3)
    return ""


def event_to_note(payload: dict) -> dict | None:
    """Translate a hook payload into {'kind', 'text'} or None to ignore."""
    ev = payload.get("hook_event_name")
    if ev == "Stop":
        text = last_assistant_text(payload.get("transcript_path", ""))
        return {"kind": "stop", "text": text or "Claude is waiting for your input."}
    if ev == "Notification":
        kind = payload.get("notification_type", "")
        if kind == "idle_prompt":
            return None  # already rang when Claude stopped
        return {"kind": kind or "notification", "text": payload.get("message", "Claude needs you.")}
    return None


def hooks_settings(command: str) -> dict:
    hook = [{"hooks": [{"type": "command", "command": command}]}]
    return {"hooks": {"Stop": hook, "Notification": hook}}
