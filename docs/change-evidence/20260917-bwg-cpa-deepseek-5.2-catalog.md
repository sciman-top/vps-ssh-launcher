# BWG CPA 接入 DeepSeek 官方通道 + GLM 别名 gpt-5.5 → gpt-5.2

- Scope: 仅 `bwg`；未触碰 `zz`。授权：用户 2026-09-17 指令（检测 env 中新增的
  DeepSeek key、可用则接入；整理渠道与裸名目录；退役 `gpt-5.5` 别名；
  OAuth 保持完全移除、以后需要再按 runbook 加回）。
- 背景：ChatGPT desktop 将于 2026-10-14 从 picker 移除 5.5，`gpt-5.5` 别名
  失去消费方；DeepSeek 当前官方模型为 `deepseek-flash` / `deepseek-v4-pro`
  （旧 deepseek-chat/reasoner 命名已不存在）。r2（codex.ciii.club）经用户确认
  放弃，保持移除。

## Key 检测与脱敏口径

- `- 副本.env` 仅本地取变量名与大小；整文件 b64 传至服务端解码（600），
  服务端解析 slot4 = `https://api.deepseek.com` + key，**值全程未进入会话日志**
  （URL 仅打印 scheme://host，key 仅打印是否非空）。检测：`GET /models` 返回
  `[deepseek-flash, deepseek-v4-pro]`；真实 `chat/completions`（max_tokens 8）
  200/stop/content OK。检测通过后才进入接入事务。

## 变更

- `config.yaml`（服务端 python 事务：断言→编辑→round-trip→原子写 600）：
  - zhipu-plan `models` 别名 `gpt-5.5` → `gpt-5.2`（GLM 上游不变）；
  - 新增 `openai-compatibility` 条目 `deepseek`（base-url
    `https://api.deepseek.com`，声明裸名 `deepseek-flash`/`deepseek-v4-pro`，
    key 结构镜像 zhipu-plan 条目，key 值来自 env slot4）；
  - r1 两个条目与其余键不动；openai-compatibility 顺序 = [zhipu-plan, deepseek]
    （条目顺序无路由效果，模型集两两不相交）。
- `cpa-health.py`（投影 sha `fd36f4e4…`，mode 700，py_compile OK）：allowed 集
  6→8（−`gpt-5.5`，+`gpt-5.2`/`deepseek-flash`/`deepseek-v4-pro`）；
  generation-all/quality-canary 矩阵追加 `deepseek-flash`（256 预算；luna 仍按
  既有设计保留首位）；smoke 仍 = `gpt-5.6-sol`。
- 契约同步：fixture（GLM 别名 + deepseek 条目镜像）、guardrails 摘要
  `has_glm_alias_55`→`has_glm_alias_52`、runbook 裸目录基线、测试（目录夹具
  8 模型集、矩阵 +deepseek-flash、预算断言按模型名取值）。

## 验证

- 门禁全绿（bandit/ruff/format/mypy 含 132 passed / 1 skipped / 112 subtests，
  其中一处旧断言"矩阵末位=GLM 1024 预算"随矩阵尾部变化改为按模型名断言）。
- 重启后 readiness 首试 `HEALTH_OK`；generation（sol 冒烟）`HEALTH_OK` exit 0。
- 裸目录恰为 8 模型基线、总数 19（8 裸名 + 11 `r1/*`）。
- 单发矩阵：`gpt-5.2` 200/stop responded=**glm-5.3-flash**（新别名命中）；
  `deepseek-flash` 200/stop；`deepseek-v4-pro` 200/stop；退役 `gpt-5.5` 400
  （预期）。全程 `NO_CDS_OK`。
- strict doctor `DOCTOR_CONTRACT_OK`。
- OAuth：本切片零改动、零残留（auth/ 无凭据文件；重登入口 = runbook）。

## 回滚与边界

- 备份 `/root/cpa-deepseek-5.2-20260917T125348Z/`（旧 config.yaml + 旧
  cpa-health.py）；回滚 = 拷回两者 + `docker restart cli-proxy-api` +
  revert 本切片提交。
- 10-14 前官方 desktop 若仍显示 5.5 条目，点击将 400（别名已退役）——改用
  `gpt-5.2`。deepseek-v4-pro 为 pro 档，单发生成延迟可见（约 5-15s），健康
  矩阵刻意只烧 deepseek-flash。
- DeepSeek key 来源：env slot4；如需轮换，改 env 后重跑本事务的 config 编辑步。
