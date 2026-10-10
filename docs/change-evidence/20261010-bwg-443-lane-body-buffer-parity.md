# BWG 443 车道请求体缓冲与 SSE 传输契约对齐

日期：2026-10-10（Asia/Shanghai）。只处理 `bwg`，未连接或修改 `zz`。

## 结论

Desktop 实际入口（443 车道：`fq.sciman.top:443` → xray dokodemo-door →
VLESS REALITY → `fq.sciman.top:33387` → nginx `subscribe.conf`）仍停留在
8443 车道 2026-09-15 之前的传输形态：**缺 `client_body_buffer_size 128k;`**。
默认 `8k/16k` 下，超过缓冲的请求体会被 nginx 先写进
`/var/cache/nginx/client_temp/` 临时文件再转发给 admission，同时每条都在
`error.log` 留下 `a client request body is buffered to a temporary file`
告警。已在维护锁下按 8443 车道的既有取值补齐，并把该取值纳入 strict doctor
的 443 车道契约（与连接预算同样按「与 canonical 车道相等」比较，不钉字面量）。

## 证据链

### 1. 落后证据（只读）

`/var/log/nginx/error.log.1`（2026-10-09 00:00:14–11:09:09 UTC 之前一轮
logrotate 周期）内 `a client request body is buffered to a temporary file`
共 415 条，按 `host:` 字段分布：

```
234 host: "fq.sciman.top"        # 443 车道（desktop 实际入口）
181 host: "fq.sciman.top:8443"   # 直接指向 8443 的客户端
```

8443 车道的 181 条是 `client_body_buffer_size 128k` 之上（>128k）的请求体，
属既定残差；443 车道的 234 条里包含 8k–128k 区间，本属 8443 车道已消除的
形态。request 路径中的 capability prefix 在采集时即以
`sed -E 's|/[0-9a-f]{16}/|/<redacted>/|g'` 脱敏，未落盘明文。

### 2. 受控 A/B（同一请求体，经 443 车道本机 server block）

探针：VPS 本机 `curl --resolve fq.sciman.top:33387:127.0.0.1`，携带
CPA 客户端 key（`config.yaml` 的 `api-keys[0]`，不回显），向
`https://fq.sciman.top:33387/<prefix>/v1/chat/completions` 发一条
`glm-5.3-flash` 小完成请求，正文为无害填充。判定量 =
`grep -c 'client request body is buffered' /var/log/nginx/error.log` 的增量。

| 请求体字节 | 修复前告警增量 | 修复后告警增量 | HTTP | completion |
| --- | --- | --- | --- | --- |
| 40708 | 1 | 0 | 200 | True |
| 207208 | — | 1 | 200 | True |

40KB 落在 8k/16k 默认缓冲之上、128k 之下 ⇒ 修复前落盘、修复后不落盘；
200KB 仍在 128k 之上 ⇒ 修复后仍落盘，与 8443 车道同界，属记录在案的残差。
两次**修复前**读数在补丁前采集（同一条错误日志、计数器未轮转）。

### 3. 远端修复事务（受控写入）

`flock -n /run/vps-ssh-launcher-maintenance.lock` 内：

1. 前置断言 `limit_conn cpa_cc443 20;` 在位，且 `subscribe.conf` 尚无
   `client_body_buffer_size`；
2. 备份 `/root/cpa-443-lane-backup-20261010T033221Z/`
   （`subscribe.conf.before` + `.sha256`）；失败即 `cp -a` 回滚；
3. 锚定插入（`limit_conn cpa_cc443 20;` 之后同一 location 内），并用
   python 断言指令落在 CPA location 的 `{}` 范围内、且仅出现一次；
4. `nginx -t` 通过（输出留存 `$BAK/nginx-t.after.txt`），
   `systemctl reload nginx`；
5. 复验 `subscribe.conf` sha256 =
   `bf829939188e532203dda158cfb7b956241ecf50c3703a303292a3f78787eda6`。

修复后该 location 的关键指令序列：

```
limit_req zone=cpa_rl443 burst=10;
limit_conn cpa_cc443 20;
client_body_buffer_size 128k;
limit_req_status 429;
limit_conn_status 429;
auth_request /_cpa_auth;
add_header Retry-After $cpa_throttle_retry_after443 always;
proxy_pass http://127.0.0.1:8318/v1/$1$is_args$args;
proxy_http_version 1.1;
proxy_buffering off;
proxy_read_timeout 300s;
proxy_send_timeout 300s;
```

