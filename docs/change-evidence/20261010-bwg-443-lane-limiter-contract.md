# BWG 443 车道入口限流契约修复（Desktop 实际入口）

日期：2026-10-10。本地时间 Asia/Shanghai（UTC+08:00）。

## 结论

ChatGPT desktop 实际使用的入口是 **443 车道**，而不是 8443。
`fq.sciman.top:443` 由 xray 的 dokodemo-door 接入，转发到 VLESS REALITY
inbound（`realitySettings.target = fq.sciman.top:33387`），最终落到 nginx
`/etc/nginx/conf.d/subscribe.conf`（`listen 33387 ssl`）。这条车道就是
2026-09-30 以「后备车道」身份上线、repo 当时只按 `1 + lane_count` 计数容忍的
那条路径（见 `20260930-bwg-cpa-catalog-11id-consolidation.md`），因此它的入口
限流一直停留在 8443 车道的历史版本上：

1. `limit_conn cpa_cc443 6`：8443 车道在 2026-10-02 已按三条 admission lane 的
   持有预算升到 `20`，443 车道没有同步。
2. 未声明 `limit_req_status` / `limit_conn_status`：落入 nginx 默认的 `503`，
   与上游过载在状态码层面无法区分，且不带退避信号。

两项均在 2026-10-10 于维护锁下修复，命令行/日志侧有 A/B 行为证据；strict
doctor 现在按「车道存在才校验」冻结这两点。

## 证据链

### 1. 拓扑（只读）

- `ss -ltnp`：`*:443` 属 xray（pid 41475），nginx 无 443 监听。
- `/etc/v2ray-agent/xray/conf/07_VLESS_vision_reality_inbounds.json`：
  `dokodemo-door` 443 → `127.0.0.1:45987`；reality inbound
  `realitySettings.target = fq.sciman.top:33387`。
- `/etc/nginx/conf.d/subscribe.conf`：`listen 33387 ssl`，
  `server_name fq.sciman.top`，含 `location ~ ^/<16hex>/v1/(.*)$`、
  `/_cpa_auth` 与 `cpa_safe443` 访问日志。
- access log 中的外网 503 来自该车道（`144.34.x.x ... status=503
  upstream_status=503 auth_status=200 retry_after=seconds`），确认桌面流量确实
  经由 admission 返回上游容量错误，而非本地拒绝。

### 2. 修复前的限流拒绝形态（受控负例）

VPS 本机对 `https://fq.sciman.top:33387/<prefix>/v1/models` 并发 21 个未认证
请求（`--resolve` 到 127.0.0.1，`$remote_addr=127.0.0.1` 桶，不占用外网客户端
桶，且 127.0.0.1 在 fail2ban `ignoreip` 内）：

```
11 401
10 503     # limit_req 拒绝，nginx 默认状态码，无 Retry-After
```

### 3. 修复

在 `/etc/nginx/conf.d/subscribe.conf` 的 CPA location 内、`limit_conn
cpa_cc443` 之后插入 `limit_req_status 429;` 与 `limit_conn_status 429;`。
同一次受控事务（flock `/run/vps-ssh-launcher-maintenance.lock`）内：备份
`/root/cpa-443-lane-backup-20261010T025904Z/`（含 `subscribe.conf.before` 与
`.sha256`）→ Python 锚定替换 → `nginx -t` → `systemctl reload nginx` →
复验。失败即回滚备份。

连接预算 `6 → 20` 在本次之前同日的另一个受控事务中完成，备份
`/root/cpa-443-lane-backup-20261010T025311Z/`；两者都属同一车道，一并记录。

### 4. 修复后的同一负例

```
11 401
10 429
HTTP/2 429
retry-after: 1
```

### 5. 契约冻结

`scripts/remote/cpa-guardrail-doctor.sh` 新增 `gateway-443-lane` 断言块
（车道存在才生效）：连接预算与 `cpa-gateway.conf` 的 `limit_conn cpa_cc`
**相等**（不钉字面量），并要求 `limit_req zone=cpa_rl443 burst=10;`、两个
`429` 状态、`add_header Retry-After $cpa_throttle_retry_after443 always;`、
`cpa_rl443` / `cpa_cc443` 的 zone 声明同时在位；车道不存在时输出
`gateway-443-lane=ABSENT`，不改变 8443 车道的 fail-closed 计数。

## 变更内容

- `scripts/remote/cpa-guardrail-doctor.sh`：新增 443 车道限流契约断言块。
- `tests/test_cpa_guardrails_script.py`：
  `test_cpa_guardrails_doctor_asserts_443_lane_limiter_contract`，冻结断言
  内容与「预算取自 canonical 车道」的性质。
- `docs/runbooks/cpa-gateway.md`：新增「443 车道的入口限流契约（Desktop 实际
  路径）」小节。

## 验证

- 断言块四例 A/B（抽取块 + fixture，本机 bash）：匹配
  `gateway-443-lane=OK conn-budget=20`；车道 `6` →
  `gateway_443_conn_budget=canonical:20 lane:6` + FAIL；预算对但缺 `429`
  状态 → FAIL；车道标记缺失 → `gateway-443-lane=ABSENT`。
- 远端 fresh strict doctor：`DOCTOR_CONTRACT_OK`、
  `gateway-443-lane=OK conn-budget=20`、`gateway-per-ip-concurrency=20`、
  `gateway-throttle-status=429`、`gateway-443-fallback-lane=1`、
  `throttle-retry-after-map-count=2`、12 项 `projection-drift ... MATCH`、
  `admission-integrity=OK`。
- 行为 A/B 见上文第 2、4 节（同一负例，修复前 503 / 修复后 429 + Retry-After）。

## 证据边界

- 连接预算从 `6 → 20` 的生效依据是部署文件 + `nginx -T` 合并视图 + worker
  重载时间（worker 起始时间与 `subscribe.conf` mtime 一致）；本次没有对 443
  车道做外网并发压测去击中 `limit_conn`，`20` 仍是算术下界（6+7+7）。
- `429` 行为证据来自本机 `127.0.0.1` 桶的合成突发，不是外网客户端桶；外网桶
  在 443 车道上与 VPS 自身地址同桶（见残余），因此该证据覆盖的是「状态码与
  退避头」契约，不是外网 per-IP 语义。
- 未宣称桌面端 UI 验收；ChatGPT desktop 的真实会话仍需用户侧确认。

## 已知残余

- 443 车道的 `$remote_addr` 是 xray 新建连接的源地址（VPS 自身），per-IP 限流
  实际退化为全局桶。恢复 per-IP 需 `xver=1` PROXY protocol 回退 + nginx
  `proxy_protocol`，属 v2ray-agent/xray 配置域，本次未改。
- `subscribe.conf` 由 v2ray-agent `install.sh` 生成，不属本仓投影清单；重装会
  删掉 CPA location，由 doctor 的 `random-route-count` 与新增的
  `gateway-443-lane` 双向 fail-closed 检出。

## 回滚

- 远端：`cp -a /root/cpa-443-lane-backup-20261010T025904Z/subscribe.conf.before
  /etc/nginx/conf.d/subscribe.conf` → `nginx -t` → `systemctl reload nginx`；
  连接预算回滚同理使用 `...T025311Z` 备份。Git 回滚不能代替远端恢复。
- 仓库：还原本次三个文件的改动；doctor 断言块与 `-Apply` 投影互不影响。
