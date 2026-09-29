#!/usr/bin/env bash
# 一键重打 Cockpit sidecar 的 responses-flush 补丁（Cockpit 升级覆盖后使用）。
#
# 做四件事：
#   1. 读已装 Cockpit 版本（~/.antigravity_cockpit/server.json）
#   2. 稀疏克隆官方仓对应 tag（只取 sidecar 子树，约 30 MB）
#   3. 结构式打补丁（docs/runbooks/cockpit-sidecar-flush-fix.py，按锚点，版本变化也能命中）
#   4. 构建出新的 sidecar
# 最后打印「替换」步骤 —— 替换必须在 Cockpit 关闭时做，且**启动要由你手动双击**。
#
# 用法： bash docs/runbooks/rebuild-cockpit-sidecar-patch.sh
set -euo pipefail

UPSTREAM="https://github.com/jlcodes99/cockpit-tools.git"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXER="$REPO_ROOT/docs/runbooks/cockpit-sidecar-flush-fix.py"
PY="$REPO_ROOT/.venv/Scripts/python.exe"
GO="/c/Program Files/Go/bin/go.exe"
COCKPIT_DIR="$HOME/.antigravity_cockpit"
WORK="${TMPDIR:-/tmp}/ct-repatch-$$"
OUT="${TMPDIR:-/tmp}/cockpit-cliproxy-repatched.exe"

# 代理：环境里的 12803 到外网不通，10808 可用（2026-09-29 实测）
export https_proxy="${https_proxy:-http://127.0.0.1:10808}"
export http_proxy="${http_proxy:-http://127.0.0.1:10808}"
export GOPROXY="https://proxy.golang.org,direct"
export GOFLAGS="-mod=mod"

VERSION="$("$PY" -c "import json,os;print(json.load(open(os.path.expanduser('~/.antigravity_cockpit/server.json')))['version'])")"
echo "installed_version = $VERSION"
TAG="v$VERSION"

echo "=== 1/4 稀疏克隆 $UPSTREAM @ $TAG ==="
rm -rf "$WORK"
CLONE_ERR="$(mktemp)"
if ! git clone --depth 1 --branch "$TAG" --filter=blob:none --sparse "$UPSTREAM" "$WORK" 2>"$CLONE_ERR"; then
  echo "克隆 $TAG 失败（tag 不存在 / 版本号格式不符 / 网络不通），错误输出：" >&2
  cat "$CLONE_ERR" >&2
  rm -rf "$WORK" "$CLONE_ERR"
  exit 1
fi
rm -f "$CLONE_ERR"
( cd "$WORK" && git sparse-checkout set sidecars/cockpit-cliproxy >/dev/null 2>&1 )
echo "cloned: $(cd "$WORK" && git describe --tags 2>/dev/null || echo "$TAG")"

echo "=== 2/4 结构式打补丁 ==="
set +e
"$PY" "$FIXER" "$WORK/sidecars/cockpit-cliproxy/provider_gateway.go"
RC=$?
set -e
if [ "$RC" -eq 2 ]; then
  echo "上游已自带该修复（already_patched）—— 无需重打，直接升级即可。"
  rm -rf "$WORK"; exit 0
elif [ "$RC" -ne 0 ]; then
  echo "锚点未命中，官方源码结构已变，需人工处理。源码保留在 $WORK"; exit 1
fi

echo "=== 3/4 构建 ==="
( cd "$WORK/sidecars/cockpit-cliproxy" && "$GO" build -trimpath -o "$OUT" . )
NEW_SHA="$(sha256sum "$OUT" | cut -c1-16)"
echo "built: $OUT  sha256=$NEW_SHA"

echo "=== 4/4 下一步（必须手动）==="
cat <<EOF

新的 sidecar 已构建好。替换步骤：

  1) 先手动退出 Cockpit（托盘图标 → 退出）
  2) 备份并替换：
       cp "$HOME/AppData/Local/Cockpit Tools/cockpit-cliproxy.exe" \\
          "$HOME/AppData/Local/Cockpit Tools/cockpit-cliproxy.exe.before-repatch-\$(date +%Y%m%d-%H%M%S).bak"
       cp "$OUT" "$HOME/AppData/Local/Cockpit Tools/cockpit-cliproxy.exe"
  3) 手动双击启动 Cockpit
  4) 验收：
       PROBE_ASSERT=1 PROBE_SIDECAR_KEY=<key> \\
         "$PY" "$REPO_ROOT/outputs/sse_framing_probe.py" sidecar; echo "exit=\$?"
     期望 exit=0（headers/total ≤ 0.5、socket_reads ≥ 20）

不要从脚本/工具会话启动 Cockpit —— 该应用的前端在非交互会话里不会挂载。
构建源码保留在：$WORK
EOF
