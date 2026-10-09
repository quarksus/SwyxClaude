#!/bin/sh
# Create the venv, install dependencies and download the TTS voices + Whisper model.
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
mkdir -p models/piper
.venv/bin/python -m piper.download_voices --data-dir models/piper en_US-lessac-medium de_DE-thorsten-medium
.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
echo "Setup complete. Run ./scripts/install-udev.sh once, then ./p280-bridge run"
