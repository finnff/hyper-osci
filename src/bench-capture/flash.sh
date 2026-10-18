#!/usr/bin/env bash
# Compile and flash xiao_adc to the XIAO RA4M1 (docs/hardware/bench-capture.md §4.4).
#
# A plain `arduino-cli upload` fails on this board ("Cannot set alternate interface zero:
# LIBUSB_ERROR_OTHER", exit 74, old firmware keeps running): the core sets use_1200bps_touch=false,
# so dfu-util goes through the app's DFU run-time interface, and that doesn't work here. A
# 1200-baud touch first drops the board into the bootloader (2886:8049) and the upload then works,
# even with the old firmware stuck streaming.
set -euo pipefail
cd "$(dirname "$0")"
FQBN=Seeeduino:renesas_uno:XIAO_RA4M1

arduino-cli compile -b "$FQBN" xiao_adc
port=$(ls /dev/serial/by-id/*XIAO_RA4M1* 2>/dev/null | head -1 || true)
if ! lsusb -d 2886:8049 >/dev/null; then
  [ -n "$port" ] || { echo "no XIAO RA4M1 serial port and no bootloader on USB" >&2; exit 1; }
  python3 -c 'import serial, sys, time
s = serial.Serial(sys.argv[1], 1200); s.dtr = False; time.sleep(0.3); s.close()' "$port"
  for _ in $(seq 20); do lsusb -d 2886:8049 >/dev/null && break; sleep 0.5; done
  lsusb -d 2886:8049 >/dev/null || { echo "bootloader did not appear; replug the XIAO" >&2; exit 1; }
fi
# The port argument is only used for discovery; dfu-util finds the bootloader by VID:PID.
arduino-cli upload -b "$FQBN" -p "${port:-/dev/ttyACM0}" xiao_adc
for _ in $(seq 20); do ls /dev/serial/by-id/*XIAO_RA4M1* >/dev/null 2>&1 && break; sleep 0.5; done
sleep 1
python3 xiao_capture.py --cmd info
