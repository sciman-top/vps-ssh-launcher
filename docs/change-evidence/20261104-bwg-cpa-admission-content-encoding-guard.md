# BWG CPA admission 上游 Content-Encoding fail-closed 守卫（2026-11-04）

## 目标与边界

同日 `8828cf7`（剥离上游 `Accept-Encoding`）的纵深防御：即使未来配置回退或
上游行为变化再次产生编码响应，admission 也必须 fail-closed，而不是把客户端
无法识别的 gzip 字节当 SSE 明文转发（当日 DSH 全协议断流的失效模式）。
只加响应头检查与两条既有错误通道的复用，不改路由、凭据选择、并发、cooldown。

## 变更

- `scripts/remote/cpa-admission.py`：`_proxy_unlocked` 在读取上游
  `Content-Type` 后检查 `Content-Encoding`；非空/非 identity 即拒绝——
  streaming（early）路径复用 `_send_stream_failure` 发
  `code=upstream_content_encoding` 的终态 SSE 错误事件 + `[DONE]`，
  非 streaming 路径回 502 JSON 同 code；均记
  `upstream_content_encoding_rejected` warning 日志。lease 经既有 finally
  以 `successful=False, capacity_error=False` 释放（不触发车道冷却）。
- `scripts/remote/cpa-admission-integrity-pin.txt`：同步更新为
  `8b0e009a91d66574621c5c04fd3b2012283e275be1cf821f1c36f1497b0be353`
  （与 py 同 commit）。
- `tests/test_cpa_admission.py`：新增
  `test_upstream_content_encoding_fails_closed_on_sse`（假上游发
  `Content-Encoding: gzip` 的 SSE：断言下游无 gzip 魔数字节、含终态错误
  事件与 `[DONE]`、车道不进入 hold）。

## 验证

- `repo_verified`：`py_compile` OK；`tests/test_cpa_admission.py` 290 passed
  （含两个 2026-11-04 新回归）。
- `filesystem_projected`：scp 投影 + pin 原子更新 +
  `systemctl restart cpa-admission`；ExecStartPre `ADMISSION_INTEGRITY_OK`。
- `host_loaded`：直连 8317/8318 明文 SSE 正常；strict doctor 合同保持 OK。
- `live_accepted`：正常路径由同日 DSH 真实客户端确认（见
  `20261104-bwg-cpa-admission-accept-encoding-strip.md`）；守卫路径为
  单元级验证（生产上游被 Accept-Encoding 剥离保护，正常不再产生编码响应，
  无需也不应人为在生产触发）。

## 回滚

`git revert` 本 commit 后按同-commit 契约重新投影 py+pin 并重启 admission。
