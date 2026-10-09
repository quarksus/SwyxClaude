# P280 Claude Bridge

Talk to Claude Code with a Swyx P280 USB phone.

1. **Pick up the handset** → a rising beep means it is listening. Speak your prompt.
2. After a short pause (0.8 s, up to 2 s for long prompts) the speech is transcribed locally (faster-whisper, German/English
   auto-detected) and **typed into Claude Code's prompt** (and submitted).
3. When Claude **stops or needs you** (finished answer / question / permission dialog) and the phone
   is on the hook, it **rings once**. Pick up: Claude's message is read aloud (Piper TTS), then you reply.
   If you are already on the phone, the message is read aloud immediately.

Claude Code runs inside the bridge (`p280-bridge run` wraps `claude` in a pty), so you see everything
in the same terminal. Hooks (`Stop`, `Notification`) are injected with `claude --settings`, so your
own Claude settings are not touched.

## Install

Requires Linux with PipeWire/PulseAudio, Python 3.11+, [pipx](https://pipx.pypa.io) and the `claude` CLI.

```sh
curl -fsSL https://raw.githubusercontent.com/quarksus/SwyxClaude/main/install.sh | sh
```

or `pipx install git+https://github.com/quarksus/SwyxClaude`.

## Use

```sh
p280-bridge run            # extra args go to claude, e.g. p280-bridge run --resume
```

The **first run starts a setup wizard** that checks the tools, finds the phone, installs the udev rule
(asks for sudo once), downloads the speech models (~700 MB), and tests speaker, hook switch, microphone
and ring. Re-run it any time with `p280-bridge init`.

Other commands: `monitor` (print hook/button events), `probe-ring`, `devices`.
Logs: `~/.local/state/p280-bridge/bridge.log` (`BRIDGE_DEBUG=1` for more).

## Configuration

Optional `~/.config/p280-bridge/config.toml`, e.g.:

```toml
whisper_model = "small"    # more accurate, ~3x slower than the default "base"
mic_match = "StreamCam"    # use another microphone (substring of its name) if the phone mic does not work
language = "de"            # skip auto-detection
auto_submit = false        # only type the text, press Enter yourself
max_silence_seconds = 3.0  # pause that ends a long prompt (short phrases end after silence_seconds = 0.8)
ring_method = "tone"       # auto | hid | tone | both
voice_permissions = false  # don't answer permission dialogs by voice
```

See `p280bridge/config.py` for all options.

## Notes

- Saying **yes/ja/ok** (or **no/nein**) right after a permission dialog rings approves (Enter) or rejects (Esc)
  it. Only exact words count; disable with `voice_permissions = false`.
- The P280 appears to deliver no microphone audio while on-hook, which is fine here (we only listen off-hook).
- The HID ring (output report 2, bit 0) depends on the phone firmware; if the phone doesn't physically
  ring, the wizard switches to `ring_method = "tone"` (or set it yourself) (plays a ring tone through the phone speaker).
- Development: `python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m unittest discover -s tests`.
