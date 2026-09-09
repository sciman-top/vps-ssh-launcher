# 2026-09-09 bwg CPA 公网入口与失败放大风险收紧

## 范围与决定

- 目标 profile：`bwg`；本次未连接、检查或修改 `zz`。
- 用户明确保留公网 Nginx TLS 入口与随机 capability path；本次没有改为 SSH tunnel、VPN 或仅内网入口。
- 变更目标是减少本地可避免的失败放大和暴露面：关闭 CPA 自动请求重试、移除已确认失效的上游条目、保护入口、避免 access log 记录随机路径，并保留可回滚证据。
- 没有启用 `identity-confuse`、伪造指纹、增加账号或 key，也没有尝试绕过 provider 的账号/套餐配额。

## 远端 apply

- 执行入口：`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`。
- 备份目录：`/root/cpa-guardrails-backup-20260909T141602Z/`。
- CPA 镜像未变更：`eceasy/cli-proxy-api:v7.2.154`，digest 为 `sha256:e93870454b282f260cd1587bcfb6a11c638915903647a1175f9b0915a865f66d`。
- `config.yaml`：`request-retry` 从 `1` 收紧为 `0`；移除一个已确认失效的 `r2` Codex credential block 和一个 DeepSeek placeholder provider；`max-retry-credentials: 1`、`save-cooldown-status: true`、`force-model-prefix: true`、`routing.strategy: fill-first` 保持不变。
- `auto-update.sh`：复用并校验已有的 awk API-key extraction；本次 hash 保持 `e8dbd060ba81b6fb5118126eccd5f1e8bf193e3a52b462f14bff1356aede9460`，没有引入第二份 updater 逻辑。
- Nginx：继续 `0.0.0.0:8443 ssl`，随机 16 hex 路径的值在 apply 前后相同但不记录在本 receipt；CPA 继续只对 `127.0.0.1:8317` 提供服务。已有 `10r/s + burst=20` 和每 IP `limit_conn=6` 被保留并重新断言。
- access log：改为 `cpa_safe` 脱敏格式，仅记录方法、状态、耗时和响应字节，不记录 `$request`/随机路径；最终 Nginx 配置 hash 为 `3f6f3cefce2a60dbe054e364adf8fc9191ab1776db8b4ba51d44d4b848594328`。
- logrotate：复用现有 `/etc/logrotate.d/nginx` 的 `/var/log/nginx/*.log` 规则；没有创建 `/etc/logrotate.d/cpa-gateway`，避免重复管理同一日志。
- apply 过程重启 CPA 一次；readiness 最终 HTTP 200。重启窗口出现过一次 TCP reset，随后在等待循环内恢复，不构成失败。

## Fresh host verification

- 容器 `running`，restart count `0`；CPA loopback listener、Nginx 公网 `8443`、updater timer 均正常。
- `/v1/models` 目录摘要：`has_r2=False`、`has_deepseek=False`、`has_r1=True`、`has_oauth_luna=True`、`has_glm=True`。
- `bash -n /opt/cliproxyapi/auto-update.sh` 和 `docker compose ... config --quiet` 通过；`nginx -t` 通过；全局 `logrotate -d /etc/logrotate.conf` 通过。
- updater 手工运行退出 0，日志为 `SKIP: v7.2.155 is 0d old (<3d soak), staying on v7.2.154`；没有提前升级尚在 soak 期的镜像。

## 受控公网路径回放

回放使用 VPS 本机的公网 Nginx listener，通过 TLS `--resolve` 命中 `8443`，完整经过随机路径、Nginx 和 CPA；请求体很小，未使用自动重试，也没有把响应内容或凭据写入证据。

- authenticated `/v1/models`：HTTP 200。
- OAuth `gpt-5.6-luna`：HTTP 200，约 1.3 s，exact OK。
- GLM `glm-5.3-flash`：HTTP 200；`max_tokens=16` 时 provider 返回空 `content`，提高到 `max_tokens=256` 后 HTTP 200、exact OK，说明该 reasoning channel 需要足够输出预算。
- `r1/gpt-5.6-luna`：HTTP 200、exact OK，约 21.2 s；仍标记为上游 relay 延迟风险，不以本地重试掩盖。
- `r2/gpt-5.6-luna`：HTTP 400、约 36 ms、model unavailable。
- `deepseek-chat`：HTTP 400、约 39 ms、model unavailable。
- 并发 30 个无认证 `/v1/models` 请求：23 个 401、7 个 429、0 个其他状态；新增 access log 行记录了 429，且没有包含随机路径。该测试只触发入口认证/限流，没有触发 provider。

## 本地验证与真值边界

- `powershell_parse=OK`、embedded Python AST parse=OK、`git diff --check` 无 whitespace error。
- `repo_verified`：是；`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1` 通过，`105 passed, 1 skipped, 40 subtests passed`，Bandit/Ruff/Mypy 均通过。
- `filesystem_projected`：是；脚本、README、AGENTS 和本 receipt 均在本地工作树。
- `host_loaded`：是；apply 后 fresh doctor 与服务/端口/模型目录回读通过。
- `controlled_live_replay`：是；上述受控公网路径回放通过。
- `live_accepted`：否；本次不是自然用户流量，也不是 provider 账号长期不封禁/不限流的证明。

## 回滚

只回滚本次实际修改的文件：从上述 backup 恢复 `config.yaml`、`auto-update.sh`、`cpa-gateway.conf`，执行 `chmod`、`docker restart cli-proxy-api`，再执行 `nginx -t && systemctl reload nginx`。本次没有新增或修改独立 logrotate 文件，因此没有 logrotate 文件回滚步骤；远端恢复不能由 Git 回滚替代。

## 决策依据与残余风险

- [CLIProxyAPI v7.2.154 config example](https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/v7.2.154/config.example.yaml)：用于核对请求重试、credential cooldown 和 request-scoped error 语义；本次选择 `request-retry=0` 以避免失败放大。
- [Nginx limit_req module](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)：入口限流是基于 key 的 leaky-bucket，不等同于 provider 或账号配额；因此只把它作为公网入口保护。
- [OpenAI Rate Limits](https://developers.openai.com/api/docs/guides/rate-limits) 与 [OpenAI Terms of Use](https://openai.com/policies/row-terms-of-use/)：重试应遵守 `Retry-After`，不能绕过 rate limits 或保护措施；未把多 key、伪装或 tunnel 当作规避方案。
- [智谱速率限制](https://docs.bigmodel.cn/cn/api/rate-limit) 与 [Coding Plan 使用须知](https://docs.bigmodel.cn/cn/coding-plan/usage-notes)：GLM/Coding Plan 的账号、模型和套餐限制仍由 provider 控制。

残余风险：Nginx 是 IP/连接级入口保护，不是 provider/model/account token bucket；`r1` relay 仍慢；OAuth/Coding Plan 通过公网 CPA 的条款适用性须按具体账号和地区确认；自然使用观察窗口仍未完成。
