# BWG WorkBuddy 登录 realm 残留修复（2026-11-03）

## 范围

- 唯一远端写入：bwg `/opt/cliproxyapi/config.yaml` 的
  `plugins.configs.workbuddy.login_region`，由 `cn` 改为常态 `intl`。
- 未修改 WorkBuddy 凭据、账号池、模型 pin、并发、RPM、重试、冷却或路由。
- 未重启 CPA；本次依赖配置 watcher 热加载。

## 事务

- 预演：`WORKBUDDY_LOGIN_REGION_DRY_RUN current=cn target=intl`。
- 写入前取得维护锁与 admission `requests.lock` 排他锁，等待现有请求排空。
- 备份：`/root/cpa-workbuddy-login-region-backup-20261103T124736.465205Z/config.yaml`
  （目录 `0700`、文件 `0600`）。
- 配置 SHA-256：
  - before `0a50f51ce9a6d0507e69c92a00231cf467db3f4ed0e2c3dcfcb448a19b795cba`
  - after `b327ef0240fce81d807527a3af4a2eb763abe8684f336f3db1f6aaba8d7fdb96`
- 写入保持同一 inode，只替换 `login_region` 行；脚本比较写入前后 YAML，
  拒绝任何其他字段差异。

## 验证

- 容器未重建：`restart=0`，原启动时间不变。
- watcher 日志：`config file changed, reloading` 与
  `config successfully reloaded, triggering client reload`。
- 落盘读回：`login_region=intl`，SHA-256 与 after 一致。
- 幂等复跑：`WORKBUDDY_LOGIN_REGION=ALREADY_APPLIED value=intl`。
- strict doctor：`DOCTOR_CONTRACT_OK`；三条在役 OAuth lane 均 `OPEN`；
  `workbuddy-login_region=intl state=ok`、`workbuddy-config=OK`。
- 之后 1 小时自然流量：WorkBuddy `239/239 completed`、容量事件 0、
  client disconnect 0；缓存命中 `0.98`。该窗口仍可见单凭据
  `inflight=1` 导致的排队 p50 约 6.15s、p90 约 20.55s，不能用提高并发解决。

## 回滚

在维护窗恢复备份中的 `config.yaml`，保持原权限并依赖同一 watcher 热加载；
若需强制确保加载，使用项目既有 guarded restart 契约。不得用 Git 回滚代替远端
配置恢复，也不得重置冷却或制造 OAuth 探针。

## 边界

- 本事务只修复“后续新登录可能误入 CN realm”的粘性默认值。
- 不改变现有凭据血统，不证明 WorkBuddy 第三方接入合规或账号长期安全。
- `DOCTOR_CONTRACT_OK` 与 1 小时自然流量只达到 `host_loaded` / 短窗自然观察，
  不等于长期账号、配额或质量验收。
