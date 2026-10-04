# CPA 错误转储取证（要不要为容量信号改 admission？）

适用问题：**上游的容量响应里，有没有一个 admission 认不出来的信号？**
这个问题决定 admission 契约要不要改，而它曾经被"从客户端症状反推"答错过。

工具：`scripts/cpa_error_dump_forensics.py`（只读）。

    # 本机文件
    ./.venv/Scripts/python.exe scripts/cpa_error_dump_forensics.py --file <dump>
    # 直接在主机上分析最新保留转储（只回传聚合，提示词不出机器）
    ./.venv/Scripts/python.exe scripts/cpa_error_dump_forensics.py --ssh-profile bwg
    ./.venv/Scripts/python.exe scripts/cpa_error_dump_forensics.py --ssh-profile bwg --json

退出码：`0` 每个容量响应都被识别；`1` 发现未识别信号（**这才是要改 admission 的判据**）；
`2` 输入不可读，**或文件里根本没有 `=== … ===` 响应段**（那不是原始转储，把它报成"无事发生"
会被读成体检通过）。

## 1. 它回答什么，不回答什么

| 它回答 | 它不回答 |
|---|---|
| 某个容量响应**具体长什么样**（状态码、`Retry-After`、标记） | 上游**为什么**过载 |
| 部署的 admission **会不会**把它计成容量 | 这次过载该不该切 lane（那是处置） |
| 有没有**落在词表外**的容量信号 | 端到端成功率（那是 `cpa_failure_triage.py`） |

词表来自**已部署的** `cpa-admission.json`（`--ssh-profile` 模式下是
`/opt/cliproxyapi/cpa-admission.json`），所以判定口径是"**部署的那道闸门会不会算它**"，
不是第二意见。

## 2. 两条硬规则（都是踩过的坑）

- **只读响应段。** 转储同时含请求体（用户提示词）。2026-09-21 的 doctor 审查就因提示词里
  出现 `server_is_overloaded` 而报出幻影标记。工具只在 `=== api error response ===` /
  `=== api response ===` / `=== response ===` 段内解析，且只输出**头名 + 有界值前缀**。
- **`--ssh-profile` 在主机上算完只回传聚合**，不把转储内容拉到本机。

## 3. 2026-10-04 实测结论（**不要重复推翻它**）

对 bwg 上 5 份保留容量转储逐份取证，**五份形状完全一致**：

```
Status: 503
Retry-After: 22 | 27 | 43 | 52 | 59
Content-Type: application/json

{"error":{"message":"auth_unavailable: no auth available (providers=codex,
 model=gpt-6.1-sol; last upstream error: server_is_overloaded: Our servers are
 currently overloaded. Please try again later.)",
 "type":"server_error","code":"internal_server_error"}}
```

⇒ 三个被提出过的"扩宽容量识别"猜想**全部被证伪**：

| 猜想 | 实测 | 结论 |
|---|---|---|
| 上游用 `x-ratelimit-*` 表达余量 | **0 次**出现 | 不要加这类头标记 |
| 上游用 `resets_at` / `resets_in_seconds` 表达窗口 | **0 次**出现 | 不要做"从体里推退避" |
| 上游用 `x-retry-metadata: NO_MORE_RETRY` 表达不可重试 | 本批 **0 次**出现 | 同上（客户端侧文案不等于网关侧信号） |
| 上游退避只走阶梯、`Retry-After` 路径是死代码 | `Retry-After` **存在**（22–59s），journal `retry_after=present` **527** 次 | 退避路径是活的，别当死代码删 |

`auth_unavailable` 是 **CPA 自己**的包装文案（`code=internal_server_error`），它**随 503 一起到**，
而 503 已在 `capacity_statuses` 里、体里又带 `server_is_overloaded` ⇒ **已被计数**。
**所以本轮没有为它改 admission，也没有 `-Apply`。**

交叉核对（admission journal，保留窗口）：

```
status=200 capacity=false 9507   status=200 capacity=true  93   ← 体标记路径
status=503 capacity=false  407   status=503 capacity=true 136   ← 状态码路径
status=502 capacity=false   93   status=502 capacity=true  59
status=429 capacity=true     9
status=404/400/401 capacity=false（全部）                        ← 路由/鉴权失败被正确排除
```

