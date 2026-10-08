#!/usr/bin/env bash
# Force the UNO Q's USB-C port into host mode so the hub, USB sound card and any
# other peripherals enumerate when the board runs on VIN/PD power instead of
# being tethered to a PC. Without this the port stays in device mode, nothing
# enumerates, and App Lab refuses to start the keyword_spotting brick because
# there is no microphone.
#
# Installed as a system unit (needs root to write the debugfs node):
#   sudo install -m 755 usb-host-mode.sh /usr/local/sbin/usb-host-mode.sh
#   sudo systemctl enable --now usb-host-mode.service
set -u
MODE=/sys/kernel/debug/usb/4e00000.usb/mode
ROLE=/sys/class/usb_role/4e00000.usb-role-switch/role
DATA_ROLE=/sys/class/typec/port0/data_role
LOG=/var/log/usb-host-mode.log
say() { echo "$(date -Is) $*" >>"$LOG"; }

# debugfs appears late in boot; wait for the node rather than racing it.
for _ in $(seq 1 30); do [ -e "$MODE" ] && break; sleep 1; done

# Ground truth is the Type-C data role, not what we wrote: the role switch can
# accept the write and still come back as a device if nothing is attached yet.
is_host() { [ -e "$DATA_ROLE" ] && grep -q '\[host\]' "$DATA_ROLE" 2>/dev/null; }

for i in $(seq 1 60); do
  [ -w "$MODE" ] && echo host > "$MODE" 2>/dev/null
  [ -w "$ROLE" ] && echo host > "$ROLE" 2>/dev/null
  sleep 3
  say "attempt $i: mode=$(cat "$MODE" 2>/dev/null) data_role=$(cat "$DATA_ROLE" 2>/dev/null)"
  is_host && { say "confirmed host mode on attempt $i"; exit 0; }
done
say "FAILED to confirm host mode after 60 attempts"
exit 0
