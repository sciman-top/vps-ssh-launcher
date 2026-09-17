# BWG 图像家族目录排除 + fixture 验收桩确定性重设计与全场景通过

- Scope: `bwg` config 单键变更 + 仓库内验收脚本改造；生产零写入（fixture 在
  隔离 ns 内运行）。授权：用户 2026-09-17/18"按推荐连续执行直至完成"。

## 1. r1 前缀视图排除图像家族

- `r1` 条目 `excluded-models` 追加 `gpt-image-*`（通配符语义已在源码核实：
  `applyExcludedModels`/`matchWildcard`，大小写不敏感）。动机：ai.input.im
  站点账号组已不支持任何 gpt-image 模型（实测 404："not supported by any
  configured account in this group"），目录残留死条目。
- 结果：`r1/*` 前缀视图仅剩 `r1/gpt-5.5`（真 5.5，10-14 自然消失）+
  `r1/codex-auto-review`；裸目录 7 模型不变；总目录 9。
- 生效验证：restart 后 readiness HEALTH_OK、目录断言、`gpt-image-*` 零残留。

## 2. fixture 验收桩确定性重设计（根因修复）

- 根因（经 4 轮迭代定位）：验收桩的合成上游不服务 `GET /v1/models`（501），
  v7.3.4 对 codex-api-key 声明式模型的目录注册依赖不确定的回退路径——
  目录完整性非确定（0/6/7 模型），导致 success 场景的 pre-update 健康检查
  （裸目录严格相等断言）必失败。
- 修复（`cpa-update-acceptance.py`，commit ac4c3a0+）：
  1. docker stub 确定性就绪等待：SIGTERM 后轮询端口释放（≤20s）→ 启动新
     CPA → 轮询 `/v1/models` 200（≤45s，start_fail 场景按设计跳过）；
  2. 每场景 pre-state 门：以 updater 将使用的同一判定
     （`cpa-health.py generation`，exit 0 通过 / 10 重试 / 20 快速失败）
     轮询至健康（≤150s），消灭 pre-update 健康检查的竞速窗口；
  3. 保留 per-scenario 诊断（bare_diag + sol 直连探针）。

## 3. 全场景验收通过（最终认证）

- 完整运行（过载周期 62s + 4 场景）：`ACCEPTANCE_EXIT=0`。
- overload 503/upstream=1 → 冷却零放大 → 62s 同进程恢复 200 → 真实 health
  HEALTH_OK → start_fail/model_exposure exit1+回滚 → transient exit10
  defer → **success exit 0（升级采用，pre-gate 首试即过）**。
- 每场景 pre-state 目录 7 模型完整（确定性等待生效的直接证据）。
- 清理：`NO_FIXTURE_LEFTOVERS`；生产全程健康（readiness OK、`NO_CDS_OK`、
  容器 Up）。

## 4. 传输缺陷教训（操作层）

- ssh_tool `--command` 内联 base64 分块传输在单命令 >~14KB 时会出现静默
  字节损坏（三次复现）；跨 Bash 调用的变量不存活（空块追加）。
- 规范：每命令 ≤6KB b64（每片在**同一命令内**重算 base64 取片）→ 追加原始
  b64 到同一 .b64 文件 → 一次性解码 → `py_compile` + **全量 sha 比对**通过
  后才使用。

## 验证与回滚

- 门禁：132 passed / 1 skipped / 117 subtests + bandit/ruff/format/mypy。
- 生产巡检：readiness `HEALTH_OK`、`NO_CDS_OK`、容器 Up、零残留。
- 回滚：config 备份 `/root/cpa-image-exclude-<TS>/`（仅 excluded-models
  一键）；验收脚本改动经 git revert。
