#!/usr/bin/env bash
# 自包含：给 Cockpit Tools 的 sidecar 补上 provider-gateway responses 透传缺失的 Flush。
#
#   sidecars/cockpit-cliproxy/provider_gateway.go 的 writeProviderGatewayResponsesStream
#   只写 c.Writer 从不 Flush()，导致整段 SSE 被 net/http 的 bufio 憋到流末尾才吐出。
#   上游 issue #2658 / PR #2659。
#
# 本脚本做四件事，最后打印「替换 + 验收」步骤（替换与启动必须人工做）：
#   1. 定位 Cockpit 与 sidecar，读已装版本
#   2. 稀疏克隆官方仓对应 tag（只取 sidecar 子树，约 30 MB）
#   3. 结构式打补丁（按锚点，跨版本可命中，幂等）
#   4. 构建
#
# 用法：
#   bash patch-cockpit-sidecar.sh [--cockpit-dir DIR] [--proxy URL] [--tag TAG] [--yes]
# 依赖：git、go、python3（或 python）
set -uo pipefail

UPSTREAM="https://github.com/jlcodes99/cockpit-tools.git"
COCKPIT_DIR=""; PROXY=""; TAG=""; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cockpit-dir) COCKPIT_DIR="${2:-}"; shift 2;;
    --proxy)       PROXY="${2:-}";       shift 2;;
    --tag)         TAG="${2:-}";         shift 2;;
    --yes)         ASSUME_YES=1;         shift;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0;;
    *) echo "未知参数: $1"; exit 2;;
  esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------- 平台与工具 ----------
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) OS=windows; EXE=".exe";;
  Darwin)               OS=macos;   EXE="";;
  Linux)                OS=linux;   EXE="";;
  *)                    OS=unknown; EXE="";;
esac
say "platform = $OS"

GO="$(command -v go 2>/dev/null || true)"
[ -z "$GO" ] && [ -x "/c/Program Files/Go/bin/go.exe" ] && GO="/c/Program Files/Go/bin/go.exe"
[ -n "$GO" ] || die "找不到 go，请先安装 Go（https://go.dev/dl/）后重跑"
say "go       = $GO ($("$GO" version 2>/dev/null | head -1))"

PY="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
[ -n "$PY" ] || die "找不到 python3/python，请先安装 Python 3 后重跑"
say "python   = $PY"

git --version >/dev/null 2>&1 || die "找不到 git，请先安装 git 后重跑"

# ---------- 定位 Cockpit 与版本 ----------
STATE_DIR="$HOME/.antigravity_cockpit"
if [ -z "$COCKPIT_DIR" ]; then
  case "$OS" in
    windows) COCKPIT_DIR="${LOCALAPPDATA:-$HOME/AppData/Local}/Cockpit Tools";;
    macos)   for c in "/Applications/Cockpit Tools.app/Contents/MacOS" \
                       "/Applications/Cockpit Tools.app/Contents/Resources" \
                       "$HOME/Applications/Cockpit Tools.app/Contents/MacOS"; do
               [ -e "$c/cockpit-cliproxy" ] && { COCKPIT_DIR="$c"; break; }; done;;
    linux)   for c in "$HOME/.local/share/Cockpit Tools" "/opt/Cockpit Tools" \
                       "$HOME/.local/bin"; do
               [ -e "$c/cockpit-cliproxy" ] && { COCKPIT_DIR="$c"; break; }; done;;
  esac
fi
[ -n "$COCKPIT_DIR" ] && [ -d "$COCKPIT_DIR" ] || die "找不到 Cockpit 目录，请用 --cockpit-dir 指定"
SIDECAR="$COCKPIT_DIR/cockpit-cliproxy$EXE"
[ -f "$SIDECAR" ] || die "在 $COCKPIT_DIR 下找不到 cockpit-cliproxy$EXE，请用 --cockpit-dir 指定"
say "cockpit  = $COCKPIT_DIR"
say "sidecar  = $SIDECAR"

VER="$("$PY" - "$STATE_DIR/server.json" <<'PY'
import json,sys,os
p=sys.argv[1]
try: print(json.load(open(p,encoding='utf-8')).get('version',''))
except Exception: print('')
PY
)"
[ -n "$VER" ] || die "读不到 $STATE_DIR/server.json 的 version，请手动用 --tag 指定（如 --tag v1.3.62）"
[ -n "$TAG" ] || TAG="v$VER"
say "version  = $VER  (tag=$TAG)"

# ---------- 代理（不硬编码，按需传入或沿用环境） ----------
if [ -n "$PROXY" ]; then
  export https_proxy="$PROXY" http_proxy="$PROXY" HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY"
  say "proxy    = $PROXY"
else
  say "proxy    = 沿用环境（$([ -n "${https_proxy:-}" ] && echo "$https_proxy" || echo 未设置)）"
fi

# ---------- 克隆 ----------
WORK="${TMPDIR:-/tmp}/ct-$TAG-$$"
OUT="${TMPDIR:-/tmp}/cockpit-cliproxy-patched$EXE"
say ""
say "=== 1/4 稀疏克隆 $UPSTREAM @ $TAG ==="
rm -rf "$WORK"
git clone --depth 1 --branch "$TAG" --filter=blob:none --sparse "$UPSTREAM" "$WORK" || \
  die "克隆失败（tag 是否存在？网络/代理？）"
( cd "$WORK" && git sparse-checkout set sidecars/cockpit-cliproxy ) || die "sparse-checkout 失败"
SRC="$WORK/sidecars/cockpit-cliproxy/provider_gateway.go"
[ -f "$SRC" ] || die "克隆后找不到 $SRC"
say "ok: $SRC"

