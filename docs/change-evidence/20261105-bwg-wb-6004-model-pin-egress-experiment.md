# BWG wb 6004 模型 pin 增补与出口判别实验（2026-11-05）

## 范围

- 唯一持久远端写入：bwg `/opt/cliproxyapi/config.yaml` 的 `models_intl`，
  `deepseek-v4.1-flash` → `deepseek-v4.1-flash,deepseek-v4.1-flash-sg`
  （L1 模型分散：6004 模型级 ⇒ 第二个独立桶）。
- 实验性临时变更（全部已回滚或带自愈回滚）：CPA `proxy-url`
  `socks5://127.0.0.1:18080` 翻转/回滚（判别实验机械，同 inode 字节级）；
  bwg↔zz 一次性 ed25519 隧道键与 `ssh -D 127.0.0.1:18080 -p 31854`；
  zz `/etc/ssh/sshd_config` `PubkeyAuthentication no→yes`（实验期临时）。
- 未改凭据、账号池、并发、RPM、路由顺序、冷却参数。

## 事务与验证

- pin 写入：备份 `/root/wbpin-config-backup.yaml`；same-inode 单行替换，
  前后 sha 记录 `/root/wbpin-config-{before,after}.sha`
  （pre `dc5969f1fc31864e…` / post `0e943ac67fe8ceb2…`）；watcher 热加载
  （9 条 reload 日志）；`/v1/models` 广告校验 flash 与 flash-sg 均在列；
  flash-sg 8318 单发探针 200/1.02s、SSE 5 事件 `[DONE]` 终态（PONG 分片
  跨事件，拼接完整）。
- 实验机械：`proxy-url` 翻转脚本先因本地构造转义损坏而 ABORT（配置零变更、
  sha 复核一致），重建后未再执行翻转（自然热窗未出现）；守望器
  （检测真实拒绝→claude_idle 门→翻转→68s park 过期→zz 探针→回滚→对照探针，
  超时自愈回滚）4h 窗在位，判据未取。
- 隧道验证：socks 出口 `38.244.39.xx`（zz）实测通；zz sshd `-t` 校验后
  reload，密码登录不受影响。

## 事故（脱敏）

- 探针误用 `grep '"note"'` 于单行 auth JSON：整行（含 access/refresh
  token）进入会话输出（31.6KB）。落盘临时副本已当场删除；token 未进仓库与
  远端变更。后续一律 jq 白名单逐文件提取（runbook §4.2 已固化纪律）。
  建议下个维护窗对 wb 凭据执行 refresh/re-login 轮换 token。

## 回滚

- pin：恢复 `/root/wbpin-config-backup.yaml`（或反向单行替换），watcher 热
  加载；勿用 Git 回滚代替远端恢复。
- 实验面清理（判据取到或守望器超时后执行）：kill wbexp_watch*；删 bwg
  `/root/wbexp_*`、`/root/wbsg_probe.sh`、`/root/wbpin.py`；zz
  authorized_keys 移除 `wb-egress-experiment-20261105` 行；
  `/etc/ssh/sshd_config` PubkeyAuthentication 恢复 `no`（备份
  `/tmp/sshd_config.bak-wbexp`）并 `sshd -t` + reload；bwg 删
  `/root/.ssh/wb_probe_zz{,.pub}` 并杀 18080 隧道。

## 边界

- flash-sg 在役只达单发探针与广告校验（host_loaded），长期质量/额度验收由
  自然流量观察；上游退役 id 时按 runbook 从 pin 移除。
- 共享桶限制键判据（IP vs 登录面）未定，不作为任何出口拆分建设的依据。
