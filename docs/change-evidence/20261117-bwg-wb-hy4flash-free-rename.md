# 2026-11-17 BWG CPA：WorkBuddy 免费模型客户端更名 hy4free → hy4flash-free

## 触发与决策

用户指令（2026-11-17）：过渡客户端别名 `hy4free` 命名的是已退役上游 id，继续使用会误导（选择器里
免费档现显示 "Hy4 flash"）。经裁决更名为 **`hy4flash-free`**（与上游 id 逐字节一致，"-free" 后缀
自文档化；判否 `hy4flash`——那是付费兄弟档 x0.03 的上游 id，占用必混淆）。本批是 luode0320 割接
（0706806）之上的更名收尾：割接时为保持客户端面逐字节不变，桥用 replace 别名 `hy4free` 顶着
`hy4flash-free`；本批撤销该顶替，免费模型改走裸名直通（与 `deepseek-v4.1-flash-sg` 同款）。

仓库面 commit `7f89df9`（23 文件：真源 manifest/admission.json、guard 预言机 doctor/apply/policy、
pins 工具契约 2→1、context overrides slug、cache 探针归一化、8 个测试文件、README 与 4 个 runbook）；
full gates `-RunParallel` 绿（**1722 passed / 31 skipped / 819 subtests**，首轮 lint:format 挡门
修复后重跑）。`cpa-admission.py` 一字未动（pin 保持 `fcf95564` 代；其两处注释仍提旧名属历史说明，
按 reviewed-deploy 铁律不因注释轮换代）。

## 远端事务（outputs/deploy-wb-hy4flash-rename-20261117.ps1）

两件同换 + 一处三子目 config.yaml 手术，同 inode 写盘，维护锁 + requests 排空锁 + 五 lane
check_idle + 备份 + 回滚 trap：

1. `/opt/cliproxyapi/cpa-admission.json`：INTL lane models 与 `zero_cost_models`
   `hy4free`→`hy4flash-free`（sha `c46bf5fc…`→`43e1a2c4…`）。
2. `/opt/cliproxyapi/cpa_provider_routes.json`：桥删除 `{name: hy4flash-free, alias: hy4free}`
   条目，仅存 intl fork 别名（sha `c6f62b24…`→`3e2d5fc9…`）。
3. `/opt/cliproxyapi/config.yaml` oauth: 块三处手术：
   - `model-alias.workbuddy-ai-provider`：整条目移除（桥 = intl 单条目，`workbuddy-alias-bridge=OK
     intl-only`）；
   - `settings.workbuddy-ai-provider`：仅摘 `alias: hy4free` 行（条目保留 name + max-context，
     镜像 manifest oauth_settings）；
   - `settings.workbuddy`（拆分前 legacy 通道）：条目名 `hy4free`→`hy4flash-free` 原地更名。
   回执 `CONFIG_SURGERY_OK`；diff-scope 断言仅 `oauth` 键变化；post-parse 断言桥精确 = intl 单条目。

pin/bootstrap/guard 全程未动（事务前后断言 `fcf95564`）。备份
`/opt/cliproxyapi/backups/20261117T052144.523472755Z-wb-hy4rename-20261117`（含此前四次失败回滚的
备份目录：051224/051429/051511/052019，均为 fail-closed REFUSE 后 `ROLLBACK_VERIFIED`，零损伤）。
`HY4RENAME_20261117_CONTRACT_OK`：model_lanes 归属、intl lane 模型集、zero_cost 集合、限流参数
逐项断言通过。

## 后续投影与验收

1. `cpa_bwg_guardrails.ps1 -Apply`：`PROJECTION_HASH_VERIFIED` 全部匹配（guard 模板携带新期望表），
   容器与准入服务健康重启。
2. 严格 doctor：`DOCTOR_CONTRACT_OK`、`MODEL_IDS_UNKNOWN=none`、`MODEL_CONTEXT_DRIFT=none`、
   `catalog_oauth_missing=none`、`workbuddy-alias-bridge=OK intl-only`、`workbuddy-config=OK`、
   wb 两 lane OPEN（claude-oauth HELD 为已知惰性墓碑）。
3. 受控单发（一发不重试）：
   - `hy4flash-free` 经 8318：**200 / terminal / TEXT='PONG'**，SSE 回显 `hy4-flash-free`（上游
     真身）；journal 权威行 `lane_admit lane=workbuddy-intl-oauth model=hy4flash-free` +
     `upstream_result status=200 protocol_result=completed`（first_text_ms=1526）。
   - `hy4free`：**400 `unknown provider for model hy4free`**（本地拒识，零上游触达）。
   - `/v1/models`：16 模型，`hy4flash-free` 在册、`hy4free` 消失。
   - credits 前后快照 26 项**零差分**（新上游仍 x0.00 免费）。

**ACCEPTANCE_RESULT=PASS**

## 事务手筋（后续必读）

1. **config.yaml oauth: 块同一模型名可能有三处落点**（settings legacy 通道 / settings provider
   通道 / model-alias 桥）。行级手术必须按子块定位，不能全文件首个匹配——首轮就因此误删 settings
   条目被 post-parse 断言拦下。
2. **config.yaml 会被带外写入方重排**（探针时桥条目 2 行，10 分钟后变 3 行且多出
   max-context-length 键）。手术语义按 post-parse 断言钉死，不钉行形状/行数。
3. `- name:` 起始的条目块整块 `yaml.safe_load` 会解析成**单元素列表**，需解包；通道头（`    key:`）
   与列表项（`    - `）同为 4 空格缩进，作用域边界必须用 `^    [A-Za-z0-9_-]+:` 正则区分。
4. `load_config` 把 `zero_cost_models` 规范化为 **frozenset**——契约断言用集合比较，勿按 list。
5. fail-closed 五连 REFUSE 全部 `ROLLBACK_VERIFIED`，证明备份+回滚 trap 的可靠度；生产写之前
   DryRun 不覆盖手术形状断言，形状类 bug 只有真跑才显形。

## 观察项

`hy4-flash-free-trial-202611` 促销窗（11-16→11-30）到期后若上游重新计价，`hy4flash-free` 名内的
"-free" 将反向失真；届时按模型退役铁律由用户裁决（admission 6008 窗口化可兜住过渡期）。
