# 2026-09-15 BWG CPA 健康契约收紧与 v7.3.4 canary 采纳收口

并行会话完成的切片 `9c897d8`（收紧 BWG CPA 健康检查与安全契约）经本会话复核、
门禁与真机投影收口；其中包含一次显式评审的 v7.3.4 canary 升级。本次只处理
`bwg`，未触碰 `zz`；未输出 key、随机路径或凭据材料。

## 切片内容（commit 9c897d8，本会话逐段复核）

- `cpa-health.py`：health 请求经无代理 opener 直连 loopback（不受环境
  HTTP(S)_PROXY 影响）；瞬态分类升级为 `TRANSIENT_HTTP_CODES`（408/429/500/
  502/503/504/520-526，与上游 transient 定义一致）；定时更新器的生成冒烟
  回到单代表 OAuth 路由（luna），全路由矩阵改为显式 `generation-all` 模式
  或 `CPA_HEALTH_ALL_ROUTES=1`；GLM 冒烟 `max_tokens` 提至 1024（推理模型
  冗余）；响应回显模型必须命中各目标的期望集合。
- `cpa-auto-update.sh`：候选选择锁定当前 major/minor 线内的最高 patch，
  minor/major 升级须独立评审 + canary（不再自动跨线）；`prune_backups`
  只统计/清理 `*-from-v[0-9]*` 命名的更新备份目录，不触碰外部目录。
- guardrails：identity-confuse 检查从文本 grep 升级为 config/auth JSON 的
  语义化深扫（含下划线键名与嵌套）；新增 `auth-permissions` 严格检查
  （auth 目录 700、`.json`/`.cds` 600）；端口绑定检查改为精确匹配
  （`exact-loopback-only`）；doctor 文件清单纳入 `cpa-health.py` 并新增
  `health=OK`（py_compile）；apply 投影 health 后做 py_compile 门控、KEY
  提取改 yaml 解析、新增重启后公网带认证探针（经 Nginx 必须 200）、
  末尾 catalog 摘要降级为非致命 WARNING。
- README 同步（major/minor 策略、generation-all 说明、RotatePath 文档
  由“128-bit”更正为 16 hex 字符）；测试同步（128 passed / 86 subtests）。

## v7.3.4 canary 采纳（并行会话执行，本会话取证）

canary 事务本身的完整记录（用户 2026-09-16 显式授权、镜像 digest、事务步骤
与 `generation-all` 五路由验收）见
[`20260916-bwg-cpa-v734-canary.md`](20260916-bwg-cpa-v734-canary.md)；本节
只记本会话独立取证到的关键事实。

- 备份目录
  `/opt/cliproxyapi/backups/20260915T164421Z-from-v7.2.158-canary-v7.3.4`
  以 `-canary-` 后缀显式命名，符合切片新增的“minor/major 须评审 + canary”
  策略；容器 16:44:23Z 重建。
- 运行版本 `v7.3.4`（commit `8335eac`，构建于 2026-09-15T14:07Z；与参考仓
  manifest 的 main HEAD 记录一致）。16:45:29Z `--check` 记录
  `CANDIDATE current=v7.3.4 target=v7.3.4`、`BACKUP_HEALTH status=ok backups=5`。
- 升级后验证：strict doctor `DOCTOR_CONTRACT_OK`（裸目录契约、auth 权限、
  端口精确绑定、identity-confuse 语义检查等全绿）；readiness 与 generation
  均 `HEALTH_OK`；目录 17 = 6 裸名 + 11 `r1/*`，无 7.3.x 新 provider 泄漏
  （声明式 `models:` 抑制自动发现的设计生效）。
- timer 下一腿 2026-09-21 04:24 UTC 不变，将是新 updater（`79ba8d23…`）在
  7.3 线内的首个真实 prune 腿。

## 本会话投影与验证

- 投影前基线 strict doctor 全绿（含全部新检查）；随后 `-Apply`：
  `GUARDRAILS_APPLIED`、`READY_STATUS=200`、备份
  `/root/cpa-guardrails-backup-20260915T164813Z`。
- 投影后远端 sha256 与本地 HEAD 一致：`auto-update.sh 79ba8d23…`、
  `cpa-health.py 79296e48…`；`config.yaml e8d8e9fb…` 与 nginx
  `a2853703…` 投影前后不变（幂等）。
- 目录摘要 `models=17`，七项契约标志全真；full gate
  `128 passed, 1 skipped, 86 subtests passed`，Bandit、Ruff check/format、
  mypy 通过；`git diff --check` 通过。

## 回滚与残余边界

- v7.3.4 回滚：备份目录内 compose/config/auth + 旧镜像按 updater 既有
  `restore` 路径恢复；guardrails 投影回滚 =
  `/root/cpa-guardrails-backup-20260915T164813Z`。
- 定时更新器从现在起不会自动离开 7.3.x 线；下一次跨线（如 7.4/8.x）必须
  走显式评审 + canary 命名流程。
- 已知接受项不变：error-*.log 含下游请求头（有界、loopback、root-only）；
  容器 root（上游镜像）；`auto-update.log` 无轮转（量级无害）。
- 观察项：中转站间歇 503 是否在稳定窗口内收敛；9-21 腿为“新 updater +
  luna 单路由冒烟 + 7.3 线内 prune”组合的首次真机验证。
