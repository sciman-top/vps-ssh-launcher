# BWG admission SSE 流式断流修复与受控实战验收（2026-09-26）

## Scope

- Target: BWG only。ZZ 未访问、未改动。
- 请求变更：修复 desktop 经 fq 网关调用时报
  `stream disconnected before completion: idle timeout waiting for SSE`
  与 `exceeded retry limit, last status: 429 Too Many Requests` 的根因——
  当日 12:12+08 上线的 admission 侧车（`7b8461e`）破坏 SSE 流式转发。
- 本仓提交：`dcc42c6`（framing 重建+心跳+断开检测+read1）、
  `90f72a5`（-Apply 补 admission 服务重启）、
  `8f3004a`（HTTP/1.0 上游消除停车）、
  后续（读线程+队列，消除断连期停车与 `response.close()` 阻塞）。

## Root cause（实验定案，非推断）

1. 旧版 admission 剥除上游 `Transfer-Encoding: chunked` 后按裸 body 转发，
   且 `read(65536)` 聚合——nginx 收到无长度信息、64KiB 猝发的流。取证：
   HTTP/2 真实路径（TLS+nginx+随机路径，desktop 完整链路）120s 仅收到
   7×64KiB 同秒到达后全程静默（curl exit 28）；绕过 nginx 直连 8318 同样
   64KiB 猝发（每 ~10s 一块）；直连 8317（CPA 容器）健康小块流
   （1691 reads×~260B）。池化点=admission，desktop 侧
   `stream_idle_timeout_ms` 默认 300s 被饿死断流。
2. 断流僵尸不被检测，占死 chatgpt-oauth lane 单飞锁（max_inflight=1）；
   desktop 重试排队 8s 超时即 429，重试耗尽。journal 实录
   05:26–05:28 三条 `lane_reject reason=queue_timeout`。
3. 上午 09:00–12:00+08 的 390×503（上游 OAuth lane 过载+紧重试）为叠加
   诱因，已自愈；ds/glm 快 lane 全程正常——上游无罪。
4. 部署链缺口：`-Apply` 投影用 `systemctl enable --now`，对已
   enabled+active 服务是空操作，新代码投影后进程不重启（06:20 投影、
   07:33 手动 restart 才生效的根因）。

## Windows 侧车三硬事实（测试线程栈实证）

1. http.client chunked 读在返回前必须预读下一 chunk-size 行——任何
   用户态缓冲门都拦不住 `read1` 在帧边界停车；Windows 上客户端 RST 与
   `shutdown()` 均唤不醒该 recv，lane 锁挂死到 1800s 读超时。
2. 心跳线程的 `shutdown(SHUT_RDWR)` 唤醒反使 select 误报可读，把主循环
   诱入上述停车（前版断开用例 80% 失败的根因，faulthandler 栈定案）。
3. `SocketIO` 一次超时即永久毒化（`_timeout_occurred` → 后续读直接
   OSError）——超时读方案不可行。

## Final design（`8f3004a` + 读线程/队列收官）

- admission 向 CPA 发 **HTTP/1.0** 请求（`conn._http_vsn=10`）：1.0 响应
  close-delimited 无 chunked，`read1` 退化为单次裸读（数据或 EOF 立即
  返回），结构性消灭块级预读停车；下游仍统一重建成 chunked（nginx 语义
  不变，SSE 判定收紧为 text/event-stream 且 chunked 或无长度）。
  生产只读实测确认 CPA 对 1.0 请求**不分块**（`HTTP/1.0 200 OK`、
  `chunked: no`、`eof_received: True`、含 `response.completed`）。
- will_close 下 `conn.sock` 被移交（None），真 socket 从
  `response.fp.raw._sock` 回退获取。
- 心跳线程（间隔=AdmissionProxy 构造参数，生产 15s）只发
  `: keepalive` SSE 注释行 + MSG_PEEK 探测 FIN/RST 置 `client_lost`
  信号，不再 shutdown。
- **收官（读线程 + 队列）**：`8f3004a` 虽消除了块级预读，主线程仍直接
  `read1()`，并被 `peek()` 就绪门（假阳性）诱入 park。收官改由
  `_read_upstream` 读线程独占全部阻塞 `read1()`，主循环只
  `queue.get(timeout=heartbeat_interval)`，超时复检 `client_lost`——
  **主线程结构性永不在读上游时 park**，断连最迟一个心跳切片释放租约；
  非 SSE 分支保持单次 `read1()` 循环。
