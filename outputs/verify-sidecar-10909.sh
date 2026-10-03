#!/usr/bin/env bash
# 验证 10909 (Provider Gateway) 与 14185 (API 服务) 的运行态
# 用法: bash outputs/verify-sidecar-10909.sh
# 只读，不改任何配置。

set -u
COCKPIT_DIR="/c/Users/sciman/.antigravity_cockpit"
CODEX_TOML="/c/Users/sciman/.codex/config.toml"

echo "=== 1. 端口监听状态 ==="
for p in 10909 14185; do
  printf "  %-6s " "$p:"
  if (echo > /dev/tcp/127.0.0.1/$p) 2>/dev/null; then echo "OPEN"; else echo "CLOSED"; fi
done

echo
echo "=== 2. 侧车进程 ==="
tasklist 2>/dev/null | grep -i "cliproxy" || echo "  (none)"

echo
echo "=== 3. 默认实例绑定账号 ==="
python -c "
import json
d=json.load(open(r'C:\Users\sciman\.antigravity_cockpit\codex_instances.json',encoding='utf-8'))
b=d['defaultSettings'].get('bindAccountId')
print('  bindAccountId =',b)
print('  需求网关前缀  =', b.startswith('__provider_gateway__:') if b else '-')
"

echo
echo "=== 4. 桌面 config.toml 指向 ==="
grep -a -E '^\[model_providers\.|^base_url|^name =' "$CODEX_TOML" 2>/dev/null | head -8

echo
echo "=== 5. 今日 10909 启动记录（最后 3 条） ==="
LOG="$COCKPIT_DIR/logs/codex-api.log.$(date +%Y-%m-%d)"
grep -a "provider-gateway\] sidecar 已启动" "$LOG" 2>/dev/null | tail -3 || echo "  (no log)"

echo
echo "=== 6. 结论 ==="
if (echo > /dev/tcp/127.0.0.1/10909) 2>/dev/null; then
  echo "  ✓ 10909 正在运行 —— Cockpit「获取上游模型」与 ChatGPT desktop 应可用"
else
  echo "  ✗ 10909 未运行 —— Cockpit 会报 PROVIDER_MODELS_HTTP_503，desktop 无法连通"
  echo "    修复: 见 outputs/fix-10909-provider-gateway.md"
fi
