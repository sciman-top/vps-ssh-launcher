# BWG WorkBuddy scheduler_mode 回切 credits 与死别名摘除

2026-10-25 晚（北京时间）。两笔配置事务，均为维护锁 + 语义单键差分 +
原 inode 原地写 + fsync + 字节读回 + 热重载日志确认 + 备份回滚入口。

## 事务一：scheduler_mode selected → credits（14:23:18Z）

- 依据：三凭据同日先后吃到上游 6004 模型级限流（约 24h 窗，重置
  2026-10-26 12:16:03 / 12:34:44 / 18:16:04 UTC+8）。selected 把全部
  deepseek 流量钉在单一账号、叠加客户端重试风暴加速触顶；人工换号只是
  搬运同样的压力。credits 语义经 0.9.66 源码核实：粘性选择+故障回退
  （`active_auth.go:58-78`），执行器对 6004 park 的 (账号,模型) 对
  ms 级快败（`main.go:846/934`、`model_ratelimit.go`），park 按上游声明
  重置时间自然到期（issue #35 维护者口径：上游自身按模型维度表述限额）。
  自动回退不是账号保护；上游阈值、`max_inflight=1`、调用端
  `request_max_retries=0` 等减压边界不变。
- 事务：`/root/wb-credits-transaction-20261025.py`（dry-run 后 apply）。
  回执 `PREFLIGHT inode=558923` → `WRITE_OK inode_preserved` →
  `config_reload.go:148 config successfully reloaded` → `APPLY_OK mode=credits`。
  语义差分=仅 `plugins.configs.workbuddy.scheduler_mode`。
  备份：`/opt/cliproxyapi/backups/20261025T142318Z-workbuddy-credits/config.yaml.pre-credits`。
  回滚：备份字节原地写回 + 等重载。
- 行为验证（14:24Z，全池 6004 期间）：探针一 1.06s 内完成三凭据遍历
  （docker 日志三条 conductor 失败行；INTL#1 因 13:39Z 重启丢失 registry
  条目而吃一次真实 6004，另两凭据 ms 级快败）；探针二 42ms 被 admission
  `model_cooldown` 快败（60703c77 模型级 park 生效），零上游接触。
  glm/gpt-6-luna/gpt-6.1-sol 同期 200 正常。

## 事务二：opencode 死别名摘除（14:51:34Z）

- 事故：13:55Z 前后面板写入给 `api-keys.openai-compatibility[opencode].models`
  增加条目 `name: deepseek-v4.1-flash / alias: deepseek-v4.1-flash-open`
  （条目键序与 CPA 序列化风格不同，佐证面板 PUT 路径）。该别名：
  (a) 路由从未咨询——deepseek 请求只遍历 workbuddy 凭据（docker 日志实证）；
  (b) 上游不存在该名字——直连 opencode（curl，含会话头）返回
  400 `Model is unavailable`（beam-free 对照正常路径）；
  (c) 打红四个 doctor 门禁（semantic-policy / model-input-modalities /
  model-max-context-length / client-model-catalog-contract）。
- 事务：`/root/opencode-alias-removal-20261025.py`（首版 needle 假设扁平
  映射被 fail-closed 拒绝，修正为真实 list-of-dicts 两行块后 dry-run+apply）。
  回执 `WRITE_OK inode_preserved` → 重载日志 `provider updated: opencode
  (models 2 -> 1)` → `APPLY_OK opencode_models=beam-free-only`。
  备份：`/opt/cliproxyapi/backups/20261025T145134Z-opencode-alias-removal/config.yaml.pre-alias-removal`。
- 验收：strict doctor 复跑 `DOCTOR_CONTRACT_OK`（claude HELD 为已知惰性
  墓碑不阻断；workbuddy-config=OK；semantic-policy=OK）。

## 同窗口外部事件（不属于本次变更面，备查）

- `gpt-6-astra`（ai.input.im/v1）模型请求挂死 >45s：中继本身可达
  （根路径 200 0.55s），故障在 input.im 侧模型后端；VPS 侧无可修面。
- `gpt-5.6-terra`（http://35.213.82.91:8003/v1，同 input.im 家族）桌面端
  曾报 429，14:35Z 实测 200 恢复（2.9s）。
- `glm-5.3-flash` 晚间全量涌入后触发 zhipu 1308 配额冷却（上游 429，
  admission 阶梯冷却处理，自动恢复）。
- `gpt-6.1-sol` 14:50Z 实测 200（14.5s，较日间 3.5s 变慢=上游晚间负载），
  chatgpt lane 十分钟窗零拒绝；桌面 429 报告与并发撞 `inflight=2` 的
  瞬时快败一致。
- config.yaml 13:55Z 面板写入把文件从 6706 字节变为 6705（含死别名）；
  两次事务后为 6615 字节。当前 live 与仓内 policy/manifest 语义一致。

## 验证汇总

- 两事务 dry-run → apply 分离，均通过维护锁、空闲检查、语义差分单键、
  inode 保持、字节读回、重载日志。
- strict doctor 终态 `DOCTOR_CONTRACT_OK`。
- 仓内无代码变更；runbook 覆盖段更新（docs 切片：`git diff --check`）。
