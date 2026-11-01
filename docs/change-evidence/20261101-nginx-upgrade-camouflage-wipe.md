# 2026-11-01 月度 nginx 升级重写伪装根页事故

## 结论
月度维护的 apt 升级（nginx mainline 1.31.7/1.31.6 → 1.31.8）把 `/usr/share/nginx/html` 的
stock 页重新解包回根目录，静默还原了伪装站提升（camouflage promotion），双机约 7 小时在
REALITY 回落与 Hy2 masquerade 面暴露 "Welcome to nginx!" 默认页与 50x.html。漂移门禁按设计
捕获。系统性修复 = dpkg path-exclude（commit 655124c），部署待用户授权。

## 时间线（UTC）
- ~04:05–09:46 并行会话完成 10-31 双核 parity 收尾：zz alt-SNI 应用（09:25，`reality_alt_sni=not-applied`→ok）、
  双机伪装 error-page 刷新（09:45/09:46，`nginx-camouflage.status result=success`）、zz merged-sub 6→7（09:26）。
- 12:30:23Z zz 每日 `VPS-SshLauncher-ZZ-Drift -Apply` 任务运行：`HARDENING_DRIFT=NONE`（当时根页完好）。
- 14:00:01–14:01:02Z 双机月度维护（bwg wrapper / zz vasma 原生）：apt upgrade 15 包，
  含 nginx 1.31.x→1.31.8（bwg 14:00:35、zz 14:00:33，dpkg.log 铁证）。
  dpkg 解包把 stock `index.html`/`50x.html` 写回根（mtime=2026-10-21 14:24:51.000000000，
  即 1.31.8 .deb 上游构建时间戳，双机同值）。服务全程绿（nginx -t 通过、无 reload 失败）。
- 14:07Z 只读漂移门禁：双机 `HARDENING_DRIFT=camouflage`
  （`camouflage=stock-default` + `camouflage=stock-artifact file=50x.html`；404 品牌化 drop-in 幸存）。

## 暴露面
REALITY 443 回落目标（nginx 33387/35622 vhost 根）与 Hy2 masquerade URL
（`https://fq.sciman.top:33387/`、`https://zz.sciman.top:12378/`）在该窗口内对任意
主动探测返回 nginx 默认页——即伪装车道族设计要消除的最强主动探测指纹。

## 根因与机制
- `index.html`/`50x.html` 是 nginx 包负载（非 conffile），包升级无条件重写；
  nginx mainline 源 ≈ 月更节奏，故每次月度升级都会复发。
- bwg 无自愈路径（`proxy_hardening_lane_recover.ps1` 限定 zz）；zz 每日任务 20:30（京）
  晚于月维 22:00（京）→ 理论暴露窗 ~22.5h。
- 附带发现：zz 升级前跑的 nginx 1.31.6 携带 5 个 medium CVE（修复版 1.31.7，
  含 CVE-2026-97629 TLS session ticket 伪造、CVE-2026-95101 HTTP/3 请求体注入）；
  今日升级后双机 1.31.8 已在全部已列公告修复面之外。

## 修复（已合入 main，均已推送）
- `655124c` fix(camouflage): lane -Apply 投影 `/etc/dpkg/dpkg.cfg.d/vps-launcher-camouflage`
  （对三个包属 stock 文件的 path-exclude），preview 报告 `dpkg_exclude=present|partial|missing`，
  备份/回滚与 error_page drop-in 对称；drift 门禁把排除文件缺失并入 camouflage 漂移；
  `residue_find` 补齐注释一直声称的 0 字节 `check` 构建标记匹配（zz 根目录现存一个）。
- `52c9371` docs(agents): AGENTS.md 记录该车道契约。
- 验证：pytest（camouflage/drift/recover/task/parity/cron 家族）79 passed 0 fail；
  WSL bash+flock 真跑 payload：apply 投影/隔离/提升 + 回滚字节级恢复 17/17 PASS。

## 恢复（已执行，LIVE_ACCEPTED）
用户授权后逐台执行（2026-11-01 22:4x–23:0x 京）：
- bwg `-Apply`：`CAMOUFLAGE_APPLIED`，根页 SHA==decoy（f73eb0c2…），`STOCK_FILE_REMOVED
  file=50x.html`，`DPKG_EXCLUDE_PROJECTED`，备份 `/var/backups/nginx-camouflage.dcjaCg`。
- zz `-Apply`：同上全过（备份 `nginx-camouflage.74cK4n`），但 `check` 残留被单词级引用
  检查误保护（`residue_kept_referenced= check`，decoy 措辞含 "check"）。追加收紧补丁
  `c6e904b`（精确名 `check` 归入 never-served 分支，lane+drift 双侧；WSL 真跑 payload
  `ACCEPTANCE_RESULT=PASS` 18/18）后二次幂等 `-Apply`（备份 `nginx-camouflage.S0JB4n`）
  完成 `check` 隔离。
- 双机复验：`proxy_hardening_drift.ps1` 均 `HARDENING_DRIFT=NONE`（exit 0，
  `dpkg_exclude=present`、`camouflage=ok`、`404-branded`，其余车道全绿）。
- 公网面实测：`https://fq.sciman.top:33387/` 与 `https://zz.sciman.top:12378/` 均返回
  decoy 页（"geili Forging"）；未知路径 HTTP 404 + 品牌化 "Page Not Found - geili Forging"；
  zz `/check` 由 200/空改为 404（文件已隔离）。
- 月度复发向量已由 dpkg path-exclude 关闭；vasma 重装向量仍由漂移门禁+zz 每日任务/手动兜底。

## 归因手法备忘
lane 末态真源：远端 `/var/lib/vps-ssh-launcher/maintenance-status/*.status`（result/exit_code/
finished_at）+ 本机 `%LOCALAPPDATA%\vps-ssh-launcher\maintenance-runs\*.log`；
包级事件：`/var/log/dpkg.log`。三者交叉即可把“谁在何时写了什么”钉死。
