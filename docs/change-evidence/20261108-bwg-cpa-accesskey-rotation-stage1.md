# bwg CPA 网关访问键加法轮换（stage 1）回执 — 2026-11-08

> 动机：2026-11-08 复审会话做 config 脱敏投影时 `access.api-keys` 列表项进入会话
> transcript（交接书 [20261108-bwg-cpa-handoff.md](20261108-bwg-cpa-handoff.md) §8.5）。
> 用户授权按建议执行轮换。**键值不写入本文件**。

## 设计（加法轮换，零断流）

- `access.api-keys: [old, new]`，旧键保持 `[0]`：doctor 探针、updater 锚点与
  未重启客户端继续用旧键；新配置的客户端用新键。
- 激活走既有 `-Apply` 事务（idle 门 + drain + 重启 + 回读校验 + 失败回滚）；
  config.yaml 由 staging 同 inode 编辑写入，apply.sh 只改自己批准的字段集 →
  staged 键在投影与回滚路径中均存活（drain 超时的 restore_all 恢复的是
  已 staged 的备份）。
- 旧键移除 = phase 2，待用户确认全部客户端已重启后执行（届时新键自然成为 `[0]`，
  doctor/updater 探针自动跟随）。

## 执行回执（2026-11-08 17:59–18:06 +08:00）

- staging：备份
  `/opt/cliproxyapi/backups/20261108T095940-accesskey-add-stage1/config.yaml`
  （sha256 前缀 `360657da02ec98e8`，与交接书 §5.7 记录的基线一致）→ 同 inode
  追加后 `328298c730a780ab`；`cpa_policy.py` → `POLICY_OK`；`STAGED_OK count=2`。
- 激活：`cpa_bwg_guardrails.ps1 -Profile bwg -Apply` → `GUARDRAILS_APPLIED`
  （全事务含 drain/`docker restart cli-proxy-api`/keys[0] 就绪探针/模型目录契约）。
- 验证：8317 `/v1/models` `key[0]_probe=200`、`key[1]_probe=200`（count=2）；
  strict doctor `DOCTOR_CONTRACT_OK`（lane-hold OK / wb 0.9.88 /
  workbuddy-config OK / tls OK / nginx-syntax OK）。
- **全链验收**：经 nginx 8443 随机能力路径（16-hex，`location ~ ^/<16hex>/v1/`，
  `--resolve` 对齐 SNI）`/v1/models`：新键 `200`、旧键 `200`（nginx→admission→
  8317 全链；正确键不进 fail2ban 失败桶）。
- 客户端传播（本地 7 文件 10 处，各留 `*.bak-keyrot-20261108-*` 备份）：
  `~/.codex/auth.json`(1)、`~/.codex/config.toml`(2)、
  `~/.cockpit_tools/codex_model_providers.json`(2)、`~/.dsh/.credentials.yaml`(1)、
  `~/.claude/relay-token.json`(1，dormant 模板)、
  `~/.cockpit_tools/codex_provider_gateway_sidecars/36218dcc…/config.json`(1) +
  `manifest.json`(2)——sidecar 是首轮清单遗漏、由全盘 sweep 揪出的活跃面
  （对应 RotatePath 清单的 "sidecars" 环节，键轮换同样适用）。
- 旧键残留终扫：`ACTIVE_SCANNED=56343`，其余 81 处命中全部为历史件
  （`.codex/sessions` 转录、cockpit 每日自动备份、`_codex_verify_backups`、
  `archive/`、`*.tmp-*`），phase 2 移除旧键后自然失效，无需清理。
- 运行中实例回写观测：codex 桌面端 18:13 自行重写了 `auth.json`
  （`OPENAI_API_KEY` 值换成非网关值，codex 自管状态）与 `config.toml`
  （收敛为单网关 provider `fq_sciman_top`，文件内已是**新键**）。
  在 cockpit/桌面端全部重启前，内存持旧键的实例仍可能回写旧键——属预期，
  重跑 `propagate` 即可；旧键在 phase 2 前持续有效，零断流。