### 4. 契约冻结（仓库）

`scripts/remote/cpa-guardrail-doctor.sh` 的 `gateway-443-lane` 断言块扩展：
除连接预算与 429 状态外，新增

- `canonical_body_buffer` / `lane443_body_buffer` 相等（值取自两文件各自的
  `client_body_buffer_size` 指令，不钉 `128k` 字面量）；
- `proxy_buffering off;`、`proxy_read_timeout 300s;`、
  `proxy_send_timeout 300s;` 三条 SSE 传输指令在位；
- 成功行改为 `gateway-443-lane=OK conn-budget=<n> body-buffer=<n>`，失败时
  追加 `gateway_443_body_buffer=canonical:... lane:...` 诊断行；
- 车道未投影仍输出 `gateway-443-lane=ABSENT`（不新增 fail-closed 计数）。

`tests/test_cpa_guardrails_script.py`
`test_cpa_guardrails_doctor_asserts_443_lane_limiter_contract` 同步冻结上述锚点
与「不钉字面量」性质（`assertNotIn("client_body_buffer_size 128k;", doctor)`）。

## 变更内容

- 远端：`/etc/nginx/conf.d/subscribe.conf` 增加 `client_body_buffer_size 128k;`
  （见第 3 节事务与备份）。
- `scripts/remote/cpa-guardrail-doctor.sh`：443 车道契约扩展。
- `tests/test_cpa_guardrails_script.py`：断言同步。
- `docs/runbooks/cpa-gateway.md`：「443 车道的入口契约」小节补缓冲与 SSE
  传输项、A/B 读数与备份路径。

## 验证

- 断言块 A/B（抽取块 + 本机 fixture）：匹配 → `gateway-443-lane=OK
  conn-budget=20 body-buffer=128k`；预算 `6` → FAIL；缺 429 → FAIL；
  缺 `client_body_buffer_size` → FAIL 且打印
  `gateway_443_body_buffer=canonical:128k lane:absent`；缺
  `proxy_buffering off;` → FAIL；车道标记缺失 → `gateway-443-lane=ABSENT`。
- 远端 fresh strict doctor：`DOCTOR_CONTRACT_OK` 且
  `gateway-443-lane=OK conn-budget=20 body-buffer=128k`。
- 行为 A/B 见第 2 节。

## 证据边界

- 本改动消除的是**请求体落盘**与相应日志噪声；它不是上游生成速度、TPS 或
  卡片式响应延迟的成因，也未宣称提升 tps。
- `client_max_body_size` 仍是 `32m`；>128k 的请求体在两条车道上都仍会落盘
  （第 2 节第二行即该残差的实测边界）。把缓冲提到覆盖 32m 意味着每请求最多
  32m 常驻内存，本次不做该取舍。
- 探针经 `127.0.0.1` 进入该 server block，走的是与 desktop 相同的 nginx
  location、`auth_request` 与 admission 链路，但少了客户端到 `:443` 的 xray
  那一跳；xray 跳转本身不参与该缓冲判定。
- 不宣称 desktop UI 验收；UI 侧仍需用户确认。

## 已知残余

- 443 车道 `$remote_addr` 是 xray 新建连接的源地址（VPS 自身），per-IP 限流
  退化为全局桶。恢复 per-IP 需要 `xver=1` PROXY protocol 回退 + nginx
  `proxy_protocol`，属 v2ray-agent/xray 配置域，未改。
- `subscribe.conf` 由 v2ray-agent `install.sh` 生成，不属本仓投影清单；重装会
  删掉 CPA location 与该指令，由 `random-route-count`、`gateway-443-lane`
  双向 fail-closed 检出。

## 回滚

- 远端：`cp -a /root/cpa-443-lane-backup-20261010T033221Z/subscribe.conf.before
  /etc/nginx/conf.d/subscribe.conf` → `nginx -t` → `systemctl reload nginx`。
  Git 回滚不能代替远端恢复。
- 仓库：还原本次四个文件的改动；doctor 断言与 `-Apply` 投影互不影响。
