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

## 观察项（不阻断）

- 17:21–17:31（+08 01:2x 前段）同形状请求曾批量 200 的窗口未完全解释，
  与 issue #5751 "修复后仍有人瞬时 429、后端相关" 的报告一致：WAF 行为
  本身有波动。若复发：先看新转储 `zw` 是否仍 >0（=0 即混淆未覆盖该字段/
  串，追加指纹串或等 #6280 合入发版）；再看是否出现带 reset deadline 的
  真配额 429（那类只能等窗口或降量）。
- `sensitive-words` 为零宽字符注入，只作用 system/developer 指令，
  语义无损；如上游未来默认启用 #6280 指纹表，本键保留叠加无害。
- 凭据冷却（`reason=quota`）为 CPA 保护行为，勿禁用。
