#!/usr/bin/env bash
# 验证 10909 (Provider Gateway) 与 14185 (API 服务) 的运行态
# 用法: bash outputs/verify-sidecar-10909.sh
# 只读，不改任何配置。只打印密钥长度与尾 4 位，绝不打印完整密钥。

set -u
PY="${PY:-python}"
export PY

echo "=== 1. 端口监听状态 ==="
for p in 10909 14185; do
  printf "  %-6s " "$p:"
  if (echo > /dev/tcp/127.0.0.1/$p) 2>/dev/null; then echo "OPEN"; else echo "CLOSED"; fi
done

echo
echo "=== 2. 侧车进程 ==="
tasklist 2>/dev/null | grep -i "cliproxy" || echo "  (none)"

echo
echo "=== 3. 默认实例绑定账号 + key 归属核对 ==="
"$PY" - <<'PYEOF'
import json, os, hashlib, glob

BASE = r'C:\Users\sciman\.antigravity_cockpit'

# --- 绑定账号 ---
bind = None
try:
    d = json.load(open(os.path.join(BASE, 'codex_instances.json'), encoding='utf-8'))
    bind = (d.get('defaultSettings') or {}).get('bindAccountId')
except Exception as e:
    print('  (读取 codex_instances.json 失败: %s)' % e)

print('  bindAccountId =', bind)
prefixed = bool(bind and bind.startswith('__provider_gateway__:'))
print('  需求网关前缀  =', prefixed)

acct_id = bind.split(':', 1)[1] if prefixed else bind
acct_md5 = (acct_id or '').replace('codex_apikey_', '')

# --- provider key 清单（含 md5） ---
providers = []
try:
    pj = json.load(open(os.path.join(BASE, 'codex_model_providers.json'), encoding='utf-8'))
    providers = pj if isinstance(pj, list) else pj.get('providers', [])
except Exception as e:
    print('  (读取 codex_model_providers.json 失败: %s)' % e)

by_md5 = {}
for p in providers:
    for k in p.get('apiKeys', []):
        kk = k.get('apiKey') or k.get('key') or ''
        if not kk:
            continue
        by_md5.setdefault(hashlib.md5(kk.encode()).hexdigest(), []).append(
            (p.get('name'), k.get('id'), len(kk), kk[-4:], p.get('baseUrl'))
        )

# --- 反查绑定账号对应哪把 key ---
print()
print('  --- 绑定账号 id 反查 provider key ---')
if not acct_md5:
    print('     (无法解析账号 id)')
elif acct_md5 in by_md5:
    for name, kid, ln, tail, bu in by_md5[acct_md5]:
        print('     md5=%s -> provider「%s」key=%s (len %d, ...%s)' % (acct_md5[:8], name, kid, ln, tail))
        print('                baseUrl=%s' % bu)
else:
    print('     md5=%s 在任何 provider 条目里都找不到对应 key' % acct_md5[:8])
    print('     ⚠️ 条目与账号可能已错位——请勿在「模型供应商」页编辑该 provider 的 API Key')

# --- 各 provider key 与端点匹配性 ---
print()
print('  --- provider key 与端点匹配（供人工判断是否挂错条目）---')
for p in providers:
    bu = p.get('baseUrl') or ''
    for k in p.get('apiKeys', []):
        kk = k.get('apiKey') or k.get('key') or ''
        if not kk:
            continue
        flag = ''
        if '10909' in bu and not kk.startswith('agt_codex_'):
            flag = '  ⚠️ 10909 只接受 agt_codex_ 前缀的 sidecar key'
        if 'fq.sciman.top' in bu and kk.startswith('agt_codex_'):
            flag = '  ⚠️ fq 网关不接受 agt_codex_ 前缀（那是本地 sidecar key）'
        print('     %-22s len=%-3d ...%s%s' % (p.get('name'), len(kk), kk[-4:], flag))
PYEOF

echo
echo "=== 4. 桌面 config.toml 指向 ==="
CODEX_TOML="${CODEX_TOML:-/c/Users/sciman/.codex/config.toml}"
grep -a -E '^\[model_providers\.|^base_url|^name =' "$CODEX_TOML" 2>/dev/null | head -8

