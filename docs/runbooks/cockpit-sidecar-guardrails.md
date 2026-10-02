# Cockpit sidecar guardrails

本仓库把本机 Direct API 的持久修复、重投影和重启后验收集中到：

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Audit
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify
```

## 证据分层

- `Audit`：只读输出安装文件 hash、运行中的两个 sidecar、10909/14185 监听、持久 collection、活动 config/manifest。
- `Audit` 同时读取最近 180 分钟的脱敏 `request_logs`：将约 45 秒的本地闸门 429、115 秒以上的远端 admission/长等待 429 和 60 秒以上的成功慢请求分开计数。三个桶只是 timing bucket；只有和 CPA request-id、`cpa-admission` journal 同窗关联后，才能升级为确切归因。
- `Project`：要求传入与本机 Cockpit 版本匹配且 hash pinned 的候选 exe；已匹配时走 no-op。需要替换时先备份、staging/hash 复核，再用旧文件改名→新文件落位；失败会尝试恢复旧文件。两个本机 collection 也只在值发生变化时写入。
- `Verify`：确认安装 hash、两个运行进程的磁盘路径 hash、每个监听端口的拥有 PID、两个监听端口和全部持久 collection；`HOST_LOADED=INFERRED_FROM_LISTENER_OWNERS` 表示端口拥有者与 pinned 映像一致的运行态推断，不伪装成内核级 loaded-image hash 证明。活动 provider manifest 的 `0/120000` 属于官方生成器已知漂移，由 r3 入口钳制覆盖。

## 重投影

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 `
  -Mode Project `
  -CandidatePath "$env:TEMP\cockpit-tools-v1.3.65-build-20261002\sidecars\cockpit-cliproxy\cockpit-cliproxy-v135-gate-r3-20261002.exe"
```

脚本不会停止 Cockpit 或 sidecar。若输出 `SIDECAR_ALREADY_PROJECTED=1`，二进制无需再次投影；仍应按当前运行态执行 `Verify`。若确实发生了二进制或 collection 改动，输出 `RELOAD_REQUIRED=1` 后，使用 Cockpit 正式重载/启动路径，随后运行：

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify
```

`Verify` 是状态验证（hash/端口/监听归属/持久 collection）。重投影了**新构建**
二进制后，再补一次行为级验收（零上游配额，桩上游 + scratch 端口）：

```bash
./.venv/Scripts/python.exe scripts/cockpit_gate_wait_cap_check.py
```

3 条请求占满账号槽后，第 4 条应在期望封顶（默认 45s，`--expect-cap-s` 调整）
附近以 429 结束并打印 `ACCEPTANCE_PASS`；`--control <旧exe>` 可附加 A/B 对照
（r2-vs-r3 首次对照实测 120.002s → 45.003s）。仅 `Verify` 通过不足以证明封顶
行为，两者判据不同。

真实 OAuth 请求不属于默认验收。受控实战使用已有的非 OAuth `glm-5.3` 路径；容量、429 和慢速分析按远端 CPA admission journal、本机 `request_logs` 和请求耗时分别归因。
