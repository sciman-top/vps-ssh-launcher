# BWG CPA 目录简化：别名退役 + r1 前缀去重

- Scope: 仅 `bwg`。授权：用户 2026-09-17 指令（移除 gpt-5.2 / gpt-5.5 /
  gpt-5.3-codex-spark 指向其它模型的别名；r1/ 中已有裸名的 astra/sol/terra/
  luna 从列表隐藏，简化目录）。
- 背景：实测确认用户的 Codex desktop 动态读取 `/v1/models`（列表含 r1/* 与
  glm），别名对它并非必需；真名直达即够。r2 已永久移除、OAuth 保持登出。

## 变更

- `config.yaml`（服务端 python 事务，断言→编辑→round-trip→原子写 600）：
  - zhipu-plan `models` 收敛为仅 `{glm-5.3-flash}`（移除 gpt-5.2/gpt-5.5
    别名）；
  - deepseek 条目 `models` 收敛为 `{deepseek-flash, deepseek-v4-pro}`
    （移除 gpt-5.3-codex-spark 别名）；
  - r1 前缀条目新增 `excluded-models: [gpt-5.6-luna, gpt-5.6-sol,
    gpt-5.6-terra, gpt-6-astra]`——前缀视图去掉与裸名重复的四项
    （该机制 9/15 网关拆分已验证）。`r1/gpt-5.5` 保留（别名移除后它是唯一
    真 5.5，随 OpenAI 10-14 下线自然消失）。
- `cpa-health.py`（投影 sha `7b14f531…`）：allowed 集 10→7；smoke 仍 sol；
  矩阵不变。
- 契约同步：fixture（glm/deepseek 收敛）、guardrails 摘要移除
  `has_glm_alias_52/55`、`has_ds_spark` 三个死标志、测试目录夹具 7 模型集、
  runbook 裸目录基线。

## 验证

- 门禁全绿：132 passed / 1 skipped / 118 subtests + bandit/ruff/format/mypy
  （发现并修复一个未同步的测试夹具：`model` 变量名变体的目录清单）。
- 重启后 readiness `HEALTH_OK`；裸目录 = 7 模型基线（luna 因中转封锁处于
  冷却缺席，与本变更无关），`r1/*` 7 个（四项重复已消失），合计 13 可见 /
  14 稳态。
- 单发：`glm-5.3-flash` 200/stop；`deepseek-flash` 200/stop；退役裸名
  `gpt-5.5`/`gpt-5.2`/`gpt-5.3-codex-spark` 全部 400（预期）；
  `r1/gpt-5.6-luna` 400（excluded 生效）。`gpt-5.6-sol` 探针超时挂起（中转
  坏窗口）→ generation exit 10 暂缓（设计内，非契约失败）。`NO_CDS_OK`。
- strict doctor `DOCTOR_CONTRACT_OK`。

## 客户端影响与回滚

- desktop 刷新列表后：`5.2`/`5.5`/`5.3 Codex Spark` 条目将消失（目录已无此
  三名，客户端旧条目点击会 400，可从客户端删除）；GLM 请直选
  `glm-5.3-flash`；若当前选中模型是 5.5，刷新后需改选其它模型。
- 回滚 = 拷回 `/root/cpa-simplify-20260917T143820Z/`（config.yaml + 旧
  health）+ 重启 + revert 本切片提交。
- luna 仍待 r1 站点解封；解封后自动回目录（裸名 + 因 excluded 不再出现在
  r1/ 前缀视图）。
