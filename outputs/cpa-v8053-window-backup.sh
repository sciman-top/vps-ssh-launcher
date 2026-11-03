#!/usr/bin/env bash
# v8.0.53 window backup (2026-11-03): compose pin, live config, in-service
# plugin and credential hashes, under backups/ with the window naming the
# swap transaction expects.
set -Eeuo pipefail
umask 077
DIR=/opt/cliproxyapi
TS=$(date -u +%Y%m%dT%H%M%S.%NZ)
BK="$DIR/backups/$TS-from-v8.0.52-to-v8.0.53-plugin-0.9.84-unchanged"
mkdir -p "$BK"
chmod 700 "$BK"
cp -a "$DIR/compose.yml" "$BK/compose.yml"
cp -a "$DIR/config.yaml" "$BK/config.yaml"
cp -a "$DIR/plugins/workbuddy.so" "$BK/workbuddy.so"
sha256sum "$DIR"/auth/*.json > "$BK/credentials.sha256"
chmod 600 "$BK"/*
cmp -s "$BK/compose.yml" "$DIR/compose.yml" && echo BACKUP_COMPOSE_MATCH || { echo BACKUP_COMPOSE_DIFFERS; exit 10; }
echo "BACKUP_DIR=$BK"
ls -la "$BK"