- 工具：`outputs/rotate-access-key-20261108.py`（scratch，不入库；键值全程经
  文件传递，不落 stdout/transcript）。

## 判否项（本轮一并定案）

- **workbuddy `reserve_credits` 不启用**。语义（cpa-multi-plugins v0.8.96
  README/CHANGELOG）= 缓存余额 ≤N 的账号退出调度，额度保给官方客户端应急。
  本池为纯消耗型（11-08 14:22Z 实证 3 份 intl 凭据同刻 6008 归零、无官方客户端
  消费面），启用会把日有效容量削去 N×14；且 6008 级联已被
  `max-retry-credentials=3` 收敛、本身不是封号信号。按 R5 判否，勿再提。

## 受控实战验收（同日补齐，新键 × 全链单发）

- 模拟层 `gate_na`：`cpa-acceptance.py` fixture 用内联配置（`fixture-client` 键、
  独立 netns），不消费生产 `config.yaml`，对本轮值变更无证明力；配置形状的模拟
  验收由 `cpa_policy.py`（POLICY_OK）+ strict doctor（DOCTOR_CONTRACT_OK）+
  模型目录契约（GUARDRAILS_APPLIED 内含）覆盖。
- 实战层：新键（keys[1]）经公网链 nginx 能力路径 → admission → 8317 → 上游，
  `/v1/messages` 单发 `max_tokens=16`、prompt "ping"：

| 车道 | 模型 | 状态 | 延迟 | usage 进→出 | stop |
|---|---|---|---|---|---|
| workbuddy-oauth | deepseek-v4.1-flash | 200 | 1.39s | 5→15 | end_turn |
| codex-oauth | gpt-6-luna | 200 | 5.68s | 7→5 | end_turn |
| antigravity-oauth | gemini-3.8-flash-high | 200 | 2.25s | 2→69 | end_turn |
| zhipu-apikey | glm-5.3-flash | 200* | 2.07s | 17→46 | end_turn |

  \* zhipu 首发在 `max_tokens=16` 内只产出 thinking 块（`text_ok=false`），
  属思考模型预算 artifact 非链路问题；追加 `max_tokens=96` 复发：
  `thinking(0 chars)+text(4 chars)`，end_turn。
- **活证据**：antigravity 单发请求 `gemini-3.8-flash-high`、上游响应模型
  `gemini-3.8-flash-n`——§8.4 的 Google 侧替换在本回执内被实弹复现，
  与 doctor `model_substitution` 7d 计数互证。
- 鉴权级先证：8317 双键 200/200、公网双键 200/200（见上文验证行）。

## 凭据卫生事件（补充）

- 实战探针构造时，随机能力路径（`/16hex`）曾一次进入会话 transcript
  （打印语句失误，已即改）。路径敏感度低于键（无键访问仅得 401 且计入
  fail2ban 失败桶）；如需消除，`cpa-guardrail-rotate-path.sh` 可随 phase 2
  一并执行，由用户决定。教训延伸：探针输出白名单只允许状态码/用量/延迟，
  URL 一律不打印。

## phase 2（用户门，未执行）

1. 用户重启 cockpit 托盘与 Codex/Claude/dsh 桌面端。注意：仍在运行、内存里持
   旧键的实例可能把旧键回写进配置文件——属预期，重跑 `propagate` 即可。
2. 用户确认后移除旧键：远端同 inode 编辑 `access.api-keys` 只留新键 → `-Apply`
   → doctor（探针自动改用新键 `[0]`）→ 本地 sweep 零残留。

## 回滚

- 远端：`cp /opt/cliproxyapi/backups/20261108T095940-accesskey-add-stage1/config.yaml
  /opt/cliproxyapi/config.yaml` 后重跑 `-Apply`（或 `docker restart cli-proxy-api`）。
- 本地：对五个文件执行 `cp <file>.bak-keyrot-20261108-* <file>` 后重启相应客户端。
