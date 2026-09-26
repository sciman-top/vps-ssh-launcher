# 诊断交接：admission SSE 断连期资源泄漏（已闭环）

**状态**：**已修复并提交（`fa33117`）**，本地验收通过；**远端未投影**
（远端仍是 `8f3004a`，需当次显式授权才 `-Apply`）。
**对象**：`scripts/remote/cpa-admission.py`。
**一句话**：lane 租约泄漏的**两个**成因都已消除——主线程停车的诱因
（`peek()` 假阳性）与清理阶段的阻塞点（`response.close()` 争锁）。

---

## 收官结论（`fa33117`）

1. **主线程不再读上游**：SSE 路径改「专用读线程 + 队列」。`_read_upstream`
   独占全部阻塞 `response.read1()`；主循环只 `queue.get(timeout=心跳)`，
   超时回顶复检 `client_lost`。断连后**最迟一个心跳切片**释放租约
   （生产 15s）。`_buffered_upstream_data()`（peek 就绪门）已整体删除。
2. **清理不得阻塞租约释放**：`conn.close()` 实测**不阻塞**；阻塞的是紧随
   其后的 **`response.close()`** ——`fp.close()` 与仍在 `read1()` 中的读线程
   争同一 `BufferedReader` 锁。故 `response.close()` 交守护线程
   `join(timeout=2)`，租约释放**无条件**紧随其后。
3. **不可唤醒的读线程**：`_retire_reader` 记录并放弃（连接单次使用、随请求
   销毁，无跨请求数据丢失），计入 `/healthz` 的 `retired_readers`。
4. **生产前提已验证**：只读实测 CPA 对 HTTP/1.0 请求返回
   `HTTP/1.0 200 OK` / `chunked: no` / `eof_received: True`，含
   `response.completed`。§3 之外的 chunked 边界当前不可达。

### 为什么会被漏过一次

原用例 `test_client_disconnect_releases_lane_lease_during_upstream_silence`
在 `8f3004a` 上**恰好通过**：其上游关闭顺序使 `response.close()` 不与读线程
争锁。新增 `test_lane_sse_lease_released_when_client_leaves_before_upstream_speaks`
覆盖「**断开早于上游 EOF**」——上游发一帧后静默 30s、客户端 RST——即刻暴露
泄漏。**教训：断连回归用例必须让「断开」发生在「上游结束」之前。**

---

## 以下为修复前的诊断记录（保留作方法论）

## 0. 已确认修复的部分（HEAD 有效）

`finally` 里 `lease.release()` 已排在 `conn.close()` 之前：

```python
finally:
    if lease is not None and lane_name is not None:
        proxy.lanes[lane_name].release(...)   # 先放租约
    if conn is not None:
        conn.close()                          # 再收连接
```

这样**即使** `conn.close()` 卡死，租约也不再被拖住。

## 1. 仍存在的缺陷：连接处理线程永久泄漏

**复现**（`outputs/dbg24.txt`，未改仓库，5 次客户端中途断连）：

```
baseline handler threads: 0
after disconnect 1: handlers=2 inflight=1
（此后 55s 内无变化）
```

`inflight` 会随实现版本时好时坏（`dbg23`：`0.3s→未释放 / 1.0s→0.05s / 3.0s→未释放`，
**不确定**），但**线程数只增不减**（`dbg23` 与 `dbg16` 均可见）。

**栈证据**（`outputs/dbg16-final.txt`）：

```
handle_one_request:424 <- do_POST:714 <- _proxy:647 <- close:1027 <- close:435 <- _close_conn:428
```

`_proxy:647` 即 `finally: conn.close()`；`_close_conn` 是
`HTTPResponse._close_conn()` → `self.fp.close()`。
该线程**永远不会返回**，每个断连请求泄漏一个线程 + 一个上游连接。

## 2. 为什么 `conn.close()` 会永久阻塞

`http.client` 的 `HTTPResponse` 把 `self.fp` 包在同一个 socket 上。
`fp.close()` 需要拿到该 `BufferedReader` 的锁，而**另一个读操作**若仍 park
在 `recv()` 上就永远放不开。

**Windows socket 语义（本轮多轮实验定论，`/tmp/dbg13..21`）**：

| 假设「能唤醒另一线程 park 的 recv」 | 实测 | 出处 |
| --- | --- | --- |
| `shutdown(SHUT_RDWR)` | **不能** | `dbg2`/`dbg21` |
| `sock.close()` | **不能**（`reader woken=False in 3.0s`） | `dbg21` |
| `conn.sock = None` 后再 `conn.close()` | **不能**（仍耗时 29.5s） | `dbg19` |
| `settimeout` 分片等待 | 会**永久毒化** `SocketIO._timeout_occurred` | `dbg7` |
| `BufferedReader.peek()` 判可读 | **假阳性**（chunked 会读到下一 chunk-size 行） | `dbg13` |
| `select()` on `conn.sock` | **盲区**（预读后数据在 fp 缓冲，裸 socket 不可读） | `dbg13`/`dbg15` |

→ **没有任何 API 能从线程 A 打断线程 B 中 park 的阻塞 `recv()`。**

## 3. 修复方向

### 3.1 主修：主线程不得做任何阻塞读（消除 §6 的假阳性死锁）

这是**根本**修复。回到「专用读线程 + 队列」：

```python
# 读线程独占 response.read1()，永不归还阻塞读给主线程
def _read_sse() -> None:
    try:
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            sse_queue.put(("data", chunk))
    except (OSError, http.client.HTTPException) as exc:
        sse_queue.put(("error", exc))
    finally:
        sse_queue.put(("eof", None))
```

主循环只等队列（可中断、可复检 `client_lost`）：

