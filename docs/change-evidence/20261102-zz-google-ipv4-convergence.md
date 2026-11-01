# 2026-11-02 zz xray 车道 google-ipv4 域清单收敛（14→10）+ drift 审计补齐

**类别**: 受控远端写入（单台 zz，一次性配置收敛）+ 只读门禁增强
**授权**: 用户 2026-11-02 会话内授权（"授权按你的推荐/建议连续自动修复和优化"）
**关联**: 第十一轮全链审查（commit 6bfe90f）；`scripts/lib/proxy_google_ipv4_domains.ps1` 单一真源

## 背景

- 真源定义 zz=10 域（v4-only host，额外域为 no-op；bwg=14 含 anthropic/claude 系）。
- zz 的 xray 车道系 2026-10-31 手动换芯旁路车道，`09_zz_routing.json` 的 `google_ipv4_out` 规则播种自 bwg 形态，携带 4 个真源外条目（anthropic.com / claude.ai / claude.com / platform.claude.com）。
- `google_ipv4_routing.ps1` ValidateSet 仅 bwg（zz 无投影器）；漂移门禁此前不覆盖该计数 → 静默发散无告警。
- 功能影响评估：zz v4-only host，多余 4 域为 no-op，收敛属契约对齐而非行为修复。

## 变更（zz，单次受控编辑）

- 备份: `/var/backups/zz-google-ipv4-converge-20261102/09_zz_routing.json`（含 before/after sha256）
- before sha256: `0a6040b7d99eddba8b8787f8fd04185b91592b0ba7d3f668dbfec7ab86d4d44c`
- 编辑: 过滤 `outboundTag=google_ipv4_out` 规则的 `domain` 列表至真源 10 域，`removed=4`，其余字段与顺序不动（json indent=2 保风格）
- after sha256: `531d26f02ddb1aee00c73c04cfd1ee857c3765660c3ec40f8c0b902817211920`

## 验收

- `xray run -test -confdir` PASS（fail-closed：不过即不重启）
- `systemctl restart xray` → active；REALITY 端口在监（45987/21470 形态校验）
- 规则复验: `09_zz_routing.json n=10 extra=[]`，与真源逐项一致
- sing-box 车道片段（10 域）未触碰
- 漂移门禁实弹: zz `google_ipv4=ok` + `HARDENING_DRIFT=NONE`（exit 0）；bwg 同轮实弹 `google_ipv4=ok` + `HARDENING_DRIFT=NONE`（bwg 14 域本就与真源对齐）
- xray 重启造成秒级连接中断，与既有 lane -Apply 形态一致

## 门禁增强（同批）

- `proxy_hardening_drift.ps1` 新增 `==google-ipv4==` 段：远端 xray 车道 `google_ipv4_out` 规则域并集必须逐项等于 PS 侧注入的真源清单（占位符注入前做形状校验），不等/缺规则/工具失败均计 `drift+=('google-ipv4')`。
- `proxy_hardening_lane_recover.ps1`: `google-ipv4` 映射为空（zz 手动车道，受控编辑，禁止盲重放）。
- 测试: `test_proxy_hardening_drift.py` lane 枚举 +1；README `HARDENING_DRIFT` 枚举 + AGENTS.md 同步。
- 受影响 pytest 33 passed；payload `bash -n` 全过；full gates 见提交信息。

## 回滚

```bash
# zz 上恢复备份并重启
cp -a /var/backups/zz-google-ipv4-converge-20261102/09_zz_routing.json \
      /etc/v2ray-agent/xray/conf/09_zz_routing.json
/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf/
systemctl restart xray
```

注意：回滚会重新引入与真源不一致的 14 域形态，drift 门禁将报 `google_ipv4=domain-mismatch`（预期告警，非误报）。