# ---------- 结构式补丁 ----------
say ""
say "=== 2/4 结构式打补丁 ==="
"$PY" - "$SRC" <<'PY'
import re, sys

path = sys.argv[1]
with open(path, encoding="utf-8", newline="") as fh:
    original = fh.read()

# Windows 工作树常是 CRLF，而锚点按 LF 写：先归一化，写完按原风格还原
uses_crlf = "\r\n" in original
work = original.replace("\r\n", "\n") if uses_crlf else original

DECL = (
    "\tflusher, ok := c.Writer.(http.Flusher)\n"
    "\tif !ok {\n"
    '\t\twriteAPIError(c, http.StatusInternalServerError, "streaming not supported", "streaming_not_supported")\n'
    "\t\treturn\n"
    "\t}\n"
)
FLUSH = (
    "\t\t\t// SSE events are terminated by a blank line; flush there so the client sees\n"
    "\t\t\t// each event as it arrives instead of one bufio burst at the end.\n"
    '\t\t\tif len(bytes.TrimRight(line, "\\r\\n")) == 0 {\n'
    "\t\t\t\tflusher.Flush()\n"
    "\t\t\t}\n"
)
FUNC_RE = re.compile(
    r"(func \(s \*relayServer\) writeProviderGatewayResponsesStream\([^)]*\) \{\n)(.*?)(\n\}\n)", re.S
)

m = FUNC_RE.search(work)
if not m:
    print("FAILED: 找不到 writeProviderGatewayResponsesStream，官方结构已变，请人工处理")
    sys.exit(1)
head, body, tail = m.group(1), m.group(2), m.group(3)

if "c.Writer.(http.Flusher)" in body:
    print("ALREADY_PATCHED: 本机版本已自带该修复，无需重打")
    sys.exit(2)

guard = re.search(r"\tif body == nil \{\n\t\treturn\n\t\}\n", body)
if not guard:
    print("FAILED: 找不到 nil guard 锚点"); sys.exit(1)
body = body[: guard.end()] + DECL + body[guard.end():]

wblk = re.search(
    r"(\t\t\tif _, writeErr := c\.Writer\.Write\([^\n]*\); writeErr != nil \{\n\t\t\t\treturn\n\t\t\t\}\n)", body
)
if not wblk:
    print("FAILED: 找不到 Write 块锚点"); sys.exit(1)
body = body[: wblk.end()] + FLUSH + body[wblk.end():]

tails = list(re.finditer(r"\n\t\tif err != nil \{\n\t\t\treturn\n\t\t\}\n", body))
if not tails:
    print("FAILED: 找不到 err 块锚点"); sys.exit(1)
t = tails[-1]
body = body[: t.start()] + "\n\t\tif err != nil {\n\t\t\tflusher.Flush()\n\t\t\treturn\n\t\t}\n" + body[t.end():]

out = work[: m.start()] + head + body + tail + work[m.end():]
if uses_crlf:
    out = out.replace("\n", "\r\n")
with open(path, "w", encoding="utf-8", newline="") as fh:
    fh.write(out)
print(f"PATCHED: {len(original)} -> {len(out)} bytes (line_ending={'CRLF' if uses_crlf else 'LF'})")
PY
RC=$?
if [ "$RC" -eq 2 ]; then
  say "上游已自带该修复 —— 无需重打，直接升级 Cockpit 即可。"
  rm -rf "$WORK"; exit 0
elif [ "$RC" -ne 0 ]; then
  die "补丁未命中（源码保留在 $WORK）"
fi

# ---------- 构建 ----------
say ""
say "=== 3/4 构建 ==="
( cd "$WORK/sidecars/cockpit-cliproxy" && GOPROXY="${GOPROXY:-https://proxy.golang.org,direct}" \
    GOFLAGS="-mod=mod" "$GO" build -trimpath -o "$OUT" . ) || die "构建失败"
SHA="$("$PY" -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" "$OUT")"
say "built: $OUT  sha256=$SHA"

# ---------- 下一步 ----------
say ""
say "=== 4/4 下一步（替换与启动必须人工做）==="
cat <<EOF

新 sidecar 已构建好：$OUT

  1) 先人工退出 Cockpit（托盘图标 → 退出）。运行中该文件被锁，无法替换。
  2) 备份并替换：
       cp "$SIDECAR" "$SIDECAR.before-sse-flush-\$(date +%Y%m%d-%H%M%S).bak"
       cp "$OUT" "$SIDECAR"
  3) 人工双击启动 Cockpit。
  4) 验收（把 <KEY> 换成 ~/.antigravity_cockpit/codex_provider_gateway_sidecars/*/manifest.json
     里 apiKeys[0].key；不要打印这个 key）：
       向 http://127.0.0.1:10909/v1/responses 发一个 stream=true 的小请求，
       统计响应头到达时间与 socket 读取次数。
       达标：headers_ms / total_ms <= 0.5 且读取次数 >= 20。
       不要用固定的「headers_ms < 2 秒」当唯一判据（上游自身波动就有 1.2-2.2 秒）。

⚠️ 不要从脚本/工具会话启动 Cockpit：非交互会话下它的前端不会挂载
   （日志 [Diagnostics] 前端启动超时 lastStage=none，缺 react_mounted），随后静默退出。

⚠️ Cockpit 下次升级会覆盖该二进制（更新是弹窗提示、非静默）。
   升级后重跑本脚本即可（会读到新版本并用对应 tag 重建）；
   若上游已合并该修复，脚本会报 ALREADY_PATCHED 并让你直接升级。

源码保留在：$WORK
EOF