- **清理不得阻塞租约释放**：读线程可能 park 在已交给 response 的单次
  连接 socket 上（Windows 无 API 唤醒）。实测 `conn.close()` 不阻塞，
  真正阻塞的是紧随其后的 `response.close()`（`fp.close()` 与读线程争
  同一 `BufferedReader` 锁）。故 `response.close()` 交由守护线程
  `join(timeout=2)`，租约释放无条件紧随其后——这是断连用例失败的真根因。
- `_retire_reader` 放弃不可唤醒的读线程（连接单次使用、随请求销毁，
  无跨请求数据丢失），并计入 `/healthz` 的 `retired_readers`、
  打 `retired_upstream_reader resident=N` warning，使回归可观测。
- `-Apply` 改 enable+restart 两步，重启失败走既有 restore_all 回滚；
  契约断言禁止 `enable --now` 回潮。

## Simulation / controlled live verification

### 模拟验收（本地双服务器单测，上游形态对齐 CPA 真实 close-delimited）

- admission 套件 13 用例 ×6 连跑全绿（原断开用例 80% 失败 → 确定绿）；
  新增：SSE 心跳注释行喂活静默流、客户端 RST 后 lane 锁及时释放、
  **断开早于上游 EOF 时租约仍在一个切片内释放**（HTTP/1.0 close-delimited
  上游形态，对齐 CPA 真实形态——原用例因关闭顺序恰好通过，掩盖了
  `response.close()` 阻塞导致的泄漏）。
- full gates：198 passed + 234 subtests + bandit/ruff/format/mypy 全过
  （2 failed 为本机沙箱已知项：`wsl.exe` Program Blacklist 与
  `test_scripts.py` 的 Unicode 解码，与本次改动无关）。

### 受控实战验收（生产 bwg，零重试单发探针）

- 部署：`GUARDRAILS_APPLIED`，三 admission 文件 `PROJECTION_HASH_VERIFIED`，
  备份 `/root/cpa-guardrails-backup-20260926T062035.276486667Z`（第一轮）
  与 07:53 轮备份；`-Apply` 后服务自动重启（07:53:09，重启契约生效）；
  `DOCTOR_CONTRACT_OK`。
- 循环连续性：loopback 8318 → 360 reads/35s 秒秒有数据（修复前
  8 reads×64KiB/80s）。
- 真实路径：HTTP/2+TLS+nginx 随机路径 → 393 data blocks/45s 连续流
  （修复前 7×64KiB 后静默）。
- 断开→锁释放：流中 inflight=1 → kill -9 客户端 → 锁 9s 内归零
  （生产心跳 15s，一个切片内；修复前挂死 1800s）。
- admission journal 自重启起零 ERROR/Traceback；期间的
  `lane_reject reason=cooldown` 均为验收探针自身触发熔断，保护按设计工作。

## Verification boundary

- 验收探针为 curl/urllib 模拟 desktop 形态（HTTP/2、流式、中断），
  非 desktop 本体——最终确认以用户实际 desktop 会话为准。
- fixture 二进制隔离 harness 未涉及（那是 CPA 本体升级验收路径，
  本次改动面为 admission 侧车，其模拟验收即本地双服务器单测）。
- 心跳仅在流静默超 15s 时注入；SSE 注释行按 RFC 8148 对事件语义透明，
  codex 客户端实测容忍（desktop 长会话待用户复确认）。
- 并行会话的 admission WIP（queue 架构版，framing 用例 flaky）保存在
  git stash（"parallel-session WIP admission queue-architecture…"），
  未合入。收官版虽同样采用「读线程 + 队列」，但是独立实现并覆盖了该 WIP
  缺失的一环——清理阶段的 `response.close()` 阻塞（见 Final design）；
  WIP 未作为基础复用，仍可安全丢弃。

## Rollback

- 代码：`git revert 8f3004a 90f72a5 dcc42c6` 后重跑 `-Apply`。
- 远端：`-Apply` 自带 restore_all 路径，或手动恢复
  `/root/cpa-guardrails-backup-20260926T062035.276486667Z` 中
  cpa-admission.py/json/service 三件后 `systemctl restart cpa-admission`。
- 诊断方法沉淀：nginx `cpa_safe` 日志按 status×IP×小时聚合 +
  `request_time` 慢桶 + error.log `upstream timed out` 关联连接 id；
  admission journal `lane_reject`/`upstream_result` 对照 desktop 报错
  时间戳；三分叉定位法（8317 / 8318 / 公网 HTTP/2 三点各测每秒到达
  分布）可直接锁定池化层。
