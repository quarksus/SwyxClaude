#!/bin/bash
# Tries snd-usb-audio quirk flags for the Swyx P280 until its microphone delivers audio.
# Run with the handset LIFTED and keep talking:  sudo ./tools/mic-quirk-probe.sh
set -u
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
PARAM=/sys/module/snd_usb_audio/parameters/quirk_flags
USER_NAME=${SUDO_USER:-root}
dev=$(grep -l '^2603$' /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1 | xargs -r dirname)
[ -n "$dev" ] || { echo "P280 not found"; exit 1; }

try() {
  flags="$1"
  echo "=== flags: ${flags:-<none>}"
  echo "${flags:+2603:0280:$flags}" > "$PARAM" 2>/dev/null || { echo "  (rejected)"; return 1; }
  echo 0 > "$dev/authorized"; sleep 1; echo 1 > "$dev/authorized"
  for i in 1 2 3 4 5 6 7 8 9 10; do grep -q P280 /proc/asound/cards && break; sleep 1; done
  sleep 2
  rm -f /tmp/mic-probe.wav
  timeout 8 arecord -q -D plughw:CARD=USBPhone,DEV=0 -f S16_LE -r 16000 -c 1 -d 2 /tmp/mic-probe.wav 2>/tmp/mic-probe.err
  size=$(stat -c %s /tmp/mic-probe.wav 2>/dev/null || echo 0)
  echo "  recorded bytes: $size  $(tr '\n' ' ' < /tmp/mic-probe.err | cut -c1-80)"
  journalctl -k --no-pager -n 6 | grep -iE "usb 1-|snd-usb" | tail -2 | sed 's/^/  kernel: /'
  [ "$size" -gt 20000 ]
}

for f in "get_sample_rate" "" "ignore_ctl_error" "get_sample_rate+ignore_ctl_error" "playback_first" \
         "playback_first+get_sample_rate" "set_iface_first" "force_iface_reset" "disable_autosuspend" \
         "fixed_rate" "ctl_msg_delay" "get_sample_rate+set_iface_first+force_iface_reset"; do
  if try "$f"; then
    echo; echo ">>> WORKS with flags: ${f:-<none>}"; echo "$f" > /tmp/mic-probe.result; exit 0
  fi
done
echo; echo ">>> No flag combination made the microphone work."
exit 2
