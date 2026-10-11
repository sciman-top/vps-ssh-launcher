# 2026-10-11 bwg CPA codex 通道族 desktop 工具形状三规则（requests.payload）

## 背景与故障定性

ChatGPT desktop（Codex 引擎，Cockpit direct 模式）对 `muse-spark-1.3-contributor`
（受管 codex 通道族，原生 `/responses` 直通 opencode zen）发起请求时，客户端
工具声明经 CPA codex 执行器原样转达上游，zen 严格校验拒绝对话：

- `tool_search`（延迟工具发现）：`parameters.required=["query"]` 未含可选键
  `limit` → 400 `'required' is required to be supplied and to be an array
  including every key in properties. Missing 'limit'`（`param="parameters"`）；
- `web_search` 携带 `search_content_types:["text","image"]` → 400
  `search_content_types is only supported for web_search_preview tools`；
- `custom` 免格式工具（apply_patch grammar）→ 400
  ``custom` tools are not supported on this endpoint``。

二分取证（当日 20:28 失败转储的真实工具集逐类重放）：tool_search 单发即复现
第一错；namespace 组与平铺 function（含大量 required⊂properties 形状）均 200，
即 zen 只对上述三类形状校验；错误串不在 CPA v8.0.23 源码（上游产生）。CPA 侧
`RecordAPIRequest` 实证 275KB 请求体未改写、Upstream URL=
`https://opencode.ai/zen/go/v1/responses`。同目录 deepseek-v4.1-flash 不受影响
是因其走 openai-compat 执行器（chat 协议翻译消化了异形工具）。desktop 尝试
17:47/20:28/20:34/20:40/22:10/22:47 全部 400。

## 修复机制（纯配置，无 fork/无 shim）

CPA v8 配置级 payload 规则（`requests.payload.{override,filter}`，规则路径
支持 `tools.#(type=="tool_search")` 条件查询解析到具体下标；生效锚点
`internal/runtime/executor/codex_executor_request.go` `cacheHelper` →
`FinalizePayload`，构建上游请求前应用）：

- override：`tools.#(type=="tool_search").parameters.required` →
  `["query","limit"]`（满足 zen 的 required ⊇ properties 约束）；
- filter：`tools.#(type=="web_search").search_content_types`；
- filter：`tools.#(type=="custom")`（整个免格式工具删除）。

三条规则全部按模型名 `muse-spark-1.3-contributor` 匹配（清单派生），其他
lane（chatgpt-oauth/zhipu/deepseek 中转）零影响；请求不含该工具时路径不解析
= no-op。

## 部署路径与实测（本文件的脱敏证据）

管理 API 热改路径不可用：`management.secret-key` 是 bcrypt 哈希（`$2a$` 前缀
60 字符），明文密钥不在服务器（容器无 `MANAGEMENT_PASSWORD` env）。探针期间
5 次错哈希尝试触发 management handler 内存封禁 30 分钟（仅管理面；/v1 数据面
实测 200 不受影响，封禁随容器重启清除）。改走**文件级编辑 + 受控容器重启**：

1. 快照（`config.manual-snapshot-*.yaml` 四份，编辑前后各阶段均有）；
2. 文本级插入 `requests:` 段（其余字节不动），python yaml 复核顶层键集合
   仅增 `requests`；
3. 等静默窗口（90s 内 POST≤1）后 `docker restart cli-proxy-api`，两次重启均
   首轮健康（/v1/models 200），会话零中断观察无异常；
4. 分两步上线（先 tool_search+web_search 两规则，实测暴露第三雷 custom 后
   补第三条）。

验收（bwg 本机 8317，客户端 key，合成 16 工具形状=desktop 全集结构等价）：

- 修复前：全集 400（tool_search 错）、单发 tool_search 400、单发 web_search
  400、15 工具（去 custom）200、custom 单发 400；
- 修复后：**全集 200/completed**、tool_search 单发 200、web_search 单发 200；
- 回归：glm-5.3-flash 200、deepseek-v4.1-flash 200（每步部署后各测）。

## 仓内配套（同批提交）

- `scripts/remote/cpa_policy.py`：`EXPECTED_PAYLOAD_TOOL_RULES` 按路由清单
  派生（codex 通道族模型集合→期望规则结构），钉进 `EXPECTED_CONFIG_PATHS
  [("requests","payload")]`，缺段/篡改/丢 filter 项即 semantic-policy FAIL；
- `scripts/remote/cpa-health.py`：codex 路由生成探针每次携带三个工具形状
  （tool_search 原始严格形状 required=["query"]，非修复后形状），规则消失时
  health 直接红——行为级回归网；
- 夹具：`script_validation_support` 的 v8 fixture 经 runpy 从 policy 模块同源
  取期望值（深拷贝防污染），apply 事务测试实证排除合并变换保留新段。

## 已知边界与退役条件

- desktop 会话中 muse 的文件编辑退化为 shell 方式（custom apply_patch 被过滤）。
  custom→function 改写未采用：响应侧 function_call↔custom_tool_call 翻译在
  Responses 直通路径无 CPA 翻译器兜底（codex 执行器无 applyPatch state），
  desktop 是否兼容无法远程验证，不赌生产。
- 上游修复（CPA codex 执行器内置该清洗，或 opencode zen 放宽校验）经 updater
  自动收编后，删除三条规则与 policy 钉死即可（配置残留无害）。
- 回滚：恢复快照 config.yaml + docker restart；policy 会 FAIL 提示缺段
  （fail-closed 提醒，非阻断数据面）。