echo
echo "=== 5. 活动 sidecar config 的一致性检查 ==="
"$PY" - <<'PYEOF'
import json, os, glob, hashlib
BASE = r'C:\Users\sciman\.antigravity_cockpit'
files = sorted(glob.glob(os.path.join(BASE, 'codex_provider_gateway_sidecars', '*', 'config.json')))
if not files:
    print('  (未找到 provider gateway sidecar config)')
for f in files:
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        print('  (读取失败 %s: %s)' % (os.path.basename(os.path.dirname(f))[:12], e))
        continue
    tag = os.path.basename(os.path.dirname(f))[:12]
    port = d.get('port')
    print('  [%s] port=%s' % (tag, port))
    for c in d.get('codex-api-key', []):
        bu = c.get('base-url', '')
        kk = c.get('api-key', '')
        print('      upstream=%s | len=%d ...%s' % (bu, len(kk), kk[-4:]))
    for k in d.get('api-keys', []):
        print('      accepts  len=%d ...%s md5=%s' % (len(k), k[-4:], hashlib.md5(k.encode()).hexdigest()[:8]))
PYEOF

echo
echo "=== 6. 今日 10909 启动记录（最后 3 条） ==="
COCKPIT_DIR="${COCKPIT_DIR:-/c/Users/sciman/.antigravity_cockpit}"
LOG="$COCKPIT_DIR/logs/codex-api.log.$(date +%Y-%m-%d)"
grep -a "provider-gateway\] sidecar 已启动" "$LOG" 2>/dev/null | tail -3 || echo "  (no log)"

echo
echo "=== 7. 结论 ==="
# 桌面指向哪个网关决定 10909 的结论该怎么读（两种模式都接受，见
# docs/change-evidence/20261004-desktop-provider-target-decision.md）：
#   local_gateway  -> 10909 必须在跑，否则 desktop 必不通
#   public_gateway -> 10909 只是旁路信息，desktop 的可用性取决于公网入口
TARGET_URL=$(grep -a -A3 '^\[model_providers\.codex_local_access\]' "$CODEX_TOML" 2>/dev/null \
  | grep -a '^base_url' | head -1 | sed 's/.*= *"\(.*\)"/\1/')
case "$TARGET_URL" in
  *10909*|*127.0.0.1*|*localhost*) TARGET_MODE="local_gateway" ;;
  *fq.sciman.top*)                 TARGET_MODE="public_gateway" ;;
  "")                              TARGET_MODE="unknown" ;;
  *)                               TARGET_MODE="other" ;;
esac
echo "  桌面目标模式 = $TARGET_MODE  ($TARGET_URL)"
if (echo > /dev/tcp/127.0.0.1/10909) 2>/dev/null; then
  echo "  ✓ 10909 正在运行"
else
  echo "  ✗ 10909 未运行"
fi
if [ "$TARGET_MODE" = "local_gateway" ]; then
  if (echo > /dev/tcp/127.0.0.1/10909) 2>/dev/null; then
    echo "  ⇒ desktop 走 10909：当前应可用"
  else
    echo "  ⇒ desktop 走 10909：**desktop 必不通**，Cockpit 会报 PROVIDER_MODELS_HTTP_503"
    echo "    修复: 见 outputs/fix-10909-provider-gateway.md"
    echo "    复发陷阱: 在「模型供应商」页编辑 fq.sciman.top 或 CPA (local 10909) 的"
    echo "              API Key 都会重算 api_provider_mode 并静默停掉 10909。"
  fi
elif [ "$TARGET_MODE" = "public_gateway" ]; then
  echo "  ⇒ desktop 直连公网入口：10909 的状态与 desktop 可用性**无关**；"
  echo "    真正的判据是桌面模型目录能被该入口路由 —— 跑"
  echo "    ./.venv/Scripts/python.exe scripts/cockpit_provider_health.py"
else
  echo "  ⚠️ 无法判定桌面目标（config.toml 里没读到 codex_local_access.base_url）"
fi
