# qq-codex-bot 模型名迁移 — 收口后只读复验清单

**背景**：`D:\CODE\qq-codex-bot` 把 sol 家族从已退役的 `gpt-6-sol` 迁到 `gpt-6.1-sol`
（luna 家族 `gpt-6-luna`），**必须投影到部署主机 `8.163.41.198` 才生效**——
仓库改动本身不改运行态。

**判据来源**：bwg 网关侧（`144.34.229.116`）的 admission journal 与 nginx access log。
bot 的请求在 bwg 侧表现为客户端 `8.163.x.x`、`route=chat`。

---

## 1. 投影前：确认仓库投影内容正确（本地，只读）

```bash
cd D:/CODE/qq-codex-bot
./.venv/Scripts/python.exe scripts/sync-provider-config.py --dry-run
```

期望（2026-09-30 现役目录）：

| 字段 | 期望值 |
|---|---|
| `primary_source.api_base` | `https://fq.sciman.top:8443/<16位随机前缀>/v1` |
| `model` / `providers.*` / `text_model_tiers.*` | 全部 `gpt-6.1-sol-input`（2026-10-01 起 sol 家族解耦 OAuth lane） |
| `text_provider_profiles[].model` | 仅 `gpt-6.1-sol-input` / `gpt-6-luna` / `glm-5.3-flash` / `deepseek-flash` |
| `model_preset_priority` | `sol_only, luna, glm, deepseek` |

**不得出现**：`gpt-6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-6-sol-input`、
`gpt-6-sol-cii`、`deepseek-v4-pro`（全部已退役）。

> `--dry-run` 只打印投影结果，不打印密钥、不写远端。

## 2. 投影（真实远端写入，需显式授权）

```bash
cd D:/CODE/qq-codex-bot
./.venv/Scripts/python.exe scripts/sync-provider-config.py --restart-astrbot
```

> 注意：该脚本**没有 `--execute` 旗标**——无 `--dry-run` 即执行；容器重启由
> `--restart-astrbot` 单独控制（经 restart guard）。

## 3. 投影后：bwg 侧只读复验（决定性）

```bash
cd D:/CODE/vps-ssh-launcher
./.venv/Scripts/python.exe ssh_tool.py --profile bwg run --command "
date -u +'SNAPSHOT_UTC=%Y-%m-%dT%H:%M:%SZ'
echo '--- 1. lane 污染应为 0（自投影时刻起）---'
journalctl -u cpa-admission --since '<投影时刻 UTC>' --no-pager | grep -c 'lane_reject'
echo '--- 2. 退役名应彻底消失 ---'
journalctl -u cpa-admission --since '1 hour ago' --no-pager \
  | grep -oE 'model=(gpt-6-sol|gpt-5\.6-terra|gpt-5\.6-luna)[^ ]* status=[0-9]+' | sort | uniq -c
echo '--- 3. 现役名应有 200 ---'
journalctl -u cpa-admission --since '1 hour ago' --no-pager \
  | grep -oE 'model=(gpt-6\.1-sol|gpt-6-luna|glm-5\.3-flash|deepseek-flash) status=[0-9]+' | sort | uniq -c
echo '--- 4. bot 侧状态分布 ---'
tail -200 /var/log/nginx/cpa_gateway.access.log | grep '^8.163' \
  | grep -oE 'route=[a-z]+ status=[0-9]+' | sort | uniq -c | sort -rn
"
```

**通过判据**

- ① `0`
- ② 无输出（或仅剩极少量投影前的残留）
- ③ 出现 `model=gpt-6.1-sol-input status=200`（2026-10-01 解耦后 bot sol 族判据名）
- ④ `400` 应归零；`429` 不应新增（投影前旧行不算——注意按 `time=[...]` 时间戳筛，
  不要用 `tail -N`，日志文件很小，`tail` 会回看到几小时前的旧行）

## 4. 已知的判据陷阱（本轮实测踩过）

1. **`tail -N` 会回看到投影前的旧行**。`cpa_gateway.access.log` 一天才几百行，
   `tail -300` 能回到几小时前，把 apply 前的 429 当成新事件。**按时间戳筛**。
2. **`grep -oE 'status=[0-9]+'` 会命中 `upstream_status=` 与 `auth_status=`**，
   计数会变成 2–3 倍。要么用 `\bstatus=`，要么直接读 doctor 的
   `==gateway-statuses-current-log-24h==` 段（它的数字是对的）。
3. **`lane_reject` 是唯一能证明「admission 本地拒绝」的字段**；
   只看 nginx 的 429 状态码无法区分 nginx 限流 / admission 冷却 / 上游 429。

## 5. 残余设计注意（2026-10-01 更新：sol 族解耦已实施）

~~`gpt-6.1-sol` 与 `gpt-6-luna` 都是本机 CPA 的 **OAuth lane 名**，
即 bot 的 `sol_only` 与 `luna` 两套 preset 会与 desktop **共用唯一 ChatGPT Plus 账号**
（lane `max_inflight=2`）~~

**2026-10-01 已按操作者指示解耦**：bot 的 sol 家族三档 + DEFAULT_MODEL 改指非 OAuth
别名 `gpt-6.1-sol-input`（槽位1 → ai.input.im，上游同为 gpt-6.1-sol），bot 不再触碰
OAuth lane；仅 `luna` preset（`gpt-6-luna`）仍与 desktop 共用。证据：
`qq-codex-bot` 仓 `docs/change-evidence/20261001-sol-family-decouple-oauth-lane.md`
（`38305a2b`，受影响 pytest 776 passed + verify-repo full 全绿 + 投影远端自验 passed）。
