# 2026-09-13 BWG CPA 更新与公网验证

- 授权：用户要求自主更新 BWG CPA、必要配置优化、风险控制与 key 可用性验证。
- 范围：仅 BWG `/opt/cliproxyapi/{config.yaml,compose.yml,auto-update.sh}`；未连接 ZZ。
  保留公网 Nginx 8443、随机路径、现有客户端 key、上游凭据、模型命名与定时周期。
- 时间：2026-09-13 10:05 UTC 更新成功；随后公网验证与重复执行成功。

## 依据与变更

- 原版本 v7.2.154；原 timer enabled/active，但此前 systemd 最后执行为 09-08。
  原脚本只对最新 tag 检查三天观察期，会在持续发布时跳过已成熟的新版本。
- 官方最新 v7.2.159 尚未满 72h；本次选择两处均已满 72h 的 v7.2.156。
  镜像 `eceasy/cli-proxy-api:v7.2.156` 固定 digest
  `sha256:7f434559781aa011bbfa1061757fb5fbb4d44190e453f5bb1cb20076d9c6581e`。
- 更新脚本真源 `scripts/remote/cpa-auto-update.sh`；GitHub/Docker Hub 双重版本检查、
  最高成熟 semver、禁止降级、flock 互斥、完整 Compose 备份、一次生成 smoke、失败回滚。
  保留旧镜像和备份，不自动清理历史备份；后续需要按磁盘容量另行审查保留策略。
- 保留 request-retry=0、max-retry-credentials=1、save-cooldown-status=true、
  force-model-prefix=true、fill-first；启用 session-affinity=true / ttl=1h。
  显式 disable-cooling=false；quota-exceeded 的 switch-project、switch-preview-model、
  antigravity-credits 全部 false，避免配额不足时自动切模型或额外 credits 回退。
  未覆盖调用方 reasoning effort，未开启 identity-confuse。
- 官方说明表明 session affinity 绑定 session 到 credential；本机各主要通道目前各只有
  一个 credential，故该选项不是本次缓存命中的唯一原因，也不能据此声称相对基线提升。

## 回滚与更新过程

- 维护前备份 `/root/cpa-maintenance-20260913/`：config、Compose、updater、auth、timer/service。
- 第一次健康检查在异步 auth 注册前读到不完整目录，自动恢复旧 Compose/镜像；外层事务
  同时恢复配置/updater，并确认 v7.2.154 及 11 个模型恢复。这是真实失败回滚证据。
- 修复为限时轮询目录直到必需模型就绪；生成检查仍只有一次。第二次 systemd 更新成功：
  ExecMainStartTimestamp=10:05:05 UTC，ExitTimestamp=10:05:12 UTC，ExecMainStatus=0。
- 成功更新备份 `/opt/cliproxyapi/backups/20260913T100507Z-from-v7.2.154/`。
  回滚本次维护：恢复维护前目录的 config.yaml、compose.yml、auto-update.sh 到部署目录，
  然后在 `/opt/cliproxyapi` 执行 `docker compose up -d --force-recreate --pull never`，
  再跑 strict doctor 和授权生成检查。auth 备份仅应在确有凭据文件损坏时单独恢复，
  避免覆盖更新后的 OAuth token / cooldown 状态。Git revert 不会回滚远端。

## 当前证据

- strict doctor：DOCTOR_CONTRACT_OK；401/404/404 路由契约正确，管理远程访问禁用，
  8317 仅 loopback，Nginx 配置 hash 与维护前相同。
- 本机直连公网（无代理、无 SSH 转发、TLS 校验启用）：8443 可达，8317 不可达。
  现有 1 个客户端 key `/v1/models` HTTP 200；错误 key HTTP 401。
- 目录更新后 14 个模型；新增 3 个 r1 图像模型来自新版静态目录，不代表这些模型已验收，
  本次未发送图像生成请求。裸模型仍只有 Luna / GLM。
- 公网 `chat/completions` 各一次：Luna HTTP 200 / 2.08s，GLM HTTP 200 / 1.91s，
  r1 Luna HTTP 200 / 4.83s；均 exact-OK、非空，响应模型符合请求通道。
  更新前 r1 为 27.04s，不能把这两个样本解释为稳定延迟改善。
- 两次相同合成前缀、相同 prompt_cache_key 的公网 Luna 请求：均 HTTP 200，
  prompt_tokens=3039；cached_tokens 依次为 0、2816（第二次约 92.7%），
  1.76s / 1.69s。只证明该受控请求缓存可用，不代表整体命中率或质量基准。
- systemd updater 再执行：success / exit 0，容器 ID 不变；timer 仍 enabled/active。
- 本地/远端 updater SHA-256 均为
  `23c11d46ad2e17b90140c87fe19a2ace8b20f316b68c212298948fc56b714d28`。
- nginx、xray、fail2ban、ssh、cron 均 active；现有 10r/s、burst 20、每 IP 6 连接保留。
  Nginx 限流不是上游账号 token bucket；本次未做高并发压力测试。
- 本地门禁：build、pytest、Bandit、Ruff、format、Mypy 通过；真实 SSH integration
  默认跳过，由本次显式授权的单机远端和公网验证提供实际证据。
  全套首次为 107 passed / 1 skipped / 44 subtests；就绪时序修复后追加并通过
  2 个 focused updater tests / 5 subtests，覆盖成熟版本选择、不降级、异步目录加载、
  生成失败不重试；对最终测试文件再次执行 Ruff/format/Mypy 及 git diff --check。

## 官方来源与边界

- [v7.2.156 release](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.2.156)：
  新增 model-level cooling 等能力；本次没有启用该选项来缩小配额冷却范围。
- [固定版本配置说明](https://github.com/router-for-me/CLIProxyAPI/blob/v7.2.156/config.example.yaml)：
  核对 retry、cooldown、session affinity 和 quota fallback；只读参考，未复制外部实现。
- [官方 release API](https://api.github.com/repos/router-for-me/CLIProxyAPI/releases?per_page=100)
  与 [既有部署镜像元数据](https://hub.docker.com/v2/repositories/eceasy/cli-proxy-api/tags/v7.2.156)：
  版本日期与 digest 交叉验证；复用现有镜像供应链，没有克隆或安装社区项目。
- 证明层级：远端加载成功、同一 systemd 入口手动触发成功、公网受控调用成功；
  下一次自然 timer tick、长期质量/命中率与上游账号安全仍未被本次短时验证覆盖。
- 历史记录曾披露 key 进入旧本机会话日志；本次不打印 key，保持兼容未轮换。
  若需要撤销历史暴露，须同步更新所有客户端，单纯服务器加固不能撤销旧 key。
