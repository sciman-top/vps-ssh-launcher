# BWG CPA admission 漂移修复与受控验收

## 范围与触发

- 时间：2026-10-09 UTC（本地 2026-10-09/10-10 跨日）。
- 目标：仅修复 `/opt/cliproxyapi/cpa-admission.py` 的投影漂移；不改 CPA
  `config.yaml`、OAuth 凭据、API key、Docker 镜像、Nginx、fail2ban 或 lane
  限额。
- 触发证据：严格 doctor 在 15:13 UTC 报
  `projection-drift-cpa-admission.py=FAIL`，期望 HEAD blob SHA-256 为
  `3eec9fcf0966759bd76ed2e48e066320bed0695701477fce49fc91cfd8eb09a4`，
  远端实际文件为旧版 `5e590c0615aca877d7932c2d7cfe49d4f3e5a8c966acdadf780e47a6cc276a6fd`。

## 投影事务

本机 `%APPDATA%\vps-ssh-launcher\providers.env` 不存在，因此完整
`cpa_bwg_guardrails.ps1 -Apply` 在远端写入前按契约拒绝；没有用空 env 或猜测凭据
绕过。远端已有受信备份
`/root/cpa-guardrails-backup-20261009T123600.781103944Z/cpa-admission.py`，其
SHA-256 与 HEAD 一致，于维护锁内执行窄替换：

1. 新建 `/root/cpa-admission-drift-backup-20261009T153405.831728774Z/`，保存漂移前
   文件；
2. 从已核对哈希的备份复制到临时文件，`chmod 755`、`python3 -m py_compile`，再
   原子 `mv` 到目标；
3. 重启 `cpa-admission.service`，复验 8318 loopback `healthz` 和 lane contract。

第一次 health 请求与服务重启存在启动竞态而得到一次 `ConnectionRefusedError`；随后
读取到 service `active (running)`、8318 已监听、`ADMISSION_HEALTH=OK`。没有发生
回滚或第二次写入。

## 结果

- 15:35、15:37、15:41 UTC strict doctor 均为 `DOCTOR_CONTRACT_OK`；9 个受管文件
  全部 `MATCH`，`admission-service=enabled-active`、`admission-health=OK`、
  `oauth_monitor=OK`、`model_substitution=OK`。
- 远端最终 `/opt/cliproxyapi/cpa-admission.py` SHA-256 为
  `3eec9fcf0966759bd76ed2e48e066320bed0695701477fce49fc91cfd8eb09a4`，服务状态
  `inflight=0`、`pending=0`、`failure_streak=0`、`cooldown_active=false`。
- 单次受控 OAuth admission：模型 `gpt-6-luna`，Responses、低 effort、16 output
  token、无重试；15:40:21 UTC 返回 `HTTP 200`，耗时 `21.379s`，无
  `Retry-After`，内容匹配预期。admission journal 同刻为
  `status=200 capacity=false waited_ms=0`。
- 非 OAuth cache canary（`deepseek-flash`）两次样本均为
  `input_tokens=3875`、`cache_read_tokens=3712`、`hit_ratio=0.9579`，返回
  `HEALTH_OK`。当前 CPA 没有可安全增加的本地缓存开关；稳定前缀已经命中主要
  可缓存 token。

## 尚未闭合的边界

- `insecure_http_providers=slot=3 host=35.213.82.91 port=8003` 仍是 doctor 的
  风险警告；该端口实测只支持 HTTP，直接改 HTTPS 会失败。未在没有替代 provider
  的情况下破坏 `gpt-6.1-sol-91` / `gpt-5.6-terra` 路由。
- OAuth 24h admission 日志仍有上游容量事件和少量 cooldown 拒绝；本次修复消除
  的是投影漂移，不会改变上游真实 capacity 或账号额度。
- 本机 Cockpit 已安装 1.3.66，而仓库旧 sidecar policy 为 1.3.65；当前没有
  sidecar 进程，两个持久 collection 文件仍是 `maxAccountConcurrency=2`、
  `accountConcurrencyWaitMs=120000`、`maxRetryCredentials=1`，API sidecar
  仍是 `requestRetry=1` / `streamBootstrapBuffering=true`。依据版本边界没有把
  1.3.65 二进制补丁投影到 1.3.66；桌面 provider target 仍未能从当前
  `config.toml` 判定，故本机 wait-cap simulation 标为 N/A。

## 回滚入口

按维护窗口先确认 lane idle，再将
`/root/cpa-admission-drift-backup-20261009T153405.831728774Z/cpa-admission.py`
（实际目录名以远端回执为准）复制回 `/opt/cliproxyapi/cpa-admission.py`，保持 755，
重启 `cpa-admission.service`，最后复跑 strict doctor。Git 回滚不能代替这一步。