```python
while True:
    if client_lost.is_set():
        break
    try:
        kind, payload = sse_queue.get(timeout=heartbeat_interval)
    except queue.Empty:
        if time.monotonic() >= read_deadline:
            raise socket.timeout("upstream SSE idle")
        continue
    if kind == "eof":
        break
    if kind == "error":
        raise payload
    chunk = payload
    read_deadline = time.monotonic() + SSE_READ_TIMEOUT_SECONDS
    _write_chunk(chunk)
```

**关键**：分支判据必须是 `sse_queue is not None`，**不要**再叠加
`and upstream_sock is not None`——那样在上游 `Connection: close`
（`conn.sock is None`）时又会落到主线程 `read1()`，与读线程双读
（`IncompleteRead`）。同时删除 `_buffered_upstream_data()` 与
`select([upstream_sock], ...)`：peek 假阳性已是实测死锁源。

### 3.2 配套：连接回收不得阻塞或与读者争锁

```python
finally:
    heartbeat_stop.set()
    if heartbeat_thread is not None:
        heartbeat_thread.join(timeout=2)
    if sse_reader is not None:
        _release_upstream()                  # 尽力唤醒（不可靠）
        sse_reader.join(timeout=1)           # 有上限，不强求
    if lease is not None and lane_name is not None:
        proxy.lanes[lane_name].release(...)  # 必须在 conn.close() 之前
    if conn is not None:
        # conn.close() 可能永久阻塞（§2）：交给守护线程，主线程立即返回。
        threading.Thread(target=conn.close, daemon=True).start()
```

### 3.3 注释订正

`_release_upstream()` 的注释宣称 `shutdown()` 能「唤醒」已 park 的读，
该行为在 Windows 上**不成立**（`dbg21`：`sock.close()` 也不能）。
应改为「尽力而为的提示，实际依赖读线程自身的超时/退出」。

## 4. 已加入的回归测试

`test_cpa_admission.py::test_lane_sse_stream_completes_when_upstream_closes_connection`
覆盖「lane 模型 + 上游 `Connection: close`」：上游在 `getresponse()` 后即
`conn.sock is None`，此时若就绪判断把「裸 socket 不可读」当成「无数据」，
或发生 check-then-read 竞态，客户端就收不到完整 body。
现有 11 个用例无一覆盖此组合。

## 6. 断连释放失败的**确切机制**（最终定位，HEAD=`90f72a5`）

已用插桩副本拿到逐行时序（`/tmp/fz4-run4.txt`）。主循环：

```python
if client_lost.is_set() or _client_gone():   # 562 行，唯一的退出检查点
    break
if not _buffered_upstream_data():            # 569 行，peek() 判断
    ready, _, _ = select([upstream_sock], [], [], heartbeat_interval)
    if not ready:
        continue
chunk = response.read1(65536)                # 578 行，可能永久阻塞
```

**两条路径，取决于 `peek()` 的瞬间结果：**

- **通过路径**：`buffered=False` → `select` 超时 → `continue` → 回到 562 行看到
  `client_lost=True` → `break`。租约释放。
  （插桩实跑：`loop top client_lost=False` → `buffered=True` → `read1 -> 11`
  → `loop top client_lost=False` → `loop exit`，1.47s 通过。）
- **失败路径**：`_buffered_upstream_data()` 返回 **True**（`BufferedReader.peek()`
  对 chunked 流的**假阳性**）→ 跳过 `select` → 直接进 `read1()` →
  **永久 park 在 `recv()`**（静默上游 + Windows 下 `shutdown()` 不能唤醒）→
  永远回不到 562 行 → `client_lost` 形同虚设 → **租约 + 线程双泄漏**。

**这就是波动性的来源**：是否阻塞取决于检查瞬间 `peek()` 是否看到已缓冲字节。
`hb=0.3/1.0/3.0` 三次实测分别「未释放 / 0.05s / 未释放」（`dbg23`）；
同一份字节相同代码在仓库与副本间表现相反（`e7a368c6` 两处均试），
加一条 `logging.warning` 就能翻转结果——**典型的时序竞态**。

**结论**：`_buffered_upstream_data()`（peek）这一"优化"是**净负收益**。
它想避免「裸 socket 看似空闲但缓冲里有数据」的假阴性，却引入了
「缓冲看似有数据但 `read1` 仍会阻塞」的**假阳性**，而假阳性的代价是
永久死锁。正确做法是**不要用 peek 做就绪判断**，回到
「专用读线程 + 队列」模型：读线程负责一切阻塞读，主线程只做
`queue.get(timeout=...)`，超时可中断、可复检 `client_lost`，
从根本上消除「主线程 park 在 `read1()`」的可能。

## 7. 并行会话冲突警告（重要）

`scripts/remote/cpa-admission.py` 在约 40 分钟内：
- md5 变化 ≥ 7 次
  （`f053e610 → dcc42c6 → 4e8d4e30 → dd1ff46b → aaa419d7 → cbd00531 → 78ed75bd → a2fb51fe → e7a368c6`）
- 期间出现 `TMPDBG` 插桩（`outer finally before close` / `sse=%s sock=%s ...`）
- 设计在两个方案间**来回切换**：`select+peek` ↔ `reader线程+queue`
- **本轮我对该文件的改被覆盖**（14:50 后 md5 回退，我的守卫修复消失）

**测量纪律**：任何复现/验证必须先**冻结副本**再 `runpy.run_path`，
否则会读到半写文件、得到互相矛盾的结论（本轮早期已发生多次）。

**协作建议**：两个会话在同一文件上并行修改是当前最大的风险源；
应择一负责该文件，另一方只出诊断与证据。
