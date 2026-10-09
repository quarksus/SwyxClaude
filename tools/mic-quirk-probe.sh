#!/bin/bash
# Tries snd-usb-audio quirk flags for the Swyx P280 until its microphone delivers audio.
# Run with the handset LIFTED and keep talking:  sudo ./tools/mic-quirk-probe.sh
# For every attempt the phone is software-replugged (its USB path is looked up again each time,
# because it can change) so the flags are really applied when the driver probes the device.
set -u
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
PARAM=/sys/module/snd_usb_audio/parameters/quirk_flags
DYN=/sys/kernel/debug/dynamic_debug/control

find_dev() { grep -l '^2603$' /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1 | xargs -r dirname; }
card_present() { grep -q P280 /proc/asound/cards; }

[ -w "$DYN" ] && echo "module snd_usb_audio +p" > "$DYN" && echo "(kernel debug output for snd-usb-audio enabled)"

replug() {
  local dev; dev=$(find_dev)
  [ -n "$dev" ] || { echo "  P280 not found - is it plugged in?"; return 1; }
  echo 0 > "$dev/authorized"; sleep 2
  dev=$(find_dev)   # path may have changed
  [ -n "$dev" ] && echo 1 > "$dev/authorized"
  for _ in $(seq 1 15); do card_present && break; sleep 1; done
  card_present && sleep 2
}

try() {
  flags="$1"
  echo "=== flags: ${flags:-<none>}"
  echo "${flags:+2603:0280:$flags}" > "$PARAM" 2>/dev/null || { echo "  (rejected by kernel)"; return 1; }
  echo "  parameter now: $(head -c 60 $PARAM | head -1)"
  mark=$(date '+%Y-%m-%d %H:%M:%S')
  replug || return 1
  card_present || { echo "  card did not come back"; return 1; }
  rm -f /tmp/mic-probe.wav
  timeout 8 arecord -q -D plughw:CARD=USBPhone,DEV=0 -f S16_LE -r 16000 -c 1 -d 2 /tmp/mic-probe.wav 2>/tmp/mic-probe.err
  size=$(stat -c %s /tmp/mic-probe.wav 2>/dev/null || echo 0)
  echo "  recorded bytes: $size  $(tr '\n' ' ' < /tmp/mic-probe.err | cut -c1-80)"
  if journalctl -k --no-pager --since "$mark" | grep -q "cannot get freq"; then
    echo "  note: 'cannot get freq' still logged -> flag NOT effective"
  else
    echo "  note: no 'cannot get freq' after replug -> flag was applied"
  fi
  journalctl -k --no-pager --since "$mark" | grep -iE "active: -|overflow|babble|error|fail|stall" | sort | uniq -c | head -4 | cut -c1-160 | sed 's/^/  kernel: /'
  [ "$size" -gt 20000 ]
}

# Flag *names* were not reliably recognised by this kernel (only the numeric form had an effect),
# so try numeric values: bit 0 (skip sample-rate GET, needed to silence "cannot get freq") combined
# with each other single quirk bit of snd-usb-audio, one at a time.
for bit in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23; do
  f=$(printf '0x%x' $(( 1 | (1 << bit) )))
  if try "$f"; then
    echo; echo ">>> WORKS with flags: $f (bit $bit)"; echo "$f" > /tmp/mic-probe.result; exit 0
  fi
done
echo; echo ">>> No flag combination made the microphone work."
exit 2
