补充一条更有说服力的对照：把「经本地 gateway」和「直连上游」放在**同一条请求形态**下比 —— 因为「修复前 vs 修复后」只能说明差异存在，而对着**正确基线**比才能说明「已经恢复正常」。

用长输出实测（同一上游、同一模型，79 个 `response.output_text.delta`）：

| 路径 | headers | socket 读取次数 | delta 跨度 | **每 token 间隔** |
|---|---|---|---|---|
| 经本地 gateway（本补丁） | 1141 ms | 90 | 2351 ms | **30 ms** |
| 直连上游（对照） | 1215 ms | 55 | 2197 ms | **28 ms** |

即修复后 **gateway 的吐字节奏与直连基本一致（30 vs 28 ms/token，约 7% 开销）**，响应体字节数与事件数也完全一致（27929 / 27863 字节，79 个 delta）。

（两条路径的 `first_delta_ms` 是 34626 vs 11061 ms —— 这个差异来自**上游推理期**，与 gateway 无关；同一上游在不同时刻本来就会抖。）

<details>
<summary>复现方式</summary>

用标准库写一个流式探针：`POST /v1/responses`（`stream: true`），**边读边解析**，
记录每个 delta 是由哪一次 socket 读取送达的 —— 这才是客户端实际感知到的吐字节奏；
再统计 headers 到达时间、socket 读取次数、delta 跨度与每 token 间隔。两种路径各跑一次即可对照。

</details>
