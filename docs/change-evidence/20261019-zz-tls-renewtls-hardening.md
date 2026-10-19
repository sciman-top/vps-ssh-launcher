# 2026-10-19 zz TLS 续期加固（bwg 式提前续期 wrapper 投影）收据

## 授权与背景

- 用户当次明确指令：按 zz 轮检报告的推荐连续自动修复（唯一排期项=TLS 续期加固）。
- 风险机制（bwg 2026-10-13 事故同款）：zz 证书 `zz.sciman.top` 由 vasma 原生 `install.sh RenewTLS` 续期，fork 的 `renewalTLS` 仅在"过期前最后一天"动作（stop nginx/core → acme → reloadCore → start nginx），每日检查 01:30 UTC 晚于到期时刻 → 续期日必然故障窗；且 zz 无 doctor `==tls-cert==` 门禁（guardrails 仅允许 bwg，仓库既定边界）。
- 投影前事实（只读探针）：证书 notAfter=2027-01-11（剩余 84 天）；acme 条目 `zz.sciman.top_ecc`（LetsEncrypt，Le_Webroot='no'=standalone，但宿主 80 端口空闲，续期无需停服务）；`Le_RealFullChainPath/Le_RealKeyPath` 钩子在位（裸 `--renew` 自动落盘 `/etc/v2ray-agent/tls/`）；证书消费方=nginx（reality-handshake/subscribe/alone 三 conf）+ sing-box（hysteria2 certificate_path）；root crontab 仅一条 legacy RenewTLS 行（01:30 UTC）。

## 仓侧变更（测试与模板）

- `scripts/v2ray_agent_renewtls_cron.ps1` 参数化：`ValidateSet("bwg","zz")`；证书路径/acme 域名走 `__CERT_PATH__/__ACME_DOMAIN__` token；reload 段按 profile 注入（`__RELOAD_SECTION__`）。**bwg 段逐字节保留**——本地重建产物 sha `3b960ed9…` 与 HEAD 版 wrapper 完全一致，未来 bwg 重投影零漂移。
- zz reload 段语义：活体 SNI 握手（nginx reality-handshake TLS listener，端口脱敏）核验文件证书；`renewtls-applied.sha` 状态文件记录"已应用到双消费方"的 sha——证书真续期时恰触发一次 nginx reload + sing-box restart；首日双消费方已服务文件证书且状态缺失时仅基线化（`RENEWTLS_BASELINED`，零重启）；sing-box 非活跃时自愈重启。
- 测试：`test_kernel_update_cron.py` 补 zz 契约断言（ValidateSet/证书路径/token 形态/状态文件/sing-box restart/SNI/bwg 字节稳定反例）；`test_maintenance_reliability.py` 与 `test_cpa_guardrails_script.py` 的 wrapper 提取适配参数化结构（断言意图不变）。
- 门禁：focused 37+79 绿；full `run_gates.ps1` 全绿（689 passed/313 subtests、ruff、format 63 files、mypy 63 files 零问题）。

## 远端事务（zz）

- check 预演：wrapper missing、cron.d missing、legacy root cron 行在位、TLS log age=0（原生任务每日在跑）。
- `-Apply`：全程持 `/run/vps-ssh-launcher-maintenance.lock`；备份 `/var/backups/v2ray-agent-renewtls-deploy.y5PviY`（wrapper/cron/crontab 三件，含回滚核验 `verify_rollback_state`）；payload sha 投影前断言；`RUNTIME_VERIFY_OK`；wrapper sha `1b524242…`（与本地构建逐字节一致）；legacy root cron 行摘除；cron.d `30 1 * * *` 指向 wrapper；备份修剪 keep_8（removed=0）。`RENEWTLS_LOCKED_PROJECTED`。
- 受控验收（手动跑 wrapper 一次）：exit 0、status success；acme `--renew` 跳过（rc=2 容忍路径）+ vasma 兜底 no-op（"剩余天数:84/证书有效"）+ `checkend 604800` 过；`RENEWTLS_BASELINED sha=089aa0fc…`（零重启零扰动）；sing-box/nginx 双 active；证书未变。
- 判据行：**ZZ_RENEWTLS_HARDENING_APPLIED**。

## 生效语义与回滚

- 每日 01:30 UTC：acme 提前续期窗口（60 天龄即续，杜绝到期日窗）→ sha 变化才 reload nginx + restart sing-box 一次 → 活体握手核验 → 状态落盘；acme 真错误（rc∉{0,2}）→ wrapper 失败且 `renewtls.status=failed` 可见；`checkend` 7 天门兜底；vasma 到期日分支保留为最后后备。
- 回滚：备份目录拷回 wrapper/cron.d 并恢复 root crontab legacy 行（事务自带 `verify_rollback_state`）；Git 回滚仅涉仓侧文件，不能代替远端恢复。

## 观察项与风险边界

- 首次真实续期预计 2026-11-11 前后（acme 60 天周期）：届时首次走 reload+restart 路径，log 应出现 `RENEWTLS_LOADED_CERT_VERIFIED`；此前 reload 路径仅 TODAY 基线分支实证，reload 分支以静态语法+事务核验为证。
- 仓侧新增 `zz.sciman.top` 域名与验证端口字面量（与 bwg wrapper 已入库的 `fq.sciman.top`/8443 先例同构）；证据文档仍按惯例脱敏监听端口。
- doctor `==tls-cert==` 门禁维持 bwg-only；zz 可观测性=`renewtls.status`+`crontab_tls.log`+`renewtls_log_age_days` 探针。
- bwg 本轮零接触（双机轮流纪律）。
