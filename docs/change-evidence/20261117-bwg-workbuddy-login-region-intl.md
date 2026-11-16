# BWG WorkBuddy `login_region` 回正回执（2026-11-17）

**性质**：一次受控配置修正与只读复验。未生成 OAuth、未轮换凭据、未清理冷却、
未提高并发，也未执行真实生成探针。

## 1. 依据与范围

- 目标主机：`bwg`。
- 变更入口：`scripts/cpa_workbuddy_login_region.ps1`。
- 变更范围：仅修改 `/opt/cliproxyapi/config.yaml` 中
  `plugins.configs.workbuddy.login_region`，从 `cn` 改为 `intl`。
- 保护边界：脚本在同一维护锁和 `/run/cpa-admission/requests.lock` 下排空流量，
  先备份，再保持原文件 inode 写回，并做 YAML 路径级差异校验和读回校验。
- 不采用：未投影尚未证明的 WorkBuddy 全局 `oauth.model-alias` / CN/INTL
  admission 拆 lane 方案；该方案仍不能证明凭据 realm 隔离。

## 2. 执行回执

执行时间为 `2026-11-16T21:19:05Z`；本机显示日期为 2026-11-17（Asia/Shanghai）。

```text
TRAFFIC_DRAIN=ACQUIRED
WORKBUDDY_LOGIN_REGION=APPLIED previous=cn current=intl
CONFIG_RELOAD_SETTLE=3s
```

脚本报告的配置摘要哈希：

```text
SHA256_BEFORE=069839541cfc43746e0d8d3e6a6d23f1aa659fbf7f4733d8fc9a95d62b56ea74
SHA256_AFTER=04cb938c5410e9de47f34c43cf13f77f8f2c4d468e1651deaa8fc295b0b57b30
```

备份留在远端受保护的 `/root/cpa-workbuddy-login-region-backup-<timestamp>/config.yaml`
目录；凭据内容未进入本回执。

## 3. 复验

`pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg`：

```text
DOCTOR_CONTRACT_OK
workbuddy-login_region=intl state=ok
container status=running restart=0
runtime_binary_source=image
runtime_binary_hash=MATCH
admission-integrity=OK
projection drift=all MATCH
credential_ban_signals_7d=0
lane-hold=OK
workbuddy-version=0.9.89
```

当前生产二进制仍为 `cli-proxy-api-fork:v8.0.75-gemini-raw`。本次动作没有重启
CPA 容器，也没有修改模型、OAuth 文件、路由、冷却或并发策略。

## 4. 证据边界与回滚

- `repo_verified`：脚本和验收测试已在本仓验证；验收脚本的默认临时路径修复保留在
  `scripts/remote/cpa-acceptance-fork.py`。
- `host_loaded`：严格 doctor 读回 `login_region=intl`、运行版本、哈希、状态文件权限
  和配置投影均通过。
- `controlled_live`：配置写入在维护锁和流量排空窗口内完成，随后配置重新加载稳定。
- `live_accepted`：本次没有发送新的 OAuth 生成请求或自然流量样本，故不宣称长期
  provider 安全、资格、QoS、缓存命中率或封号风险已被证明。

回滚时在同一维护锁和流量排空边界内恢复备份目录中的 `config.yaml`，读回
`login_region=cn` 后再执行严格 doctor；Git 回滚不能代替该远端恢复。
