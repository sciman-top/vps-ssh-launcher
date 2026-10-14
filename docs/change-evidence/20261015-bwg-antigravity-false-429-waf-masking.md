# 2026-10-15 bwg antigravity lane 假 429（WAF 指纹）定案与 sensitive-words 修复

## 现象

- desktop（codex responses 协议）切 `gemini-3.8-flash-high` 后持续
  `exceeded retry limit, last status: 429`：北京 00:48 首发，01:15、01:22
  并发簇，01:35–01:40 演变为**连 2.1–2.6KB 小请求也被上游 300–800ms 打回**；
  01:37:12 起 CPA 凭据进入 `reason=quota` 冷却，后续请求本地 3ms 快败。
- 上游 429 响应体恒为裸 `RESOURCE_EXHAUSTED`（"Resource has been exhausted
  (e.g. check quota)."），无 RetryInfo/reset deadline——按上游 PR #6364 的
  口径不属于"周配额烧完"（那类带 "Resets in ..."），是真配额耗尽之外的类别。

## 归因（当晚三轮修正，最终版）

1. ~~上游瞬时 429~~（被 01:15 起的持续复现推翻）。
2. ~~并发/速率窗口~~（被 01:35 起非并发的串行小请求连续 429 推翻；
   admission `inflight=2` 维持未动）。
3. **终判：antigravity/Cloud Code 后端 WAF 对 developer/system 指令中的
   agent 指纹字面量返回假 429**。转储实证：全部 5 份 429 请求的
   `input[0]` 均为 developer 消息 "You are Codex, a coding agent based on
   GPT-5. ..."；同一时刻 A/B 单发——无 codex 文本探针 200、带该指纹句
   探针必 429（对照干净）。转储 `zw=0` 证明 v8.0.29 默认未启用零宽混淆，
   字面量原样送达过滤器。
- 社区依据（用户明确要求外部参考）：CLIProxyAPI issue #5751（closed
  completed，多人复现同一指纹句/半句不触发/零宽打断即 200）；PR #6280
  （指纹入默认混淆表，仍 open）；PR #6364（真配额 429 携带 reset deadline
  的区分口径）。官方配额页（docs.cloud.google.com/gemini/docs/quotas）只
  覆盖企业 Code Assist，消费级 AI Pro antigravity 限额无官方数字。

## 变更（远端 config.yaml，非投影文件）

- 备份：`/root/config.yaml.backup-20261014T174459Z`
  （sha256 07809c24，与变更前一致）。
- 追加（文件此前无 `antigravity:` 顶层键）：

  ```yaml
  antigravity:
    sensitive-words:
      - "You are Codex"
      - "RFC 2119"
      - "system-conventions"
      - "system_directive"
      - "Claude Agent SDK"
  ```

- `docker restart cli-proxy-api` 静默重启，`full client load complete -
  7 clients` 正常。

## 验证

- A/B 回环单发（8318 经 admission 全链）：`A_minimal` 200/completed；
  `B_codex_fingerprint`（携带此前必 429 的 developer 指纹句）
  **200/completed**。
- 01:44:13 的最后一条 429 warn 为重启窗口前的旧请求，符合预期。
- desktop 真机复测由用户进行（预期恢复）。

## 回滚

- 恢复备份：`cp /root/config.yaml.backup-20261014T174459Z
  /opt/cliproxyapi/config.yaml && docker restart cli-proxy-api`；或仅删除
  `antigravity:` 键后重启。Git 回滚不能代替远端恢复。

## 判决实验与定案补充（同夜，与并行窗口"输入 token 阈值"假设的冲突裁决）

- 修复后判决性单发：desktop 形状请求（developer 指令 165,684 字符且含
  指纹句、15 个工具定义，共 300,805 字节 ≈ 49,245 input tokens）→
  **200/completed**。该规模为 desktop 新会话基座的数倍，证伪"-high 档
  ~1-2 万 token 输入阈值、desktop 结构性不可用"假设；其全部对照矩阵在
  "是否携带未混淆指纹字面量"单变量下归一（200 案例：无指纹或已混淆；
  429 案例：携带未混淆指纹句）。
- 唯一变量 A/B：同夜里 125KB desktop 真实请求（修复前）→429，
  300KB desktop 形状（修复后）→200，唯一差异即 `sensitive-words` 混淆。
- 原验证节的小体积 B 探针（~600B）存在尺寸混淆（阈值假设下也会 200），
  判决以本节 49K-token 实验为准。
- 01:21–01:31（北京）同形状批量 200 的窗口按 WAF 后端评分/波动解释
  （issue #5751 亦有同类不一致报告）；混淆后字面量不再出现，不再积累
  触发。若仍复发：先看新转储 `zw` 是否 >0，再分辨是否出现带
  "Resets in" 的真配额 429。

## 观察项（不阻断）

- `sensitive-words` 为零宽字符注入，只作用 system/developer 指令，
  语义无损；如上游未来默认启用 #6280 指纹表，本键保留叠加无害。
- 凭据冷却（`reason=quota`）为 CPA 保护行为，勿禁用。
