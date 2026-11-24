# bwg antigravity：第四例 TOS + Gemini 全面退役 + 死号残留清零（2026-11-24）

## 第四例 TOS_VIOLATION（07:58:29Z，单凭据非波次）

用户 argon 会话 affinity 6h TTL 过期 → `cache miss, new binding` 换绑 → 新绑定选中
jalumuruvenkatarao，首请求即上游 403 `PERMISSION_DENIED "This service has been
disabled … violation of Terms of Service"`（appeal URL 同前三例）。admission 落
`account_restricted` 持久锁（`lane_hold_latched source=response_signal`）；core 终态
挂起该凭据 + 全池 2h TOS 暂停（至 09:58:29+08）。全窗 TOS 计数=2 行=同一号，非波次。
数小时来首次 argon 上游接触即中招，零锤击。池=3（knaha367/sciman.phys/swamigundu20，
全部 flash-only 免费号）——用户于 08:02Z 经管理 API 两笔 DELETE 干净移除全部 G4
承重号（gantasalabojes+jalumuruvenkatarao），热加载 REMOVE 无残留。

## 用户指令与退役批次（4e83742 已推）

"删除全部 G4 号 + 退役 CPA 中全部 Gemini 模型"。四名
（gemini-3.8-flash-high / gemini-4-argon-high / gemini-4-hydrogen-xhigh /
gemini-4-neon-xhigh）从 oauth_routes、oauth_settings.antigravity 摘除，
`antigravity_oauth_exclusions` 增四枚墓碑（codex_api_key_exclusions 已含全部四名；
static_models 保留为上游参考面）。lane 终态声明=claude-opus-5-5-high /
claude-sonnet-5-5-high。同步面：cpa-admission.json lane models、apply.sh expected 表
+ 就绪标记（`claude-only=allowed gemini=retired-and-excluded`）+ union 摘要退役集、
doctor allowed 集、桌面 override 清单摘四行、两个 runbook（attach 清单/320K 分带表）。
测试：expectations 字面量缩至 claude-only+墓碑注释；四个探针测试迁至在役名；health
echo 测试改骑 claude-opus-5-5-high（`-high` 后缀 echo map 泛化）；readiness 夹具补
claude id；test_cpa_admission 合成 loader 钉历史模型集（lane models+model_lanes 双面，
合成 manifest 走预加载钉扎），真文件 freeze 测试改直读。Full gates EXIT=0。

## 残留清零（bwg 服务端）

- auth 目录：仅 3 活号文件，无孤儿锁；管理视图恰 3 条。
- `/root/auth-backups/`：14→4（仅活号），10 份死号凭据备份按备份内 email 精确分类后
  删除（gantasalabojes/jalumuruvenkatarao/ai.sciman.top/sciman.top/ditchgieck354/
  agi.phys×2/mamo/第三例 b717***——第三例身份自此钉死：邮箱 b717*** 开头）。
- kv.db：已删凭据的亲和/冷却残键惰性（路由只看在册号）；TOS 全池暂停 kv 条目
  01:58:29Z 自然过期。
- 用户侧待办（官方锚）：[Antigravity Additional Terms](https://antigravity.google/terms)
  点名第三方产品接入=abuse 可致 suspension/termination；
  [Google OAuth2 文档](https://developers.google.com/identity/protocols/oauth2/web-server)
  "Refresh tokens are valid until the user revokes access"——已删号的 token 在撤销前
  一直有效。待用户：myaccount.google.com/permissions 逐号撤销第三方授权（token 即死）、
  退订 AI Pro、清理各端 IDE/浏览器登录会话；申诉与否用户裁决（自认条款、解封后再犯=永久）。

## 双锁释放与验收（01:59-02:01Z）

池停自然过期后执行 stop→原 inode 写中性→start（备份
`antigravity-risk.json.before-release-20261124T015941Z.json`）；healthz 1s 恢复、
无再锁。验收（只认 8318 过 admission）：
- union 目录（8317 实测）：`gemini_models: NONE`，9 models 剩余；
- gemini 负向单发 → **400 model_not_found**（`lane=passthrough` 本地快拒 2ms，零
  上游接触，luna 先例同形）；
- claude-sonnet-5-5-high → `lane_admit` 受理后 400 model_not_found（5ms 本地）：
  声明在途、无凭据注册=union 消失（carbon 先例形态），车道当前零注册模型、基础设施
  完整保留；
- 严格 doctor：`DOCTOR_CONTRACT_OK` EXIT=0，`lane-hold-antigravity-oauth=OPEN`，
  `antigravity_lane=OK allowed=claude-2名 excluded=15项`，投影零漂移。

## 同日修正：flash-high 复名（用户指令，33738d0 已推）

用户澄清退役面=G4（烧灼全发生在 G4-capable 账号），三个 flash-only 免费号应继续
服务。`gemini-3.8-flash-high`（车道唯一 flash 档；上游无裸名 gemini-3.8-flash）
复名：routes/lane/expected/allowed/桌面 overrides/两 runbook 恢复，同名墓碑从
`antigravity_oauth_exclusions` 撤除；gemini-4 三名墓碑保持。开名手术照 11-16 手筋：
备份 `/root/cpa-config-backup-before-flash-reopen-20261124T041619Z.yaml` → python
按 excluded-models.antigravity 键精确截块摘单行（同 inode 写回热加载，不单独重启）
→ -Apply 收编（`flash=allowed claude=allowed gemini-4=retired-and-excluded`）。
Full gates EXIT=0。验收：union `flash_high_in_union=True`（gemini_ids 仅 flash-high，
10 models）、8318 flash 正向单发 **200 PONG**（lane_admit→upstream 200，上游回显
flash-n 变体）、argon 负向仍 400 本地拒（2ms 零上游）、严格 doctor
`DOCTOR_CONTRACT_OK` EXIT=0、`allowed=flash-high+claude×2`、排除表 14 项、lane OPEN。

## 回滚

仓库 `git revert 4e83742`；远端按 /opt/cliproxyapi/backups 当日备份目录重放。
死号备份删除不可逆（有意为之：死号凭据恢复即再犯）。
