# Cockpit sidecar guardrails

本仓库把本机 Direct API 的持久修复、重投影和重启后验收集中到：

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Audit
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify
```

## 证据分层

- `Audit`：只读输出安装文件 hash、运行中的两个 sidecar、10909/14185 监听、持久 collection、活动 config/manifest。
- `Audit` 同时读取最近 180 分钟的脱敏 `request_logs`：将约 45 秒的本地闸门 429、115 秒以上的远端 admission/长等待 429 和 60 秒以上的成功慢请求分开计数。
- `Project`：要求传入与本机 Cockpit 版本匹配的候选 exe；先备份，再 staging、hash 复核、原子替换，并投影两个本机 collection 文件。
- `Verify`：确认安装 hash、两个运行进程 hash、两个监听端口和持久 collection；活动 provider manifest 的 `0/120000` 属于官方生成器已知漂移，由 r3 入口钳制覆盖。

## 重投影

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 `
  -Mode Project `
  -CandidatePath "$env:TEMP\cockpit-tools-v1.3.65-build-20261002\sidecars\cockpit-cliproxy\cockpit-cliproxy-v135-gate-r3-20261002.exe"
```

脚本不会停止 Cockpit 或 sidecar。输出 `RELOAD_REQUIRED=1` 后，使用 Cockpit 正式重载/启动路径，随后运行：

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify
```

真实 OAuth 请求不属于默认验收。受控实战使用已有的非 OAuth `glm-5.3` 路径；容量、429 和慢速分析按远端 CPA admission journal、本机 `request_logs` 和请求耗时分别归因。
