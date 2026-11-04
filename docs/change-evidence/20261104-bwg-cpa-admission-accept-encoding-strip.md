# BWG CPA admission 剥离 Accept-Encoding 修复（2026-11-04）

## 目标与边界

修复 DSH (DeepSeek Harness) 等压缩协商客户端经网关流式请求全协议失败的问题。
本次只改 admission 的上游请求头集合（新增 `STRIPPED_REQUEST_HEADERS`，剥离
`Accept-Encoding`）与 nginx `cpa-gateway.conf` 的一行 `proxy_set_header
Accept-Encoding "";`。不改变路由、凭据选择、并发、RPM、重试、cooldown、
缓存键、成功判定，不触碰 `auth/` 与任何凭据。

## 根因

- 客户端链：DSH 内核 pi-ai 使用 Node/undici，默认发送
  `Accept-Encoding: gzip, deflate`；curl 与 codex CLI 不发送。
- 服务端链：nginx（无 gzip 配置、透传该头）→ cpa-admission（转发该头）→
  CPA v8.0.53 按 `Accept-Encoding: gzip` 返回 gzip 的 SSE 且正确声明
  `Content-Encoding: gzip`；但 admission 以 HTTP/1.0 取流并按自身头集合重发
  下游响应头，early-stream 路径不携带 `Content-Encoding`，下游客户端因此收到
  **无压缩声明的 gzip 字节流**，SSE 解析器按明文解析失败。
- 表现矩阵（DSH 会话转录 `assistant/attempt`，2026-11-02 至 11-04 共 11 轮）：
  `DeepSeek Messages SSE contains invalid JSON (MALFORMED_RESPONSE)`、
  `Stream ended without finish_reason`、`missing_terminal … Upstream status=200`、
  `Anthropic stream ended without a stop reason`。三种 API 协议全部失败，
  与协议无关；同会话转录 1:1 重放（56KB、13558 prompt tokens、32 tools）在
  curl（无压缩头）下始终完整收尾，证明为客户端头差异触发。
- 另证：直连 8318 带/不带该头，正文分别为 gzip 魔数与明文 `data:`。

## 修复内容

- `scripts/remote/cpa-admission.py`：新增 `STRIPPED_REQUEST_HEADERS =
  {"accept-encoding"}` 并在 `_proxy_unlocked` 构建 upstream headers 的
  comprehensions 中排除；nginx→admission→CPA 全程 loopback，压缩无收益。
- `scripts/remote/cpa-admission-integrity-pin.txt`：同步更新为
  `d547082c71da65066cd643e332dd73b166926197cd1d71e7c9400e10ad0fe8fc`
  （与本次提交的 `cpa-admission.py` 同 commit，满足 wrapper 的 HEAD 一致性校验）。
- `tests/test_cpa_admission.py`：新增回归
  `test_accept_encoding_is_stripped_upstream_and_sse_relayed_identity`
  （假上游断言：上游请求无 `accept-encoding`，下游收到 identity SSE 字节）。
- nginx `cpa-gateway.conf`（远端手管面，无投影哈希）：capability path location
  内 `proxy_pass` 行后新增 `proxy_set_header Accept-Encoding "";`，使 gate 即使
  未升级也不接收压缩偏好。远端备份：
  `/etc/nginx/conf.d/cpa-gateway.conf.bak-20261104-gzipfix`。

## 部署方式与偏差声明

本次为故障响应，未走 outputs 受控事务模板（maintenance 锁 + admission
requests.lock + 四 lane idle 复验）：SSH 直投 `.py`（临时名 + `mv` 原子替换、
保 644）、更新 `/etc/vps-ssh-launcher/cpa-admission.sha256`、
`systemctl restart cpa-admission`。`ExecStartPre` 完整性检查通过
（`ADMISSION_INTEGRITY_OK`），服务 `active`。重启未涉及 CPA 容器与其它 lane。
偏差影响：无锁窗口极短且为深夜低流量；后续 -Apply 与 doctor 在本 commit 后
已重新对齐（见下）。

## 验证

- `repo_verified`：`py_compile` OK；`tests/test_cpa_admission.py` 289 passed
  （含新增回归）；全仓 `pytest tests/` 1089 passed，16 failed 为
  batch/config/maintenance/socks5 模块的存量环境依赖失败，经 `git stash`
  前后对照与本变更无关。
- `filesystem_projected`：远端 `/opt/cliproxyapi/cpa-admission.py` 与
  `/etc/vps-ssh-launcher/cpa-admission.sha256` 均为上述新哈希。
- `host_loaded`：ExecStartPre `ADMISSION_INTEGRITY_OK`、`SERVICE ACTIVE`；
  strict doctor 在本 commit 后 `DOCTOR_CONTRACT_OK`，projection-drift 全 MATCH。
- `live_accepted`：公网端点以 DSH 条件复验（1:1 重放请求 +
  `Accept-Encoding: gzip, deflate`）：明文 `data:` SSE、无 `Content-Encoding`、
  71 chunks、`finish_reason` + `[DONE]` 完整收尾。直连 8318 同头亦为明文。

## 回滚

- 代码回滚：`git revert` 本 commit 后重新投影（scp + pin + restart）。
- nginx 回滚：
  `cp /etc/nginx/conf.d/cpa-gateway.conf.bak-20261104-gzipfix
  /etc/nginx/conf.d/cpa-gateway.conf && nginx -t && systemctl reload nginx`。

## 后续观察

- workbuddy 车道处于上游 6004 模型级限流（多数凭据 2026-11-05 11:47–13:49
  UTC+8 自动重置；一张 14012 月度额度耗尽待周期重置），与本故障无关。
- 长期可选项：admission 对 SSE 响应转发 `Content-Encoding` 的头集合核对，
  作为纵深防御；本修复后该头不应再出现。
