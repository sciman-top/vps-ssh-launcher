# 2026-10-13 BWG 网关 TLS 证书过期事故与续期链加固证据

## 事故与根因

- 症状：用户 ChatGPT desktop（desktop codex，走 `fq.sciman.top:8443` 直连路由）突发
  "Connection failed: error sending request"。
- 服务端排查（`outputs/connection-fail-diag-20261013.txt`）：CPA 容器 running（loopback
  1.2ms 200）、nginx/admission active、fail2ban 零封禁、容器日志近 30 分钟零
  error/panic——网关本体健康。
- 本机双路排查：Xray 进程/端口正常，经代理出网正常（gstatic 204、chatgpt.com 0.9s
  可达）；**直连 `fq.sciman.top:8443` TLS 握手失败（curl 000，openssl
  `verify error:num=10:certificate has expired`）**。
- 根因：`*.sciman.top` 证书 `notAfter=2026-10-13T00:32:08Z`（北京 08:32）过期。该证书
  2026-07-15 由 acme.sh 签发（ECC，90 天），acme.sh 续期点 `Le_NextRenewTime=
  2026-09-12T01:30:40Z` 之后从未真正续期。
- **深层根因（vasma fork 设计）**：`install.sh RenewTLS` 刻意"过期前最后一天才自动
  更新"（`/etc/v2ray-agent/crontab_tls.log` 明示剩余 5/4/3/2 天时逐日报"证书有效"），
  而每日检查在 01:30 UTC——比过期时刻（00:32 UTC）晚 58 分钟，形成**续期日必然的
  TLS 故障窗**（若当天续期失败则拖至次日）。vps-launcher 的 renewtls 心跳只反映
  RenewTLS 退出码（证书有效分支恒 exit 0），对"证书逼近过期"完全失明，故 9/12 以来
  每周 doctor 心跳全绿。

## 应急处置（00:47-00:52 UTC，手工）

- `acme.sh --renew -d '*.sciman.top' --ecc --force`：签发成功，新证书
  `notAfter=2027-01-10T23:49:17Z`（LE 90 天），install-cert 自动落到 nginx 引用的
  `/etc/v2ray-agent/tls/fq.sciman.top.crt|.key`（`outputs/cert-expiry-diag-20261013.txt`）。
- `nginx -s reload`（`nginx -t` 先行）。
- 恢复验证（本机，用户路径）：openssl 握手无 verify error、TLSv1.3；直连
  `https://fq.sciman.top:8443/` 返回 HTTP 404（无 capability path 的预期响应）。
- Xray 服务（active）未重启：其配置未见引用该证书路径，且过期期间经代理出网一直
  可用（入站不校验该证书）；避免在事故中引入代理链秒级中断。Xray 内存中的旧证书
  仅影响新入站握手，与本网关故障无关。
- 附带发现：故障窗内业务影响仅限直连 8443/443 的 TLS 严格校验客户端（desktop codex、
  bot 通道族）；08:32（过期）至 08:47（reload）约 15 分钟，比 fork 设计的"等到 09:30
  cron 自愈"提前 43 分钟恢复。

## 加固（本切片）

1. **wrapper 提前续期**（`scripts/v2ray_agent_renewtls_cron.ps1`，仓库所有、bwg-only）：
   `/usr/local/sbin/vps-launcher-v2ray-agent-renewtls.sh` 在调用 fork RenewTLS 前先执行
   `acme.sh --renew -d '*.sciman.top' --ecc`（非强制：acme.sh 在到期前 30 天窗口内才实际
   续期，此前 exit 0 跳过），证书文件 sha 变化时 `nginx -s reload` 并写
   `RENEWTLS_EARLY` 日志行；acme 失败 `|| true` 落回 fork 的 last-day 逻辑作兜底。
   fork 的 install.sh 调用与共享锁/状态文件机制不变。
2. **doctor 证书门禁**（`scripts/remote/cpa-guardrail-doctor.sh`）：新增 `==tls-cert==`
   段，读取线上证书 `notAfter` 计算剩余天数并输出 `gateway_tls_cert_days_left=N`；
   `< 7` 天或不可读时 `mark_fail tls-cert`（strict doctor 转 `DOCTOR_CONTRACT_FAILED`）。
   该门禁对本事故的反事实有效：9/12 续期点后证书剩余天数持续下降，10/6 即会转红。
3. 测试：`test_cpa_guardrails_script.py` 新增两例（doctor 门禁存在性与阈值顺序；wrapper
   提前续期先于 fork 调用、失败容忍、reload-on-change）。全量门禁
   `569 passed, 1 skipped, 269 subtests`（`outputs/gates-20261013-tls-fix.txt`；首跑
   lint:format 拒绝新测试格式，format 后全绿）。

## 投影与验证

- `v2ray_agent_renewtls_cron.ps1 -Profile bwg -Apply`：wrapper 原子投影（b64+sha 校验）
  + cron.d 重写（schedule 不变 `30 1 * * *`）+ 备份/回滚路径完好。
- `cpa_bwg_guardrails.ps1 -Apply`：doctor 投影，drift `MATCH`。
- 远端实跑新 wrapper 一次（收据 `outputs/renewtls-early-verify-20261013.txt`）：acme.sh
  skip（证书新鲜）+ fork RenewTLS"证书有效"+ 状态文件 success；crontab_tls.log 出现
  于本切片的新增路径生效。
- doctor（严格）输出 `gateway_tls_cert_days_left=89`、`tls-cert=OK`、
  `DOCTOR_CONTRACT_OK`。

## 回滚入口

- 证书：acme.sh 保留旧证书备份（`~/.acme.sh/*.sciman.top_ecc/backup/`）；本切片未改
  fork 与 acme 配置。
- wrapper/doctor：revert 本切片提交后重跑两个 `-Apply`；cron.d 投影器自带备份/回滚
  路径（`/var/backups/v2ray-agent-renewtls-deploy.*`）。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 根因证据链完整（证书日期、fork 日志、心跳语义）；加固切片全门禁通过。 |
| `filesystem_projected` | wrapper/doctor 投影后 drift MATCH；cron.d 契约不变。 |
| `host_loaded` | 新 wrapper 远端实跑 exit 0 + 状态 success；doctor 新门禁 `tls-cert=OK`（days_left=89）。 |
| `controlled_live_replay` | 用户路径直连 HTTPS 恢复（TLSv1.3 无告警、HTTP 404 预期响应）。 |
| `natural_live_accepted` | 用户桌面端恢复由其重试确认；下个续期窗（~2026-12-11 前 30 天）由 wrapper 提前续期 + doctor 门禁双保险覆盖。 |
