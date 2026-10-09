#!/bin/sh
# One-time: let your user read/write the P280 HID interface (hook switch, ring) without sudo.
set -e
echo 'KERNEL=="hidraw*", ATTRS{idVendor}=="2603", ATTRS{idProduct}=="0280", MODE="0660", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/99-swyx-p280.rules
sudo udevadm control --reload
sudo udevadm trigger
echo "Done. Unplug and replug the phone."
