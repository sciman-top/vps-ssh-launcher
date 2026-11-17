# 2026-11-17 BWG antigravity 凭据换血验收（mamo 移除 + 双新 pro 入池）

## 授权与边界

- 用户自主操作：删除 VALIDATION_REQUIRED 中招凭据（mamo***@gmail.com）、
  新增 2 个 IDE 实证可供 gemini-4-hydrogen-xhigh 与 claude-sonnet-5-5-high
  的 pro 账号；本会话获授权做连续验收与收尾（备份+单发+台账）。
- 本会话零远端服务面变更：不改 config/manifest/投影/凭据文件；唯一写入是
  `/root/auth-backups/` 备份副本与 3 发回环验收请求。

## 只读取证（04:42–04:44 UTC）

- 车道未落锁：`AG_HOLD=None gen=0`，五道 hold 全 null、全 idle——上午的
  operator-review 锁释放后未再落锁（mamo 在场期间无 hydrogen/sonnet 请求
  打到它）。
- 换血事件链（容器日志，邮箱全程遮蔽）：mamo REMOVE（04:38:54 UTC）干净，
  删除后零残留引用（唯一 grep 命中即 REMOVE 行本身）；新凭据 WRITE 全部
  incremental 热加载成功；窗口内 0 TOS_VIOLATION / invalid_grant /
  VALIDATION_REQUIRED。
- 池终态 5 凭据：3 老（2×sc\*\*\* + agi.phys，~1274B）+ 2 新 pro
  （~1580B），全部 04:13–04:41 UTC 刷新成功。hydrogen/sonnet 由单点承重
  变双 pro 承重。

## 写入（备份）

- `/root/auth-backups/antigravity-<sha8>-backup-20261117.json` ×5（文件名
  以内容 sha8 标签替代邮箱；cp -p 保元数据；sha256 列表留档于会话回执）。

## 验收（outputs/run-antigravity-singles-postswap-20261117.ps1）

- 三模型顺序单发，每发前后做道态门禁（hold=None 且 idle，异常即中止）：
  - `claude-sonnet-5-5-high` → 200 / terminal / 'PONG'（1.0s header）
  - `gemini-4-hydrogen-xhigh` → 200 / terminal / 'PONG'（1.5s）
  - `gemini-3.8-flash-high` → 200 / terminal / 'PONG'（6.6s，waf+选路）
- journal 逐发 `upstream_result status=200 successful=true`；三发后道态仍
  `hold=None gen=0`。

## 边界与遗留

- **doctor 本轮刻意未跑**：工作树含并行会话未提交 WIP（routes/doctor 模板
  均脏），工作区锚定的严格 doctor 会比对新未部署模板 → 10-18 假 FAIL 先
  例；本切片纯凭据级（零 config/manifest 变更），按 R6 以单发+道态+刷新证
  据验收，doctor 归并行会话收口其切片时执行。
- 新入池 2 号按封号潮风控模型执行头 7 天观察窗（盯 403 TOS_VIOLATION /
  invalid_grant / 429 计数）。
- mamo 的 Google 账号挑战本身未解除（账号仍在 Google 侧要求验证）；如日
  后重入池，先完成验证再进，防 403 落锁复发。
