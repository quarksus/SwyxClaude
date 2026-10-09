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


def _is_user_prompt(entry: dict) -> bool:
    """A real user turn (not a tool result fed back to the model)."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, str):
        return bool(content.strip())
    return any(p.get("type") != "tool_result" for p in content if isinstance(p, dict))


def reply_since_last_prompt(transcript_path: str) -> str:
    """Text of the final assistant message of the *current* turn, or '' if not written yet."""
    text = ""
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if _is_user_prompt(entry):
                    text = ""  # new turn: anything before is the previous answer
                elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
                    parts = entry.get("message", {}).get("content", [])
                    t = parts if isinstance(parts, str) else \
                        " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
                    if t.strip():
                        text = t.strip()
    except OSError:
        pass
    return text


def last_assistant_text(transcript_path: str, attempts: int = 10) -> str:
    """Claude's reply for the turn that just ended.

    The Stop hook can fire before the transcript is flushed, so only accept text that comes
    after the latest user prompt, and retry briefly until it shows up.
    """
    for _ in range(attempts):
        text = reply_since_last_prompt(transcript_path)
        if text:
            return text
        time.sleep(0.3)
    return ""


def event_to_note(payload: dict) -> dict | None:
    """Translate a hook payload into {'kind', 'text'} or None to ignore."""
    ev = payload.get("hook_event_name")
    if ev == "Stop":
        # Claude Code passes the final message directly; the transcript is only a fallback.
        text = (payload.get("last_assistant_message") or "").strip() \
            or last_assistant_text(payload.get("transcript_path", ""))
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