⇒ 容量识别**没有盲区**：所有 5xx/429 里被判为容量的，都能被"状态码或体标记"解释。

### 3.1 判定的四种取值（别把 `recognised` 读成"账号过载"）

| 判定 | 含义 | 退出码影响 |
|---|---|---|
| `RECOGNISED` | **闸门会把它算成容量**（命中 `capacity_statuses` 或已配置标记） | 0 |
| `UNRECOGNISED` | 有容量类词但**词表外** ⇒ 闸门会当成功放过去 | 1 |
| `other` | 不是容量响应（路由/鉴权/内容类），闸门正确忽略 | 0 |
| `NO RESPONSE SECTION` | 文件里没有 `=== … ===` 响应段 ⇒ **什么都没分析**，不是"体检通过" | 2 |

两个附注（只增信息，不改判定）：

- **`upstream cause:`** —— 从 CPA 包装文案里取出的**上游真实成因**。
- **`recognised by status only`** —— 体里没有任何已配置容量标记，只靠状态码被算成容量。
  **此时"识别"只等于"闸门会冷却这条 lane"，不等于"账号配额耗尽"。**

**实测对照（2026-10-04 12:42–12:44，slot 2 `codex.ciii.club` 的实时失败）**：

| 形状 | 判定 | 解读 |
|---|---|---|
| `Status: 502` + `{"type":"upstream_error","message":"Upstream access forbidden, please contact administrator"}` | `other` | 上游**拒绝服务**（路由/授权问题），不是账号容量；闸门忽略它是对的 |
| `Status: 503` + `Retry-After: 60` + `auth_unavailable … last upstream error: upstream_error: Upstream access forbidden` | `RECOGNISED`（**status only**） | CPA 把同一个上游错误包成 `auth_unavailable` ⇒ 状态码落进 `capacity_statuses`。**成因是路由不是容量** |

⇒ 这正是"只看状态码会把路由问题当容量"的实例。slot 2 没有 `admission_lane`，
所以本次无实际影响；**但若某个被 lane 覆盖的模型出现这种形状，整条 lane 会被路由问题冷却**，
属跨模型污染面（对照 `cpa-admission-risk-audit.md` 的 `lane-model-not-advertised`）。

## 4. 什么时候该动 admission

只有**同时**满足才动：

1. 工具报 `UNRECOGNISED`（退出码 1），且
2. 该信号在保留转储里**可复现**（不是单次样本），且
3. 能说明它代表**共享账号容量**而不是路由/鉴权/内容错误（后三类打开 lane 只会造成
   跨模型污染，见 `cpa-admission-risk-audit.md` 的 `lane-model-not-advertised`）。

改动属 **admission 契约变更**：同一提交改 `cpa-admission.py` + `cpa-admission.json` +
`test_cpa_admission.py` + 相关 runbook，并**必须 `-Apply`**——否则 doctor 的
`projection-drift` 会用 HEAD blob 比对已部署文件并报漂移
（`cpa_bwg_guardrails.ps1:203-216`）。

## 5. Do / Don't

**Do**

- 提"再加一个容量标记"之前**先跑这个工具**，把结论当证据写进提交信息。
- 报数时给"转储份数 + 是否含提示词污染 + 部署词表"。
- 只喂**原始转储**（含 `=== … ===` 段）；喂已抽出的响应体会得到 `NO RESPONSE SECTION` + 退出码 2。

**Don't**

- 不要因为客户端文案（"Selected model is at capacity"）就去加标记：**客户端名 ≠ 网关名**，
  客户端文案 ≠ 网关信号。
- 不要把 `auth_unavailable` 直接加进 `capacity_markers`：它已经随 503 到达，
  重复加只会让**路由/鉴权类**失败更容易开整条 lane 的冷却。
- 不要把 `RECOGNISED` 读成"账号配额耗尽"：先看 `upstream cause:` 和
  `recognised by status only` 附注——上游拒绝服务也会以 503 到达。
