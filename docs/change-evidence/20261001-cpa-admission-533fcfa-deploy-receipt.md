# CPA admission 533fcfa 部署收据（并行会话投影，本会话只读复核）

日期：2026-10-01，本地时间 Asia/Shanghai。本收据补记 `533fcfaf24cf4944673bd054051cc288107378b5`（fix: preserve CPA backoff and bound FIFO lane admission）的实际部署事实，修正 `20261001-cpa-direct-gate-final-verification.md` 中"部署被 REFUSED、未投影"的过时结论：该阻断发生在 17:19，5 分钟后部署由另一并行会话完成，验证文档的最终只读检查（17:22）早于部署时刻，结论边界到此为止。

本会话未执行任何远端写入；以下全部为部署后 fresh read 证据。

## 部署事实（远端只读核实）

- `cpa-admission.service`：`ActiveEnterTimestamp=Thu 2026-10-01 09:27:53 UTC`（17:27:53 本地），`MainPID=249191`，`NRestarts=0`；替换前进程 PID 245060 / 启动 07:20:32Z 为 c1293db 版。
- `/opt/cliproxyapi/cpa-admission.py` SHA-256：`ffb5b28e56ec2559a8c9a0780ac44e1db3d38ad24cdd7081ec00e457fb0f8f74`（52230→56721 字节），与 533fcfa 候选源码 LF 标准化哈希一致（见 final-verification 文档）。
- 严格 doctor（2026-10-01T09:36:22Z）：`DOCTOR_CONTRACT_OK`、`POLICY_OK`、9 项 projection drift 全 MATCH；CPA 容器仍为 v8.0.8@sha256:7d7203c0，07:20:30Z 起未重启。
- 备份目录最新仍为 `20261001T072028Z`（c1293db 时点）：本轮 admission-only 替换未新增 guardrails 备份目录，回滚继续以该目录中 `478ffffa`（更旧版）与 Git 历史 `bc86a825`（c1293db 版）为准。
- 部署事务本身（写入与 service restart）由并行会话执行，未在本会话观察；本收据仅证明 filesystem_projected=true 与 host_loaded=true。

## 部署后行为（journal 观察，非注入）

- 旧版 2.5h 窗口内 71 次 `reason=half_open_probe` 拒绝在新版窗口（09:28Z 起）消失。
- 17:32–17:37 本地最后一轮上游容量阵发（sol `503 capacity=true retry_after=present`、`200 capacity=true`、`502 capacity=true` 共 6 次上游容量输入）：admission 梯度冷却（60→119s）→ 17:38:33 半开探针 → 17:40:36 探针 `200 capacity=false` → 解锁，恢复周期约 3 分钟，未出现上游已恢复但 lane 锁死的模式。
- 17:40:54–17:54（收据取数止点）：chatgpt-oauth lane 连续 46 个请求全部 200、无容量标记、零拒绝；排队等待从恢复初期 20.9s 积压消化至 0ms。

## 验收边界

- `repo_verified`/`filesystem_projected`/`host_loaded`：成立（如上）。
- `controlled_live_replay`/`live_accepted`：本收据不含；顺序单请求受控验收见同日后续记录（若 429/503 触发停止规则，按窗口未完成记，不判修复失败）。
- 上游单账号容量阵发本身不可由本仓修复；冷却期 429 为设计保护。luna/sol 共用单 OAuth 账号（`oauth_codex_files=1`）、`max_inflight=2` 为容量天花板的结构性事实，维持不动。

## 回滚

同 final-verification 文档口径：取得远端写入授权后，用维护锁确认部署文件无后续变更，从备份或 Git 恢复目标版本 `/opt/cliproxyapi/cpa-admission.py`（保留 755），语法检查后重启 `cpa-admission.service`，复验 `/healthz`、8318 loopback 与严格 doctor。
