#!/usr/bin/env bash
set -Eeuo pipefail

TARGET=/opt/cliproxyapi/cpa-admission.py
PIN=/etc/vps-ssh-launcher/cpa-admission.sha256

if [ ! -r "$TARGET" ] || [ ! -r "$PIN" ]; then
  echo "ADMISSION_INTEGRITY_REFUSE missing_target_or_pin" >&2
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

echo "ADMISSION_INTEGRITY_OK"
