# 2026-11-17 bwg WorkBuddy 插件切换至 luode0320 线（凭证免重登平移）

## 背景与授权

上游 `mmqz/cpa-multi-plugins` 账号级删除（2026-11-17 发现，`gh api users/mmqz` 404，
非改名），社区评估后选定 `luode0320/cpa-workbuddy-plugin`（s1sunny/cpa-plugin 血统的
事实 owner，每日 release 节奏）为长期线。用户授权「保留原插件 + 隔离安装 luode +
凭据平移验证 → 生产切换连续执行」。隔离演练（同日早些时候）已实证免重登：17/17 auth
解析零错误、双 realm active ping 全通、credits sweep `refreshed=10 failed=0`、
free 模型端到端 200。

## 变更内容（全部 bwg）

### 生产切换事务（`outputs/run-wb-luode-cutover-20261117.ps1 -Apply`）

- `CUTOVER_APPLY_OK`，备份 `/opt/cliproxyapi/backups/20261117T023934.528982252Z-wb-luode-cutover`。
- 插件：`workbuddy.so`(0.9.89 `f2c678ff…`) → `workbuddy-provider.so` 0.21.27
  （sha `a726f672…`）+ `workbuddy-ai-provider.so` 0.1.40（sha `8fa992e8…`），
  发布 zip 与 `checksums.txt` 双重校验。
- auth（17 凭据，token 原地平移、零重登）：CN 10 × `type/provider→workbuddy-provider`；
  INTL 7 × `workbuddy-intl-<uid>.json→workbuddyai-<uid>.json` + type 改写。
- config：`plugins.configs.workbuddy` → 双 provider 节（CN
  `scheduler_mode=credits/checkin_auto=true/growth_auto/lifecycle/token_keepalive/rotation`
  全显式关闭保持 mmqz 行为语义；INTL `token_keepalive=false`）；
  `oauth.model-alias` 的 legacy `workbuddy` 通道 → `workbuddy-ai-provider`
  （`deepseek-v4.1-flash-sg→deepseek-v4.1-flash-intl fork:true` +
  `hy4flash-free→hy4free 顶替`）；`oauth.excluded-models` 两通道收窄列表写入
  （CN 15 项 / INTL 22 项，由切换时实测动态目录计算）。
- 事务内验证：双插件注册行、**/v1/models 16 模型面与切换前逐字节相等**
  （`CATALOG_SURFACE_UNCHANGED`）、`cpa-health.py readiness` OK、lane hold delta=none。
- 前两次失败尝试均被事务自动回滚吞掉（config 缩进 bug、restart 基线时序 bug），
  回滚后 readiness/healthz 均恢复正常——回滚路径实战验证有效。

### admission 换代事务（`outputs/deploy-admission-wbluode-20261117.ps1`，四件同换）

普通 `-Apply` 被 `admission_pin_transition_pending` 守卫拒绝（按设计），走同构事务：
- `cpa-admission.py`（fork 桥展开派生，HEAD sha `fcf95564…`）+
  `cpa_provider_routes.json`（新 plugin_routes/alias 面为派生输入）+
  `cpa_request_guard.py`（内嵌新代 sha）+ pin 文件，四件原子换；
  `cpa-admission.json`/bootstrap 与 live 字节相同，仅恒等重置。
- pin `16ace2c9… → fcf95564…`，`ADMISSION_INTEGRITY_OK` ×2、
  `CP_INTL_SPLIT_20261117_CONTRACT_OK`、备份 `…-admission-wbluode-20261117`。
- baseline 勘误：config/bootstrap 的 live 基线为拆分批落地值（`c46bf5fc…`/`00f4b7d7…`）。

### 仓侧适配（bfef2a1 + 47a056e，full gates 绿 1722 passed）

- fork 语义三处派生同构：`cpa_catalog_check.plugin_lane_aliases`（fork 展开）、
  `cpa-admission._shared_route_model_lanes`（同展开，pin 重算）、
  `cpa_policy._shared_lane_expectations`；`cpa-guardrail-apply.sh` plugin 通道允许
  顶替 alias（其余通道仍强制 fork）。
- manifest：plugin_routes 双 provider 新 pin 集、`oauth_model_aliases` 换通道、
  `oauth_settings` 拆双 channel。INTL admission lane 三元客户端契约
  （`deepseek-v4.1-flash-intl/deepseek-v4.1-flash-sg/hy4free`）不变。
- doctor：`==workbuddy-config==` 重写（legacy 节必须消失、INTL 插件 enabled+keepalive
  off、alias 桥两 entry、excluded 两通道在场）；realm 计数门改 `workbuddyai-` 口径，
  跨池顺序门随共享候选链退役；`==workbuddy-version==` 双插件钉
  `0.21.27 + 0.1.40` 并拒绝 legacy `.so` 残留。第二提交修正 oauth 子块读取路径
  （`oauth.model-alias` 而非顶层）。
- `cpa_workbuddy_model_pins.ps1` repurpose 为 alias 桥审计/投影工具；
  `cpa_workbuddy_login_region.ps1` 及其测试随键退役；七个测试文件同步。

## 受控验收

- **严格 doctor：`DOCTOR_CONTRACT_OK`**；`MODEL_IDS_UNKNOWN=none`、
  `MODEL_CONTEXT_DRIFT=none`、workbuddy-config=OK、双版本钉 OK、五 lane 契约 OK。
- 8318 经 admission 全链单发：CN `deepseek-v4.1-flash` 200 `finish=stop` len=2
  **LIVE_ACCEPTED**；INTL `hy4free` 200 `served=hy4-flash-free`（别名解析与路由链
  正确、INTL lane 三元在场），content 空/`finish=length` 属上游 free 档推理吞噬
  行为（两档 token 预算 16/64 同样），非投影缺陷；sg/主模型此前 429 `14018` 为
  配额墙（到达计费面），不构成回退信号。lane counters 均匀归零。
- `workbuddy-version=cn:0.21.27 intl:0.1.40`。

## 回滚

插件/auth/config 用切换事务备份目录整体还原；admission 代际回滚不得用普通
-Apply，须同构事务换回 `16ace2c9` 代（备份在 admission 事务备份目录）。

## 遗留

- luode intl 动态目录无独立 CN 名 deepseek id：INTL 付费主客户端名
  `deepseek-v4.1-flash-intl` 以 fork alias 骑在 `deepseek-v4.1-flash-sg` 上
  （两名同上游），已在 manifest note 声明。
- `hy4flash-free` 上游 id 被别名顶替后不出现在客户端面（设计内防裸名泄漏）。
- luode 上游每日 release 节奏下的后续采纳沿用本轮手筋：_checksums_ 校验 →
  隔离装载 → 逐台。
