# BWG CPA 裸名别名扩展：gpt-5.5 过渡回归 + gpt-5.3-codex-spark→deepseek-flash

- Scope: 仅 `bwg`。授权：用户 2026-09-17"按你的推荐/建议连续执行修复和优化，
  直至完成所有任务"。
- 动机：ChatGPT 官方 desktop 的 picker 条目清理无公开时间表且因人而异
  （GPT-5.2 服务端已 sunset 数月、条目仍在用户桌面显示）——`gpt-5.5` 条目
  在 10-14 后同样可能长期滞留，直接退役别名会让滞留条目点击 400。故将
  `gpt-5.5 → glm-5.3-flash` 别名**作为过渡保险加回**（与 `gpt-5.2` 并存），
  待桌面条目确认消失后择期清理。
- 第二项：desktop picker 若提供 "5.3-codex-spark" 条目，则该名成为 DeepSeek
  入口——`gpt-5.3-codex-spark → deepseek-flash` 别名。该名已被上游淘汰
  （当前 r1 自动发现目录无此模型）、无真身回归冲突；官方文档确认 5.5 于
  2026-10-14 全计划退役，spark 无独立下线公告（属较新 model drop）。

## 变更

- `config.yaml`（服务端 python 事务，断言→编辑→round-trip→原子写 600）：
  - zhipu-plan `models` 追加 `{glm-5.3-flash, alias: gpt-5.5}`（与 gpt-5.2
    并存）；
  - deepseek 条目 `models` 追加 `{deepseek-flash, alias:
    gpt-5.3-codex-spark}`。
- `cpa-health.py`（投影 sha `dd2705b3…`）：allowed 集 8→10（+`gpt-5.5`、
  +`gpt-5.3-codex-spark`）；smoke 仍 = sol；矩阵不变（spark/v4-pro 为注册
  不冒烟）。
- 契约同步：fixture（glm 三别名、deepseek 三别名）、guardrails 摘要
  （`has_glm_alias_52` 保留、恢复 `has_glm_alias_55`、新增 `has_ds_spark`）、
  测试目录夹具 10 模型集、runbook 裸目录基线。

## 验证

- 门禁全绿（132 passed / 1 skipped / 112 subtests + bandit/ruff/format/mypy）。
- 重启后 readiness 首试 `HEALTH_OK`；generation（sol）`HEALTH_OK` exit 0。
- 裸目录恰为 10 模型基线、总数 21（10 裸名 + 11 `r1/*`）。
- 单发：`gpt-5.5` 200/stop responded=**glm-5.3-flash**（过渡保险生效）；
  `gpt-5.3-codex-spark` 200/stop responded=**deepseek-flash**（DeepSeek
  desktop 入口落位）；`gpt-5.2` 200→glm、`deepseek-flash` 200→deepseek-flash
  （回归确认）。`NO_CDS_OK`；strict doctor `DOCTOR_CONTRACT_OK`。

## 回滚与边界

- 备份 `/root/cpa-alias-spark-20260917T132817Z/`（config.yaml + 前一版
  health）；回滚 = 拷回 + 重启 + revert 本切片提交。
- 两个别名均为"赌 picker 停留时间"的过渡设计：5.5/spark 条目从桌面消失后，
  分别按 cleanup 流程移除即可（每次为 30 分钟级契约同步）。
- `gpt-5.3-codex-spark` 若未来被中转站重新服务且用户需要真身，须先移除该
  别名再在 r1/裸名条目声明，避免裸名双凭据歧义。
