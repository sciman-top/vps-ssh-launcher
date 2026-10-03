#!/usr/bin/env bash
set -Eeuo pipefail
SELF="$1"
while IFS= read -r line; do
  case "$line" in
    "#FILE "*)
      out="${line#\#FILE }"
      : > "$out"
      ;;
    *)
      [ -n "$out" ] && printf '%s' "$line" | base64 -d >> "$out" || true
      ;;
  esac
done < "$SELF"
echo UNPACK_DONE
