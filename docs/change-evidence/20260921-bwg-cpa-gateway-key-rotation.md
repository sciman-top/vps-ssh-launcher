# 2026-09-21 BWG CPA 网关 key 轮换闭环

## Scope

- 仅处理 `bwg` 网关客户端 api-key；未触碰 OAuth 凭据、路由、nginx 与冷却参数。
- 起因：交叉审查提出的本地泄漏复核。实测修正了前提——`agt_codex_*` 是本地
  Cockpit 服务（127.0.0.1:4163）的 agent token，泄漏集内的历史值已全部失效
  （400/401），活值不在泄漏集；**真实泄漏是网关唯一旧 key `16db...b170`**
  （4 处本地转录 + 1 个 config 备份）。

## Rotation timeline（全部服务端操作，key 不经会话）

1. **并存**：新 key `agt_gw_`（39 字符，24 字节熵，VPS 生成）追加进
   `config.yaml`（双 key 并存），热加载验证 loopback+公网 200；备份
   `config.yaml.bak-keyrot-20260921T073929Z`。
2. **消费方迁移（用户 + aliyun bot 会话）**：另一台 Windows 机与 aliyun
   的 astrbot 容器（19:25:37 CST 重启）及 watchdog（每 5 分钟从
   cmd_config 再生 env）先后切换新 key。
3. **归因确认**：usage-queue 按 `api_key` 字段分组——切换后窗口 74 条
   记录全部在新 key 名下（deepseek×17、luna×34、sol×18、terra×5），
   旧 key **零流量**，无残留持有者。
4. **移除与失效复验**：旧 key 行删除（备份
   `config.yaml.bak-keyrot-rmold-*`）后：loopback 新 200 / 旧 **401**；
   公网随机路径新 200 / 旧 **401**；`keys_in_config=1`。

## Fresh readback

- 变更后 strict doctor：`POLICY_OK`、`auth-permissions=OK`、
  `oauth_monitor=OK`、`DOCTOR_CONTRACT_OK`。
- 转录与静态备份中的旧 key 值自此在网关侧永久失效，无需逐文件清理
  （破坏性清理留用户决策）。

## Observation（记录在案）

- management `usage-queue` 记录的 `api_key` 字段为明文（源码
  `Principal: candidate.value`）。端点已有 loopback+management-key 保护、
  doctor 只输出聚合计数；未来新增 management 消费面时须记得该字段可
  一次性读走全部客户端 key。

## Rollback

- 恢复 `config.yaml.bak-keyrot-rmold-*`（双 key）或
  `-keyrot-20260921T073929Z`（原始单旧 key）；新 key 原文在
  `/root/cpa-gateway-newkey.txt`（600）。Git 回滚不涉及本变更（无仓库
  代码改动）。
