#!/usr/bin/env bash
set -Eeuo pipefail

TARGET=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256
UNIT=/etc/systemd/system/cpa-admission.service

if [ ! -r "$TARGET" ] || [ ! -r "$PIN" ] || [ ! -r "$UNIT" ]; then
  echo "ADMISSION_INTEGRITY_REFUSE missing_target_or_pin_or_unit" >&2
  exit 1
fi
if [ -L "$TARGET" ] || [ -L "$PIN" ] || [ -L "$UNIT" ] ||
   [ ! -f "$TARGET" ] || [ ! -f "$PIN" ] || [ ! -f "$UNIT" ]; then
  echo "ADMISSION_INTEGRITY_REFUSE unsafe_target_or_pin_or_unit" >&2
  exit 1
fi

expected=$(awk 'NF {print $1; exit}' "$PIN")
if [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ADMISSION_INTEGRITY_REFUSE invalid_pin" >&2
  exit 1
fi

actual=$(sha256sum "$TARGET" | awk '{print $1}')
if [ "$actual" != "$expected" ]; then
  echo "ADMISSION_INTEGRITY_REFUSE hash_mismatch" >&2
  exit 1
fi

if ! grep -Fqx \
  'Environment=VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER=/run/cpa-admission/process' \
  "$UNIT"; then
  echo "ADMISSION_INTEGRITY_REFUSE process_marker_unit_missing" >&2
  exit 1
fi

if ! grep -Fqx \
  'ExecStart=/opt/cliproxyapi/cpa-admission-launch.sh' \
  "$UNIT"; then
  echo "ADMISSION_INTEGRITY_REFUSE broker_compatible_launcher_missing" >&2
  exit 1
fi

echo "ADMISSION_INTEGRITY_OK"
