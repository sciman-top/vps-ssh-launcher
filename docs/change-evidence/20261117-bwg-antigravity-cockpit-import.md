# 2026-11-17 BWG antigravity cockpit 导出账号入池（sw\*\*\* + kn\*\*\* 双新号）

## 授权与边界

- 用户请求把 cockpit 导出的 2 个 Antigravity 账号（sw\*\*\*@gmail.com、
  kn\*\*\*@gmail.com）导入 CPA；其 UI 上传报 `invalid auth file: auth file
  payload must be a JSON object`。获授权做根因定位、SSH 导入与验收。
- 写入面仅 `/opt/cliproxyapi/auth/` 两个**新增**凭据文件；零 config/
  manifest/投影/admission 变更，容器零重启（watcher 热加载）。

## 根因与最小 schema

- cockpit 导出顶层是 JSON 数组 `[{email, refresh_token}, ...]`（仅两字段），
  CPA 上传解析（`parseV8AuthMetadataObject`）要求单 object → 报错。
- 源码核对（上游 checkout c19787b2，与生产 fork v8.0.75-gemini-raw 零差距）：
  synthesizer 对 metadata 原样入账、antigravity 无必填校验；executor 自愈链
  `ensureAccessToken`（refresh_token→oauth2.googleapis.com）→
  `PrepareRequestAuth`（loadCodeAssist→project_id）→ capability hints/credits
  按账号取回。故最小单对象
  `{"type":"antigravity","email":...,"refresh_token":...,"disabled":false}`
  即完整等价。

## 写入与热加载

- `antigravity-sw***@gmail.com.json` / `antigravity-kn***@gmail.com.json`：
  本地生成（HG03-work 暂存，令牌走 shell 变量内插未进 transcript）→ SSH
  base64 落盘（staging 用非 .json 后缀 tmp + mv 原子落位，chmod 600 对齐
  存量）。备份不适用=纯新增；回滚=删两文件（watcher 自动注销）。
- watcher CREATE+WRITE 各 2 条 incremental 加载；落盘 ~215B，约 1 秒被 CPA
  主动富化回写至 ~1325B，字段集与在役 10 字段完全一致（access_token/
  project_id/capability_hints 全部到位）＝逐账号凭证级活体证据。
- 窗口内 0 error / 0 TOS_VIOLATION / 0 invalid_grant。池终态 7 凭据
  （UI 23→25）。

## 验收（受控实战）

- 复跑 `outputs/run-antigravity-singles-postswap-20261117.ps1`（每发前后
  道态门禁）：sonnet/hydrogen/flash 三连 200 / terminal / 'PONG'，全程
  `hold=None gen=0 inflight=0 pending=0`，journal 逐发
  `upstream_result status=200 successful=true`。
- 归因（selector LCP 绑定日志）：三发分别落在存量号 ga\*\*\*（×2）与
  agi.phys；新号未被推理命中。定向推理需改路由权重=生产写入，不做；推理级
  证明留给自然路由或 UI 逐账号"刷新额度"。新号凭证级活体证据（refresh/
  project/capability 实时取回）已足以上池。

## 边界与遗留

- sw\*\*\* / kn\*\*\* 按[封号潮风控模型](../../runbooks/)执行头 7 天观察窗
  （盯 403 TOS_VIOLATION / invalid_grant / 429 计数）。
- cockpit 与 CPA 共用同一 refresh_token（Google 不因使用轮换，可并存）；
  cockpit 侧重登/撤销会连带失效 CPA 侧凭据。
- management key 在 config.yaml 仅存哈希，服务端无法直调管理 API；UI 上传
  后备文件与源导出均在 `C:\Users\sciman\HG03-work\`（仓库根保持零敏感文件）。
