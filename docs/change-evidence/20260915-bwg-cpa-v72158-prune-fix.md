# BWG CPA v7.2.158 更新与镜像清理修复

## 目标

在既有 BWG-only CPA 维护链路内，按 72 小时成熟窗口更新 CLIProxyAPI，并修复更新成功后镜像清理误报 `PRUNE_FAILED` 的确定性缺口。未触碰 ZZ、SSH tunnel 数据面、OAuth 凭据、client key、随机入口路径或 provider 配额策略。

## 变更

- 2026-09-14 17:21:45Z 通过远端既有 `/opt/cliproxyapi/auto-update.sh --apply` 完成 `v7.2.157 -> v7.2.158`，固定 digest 为 `sha256:58178ab00cd1e54aa8520c35a62feca30b8ec2a227facb04b0fd765b96df0691`。
- 更新前 `HEALTH_OK`，更新后生成检查 `HEALTH_OK`；脚本创建回滚备份 `/opt/cliproxyapi/backups/20260914T172147Z-from-v7.2.157`。
- 根因确认：`docker inspect` 返回 `sha256:<full-id>`，而 `docker images --format '{{.ID}}'` 返回 12 位短 ID。updater 现将两者统一为 12 位短 ID，再执行当前/回滚镜像保护判断；删除边界仍限制在 CPA 仓库镜像。
- 通过 BWG guardrail apply 将版本化 updater 投影到 `/opt/cliproxyapi/auto-update.sh`，远端 hash 与本地源一致；投影过程 readiness 为 HTTP 200。

## 验证

- 本地：`pytest -q test_scripts.py` 为 `32 passed, 66 subtests passed`；新增 Bash 夹具实测跳过当前运行镜像与回滚镜像，只清理旧镜像。
- Full gate：`116 passed, 1 skipped, 71 subtests passed`；Bandit、Ruff check、Ruff format、mypy 均通过；`git diff --check` 通过。
- 远端最终 strict doctor（2026-09-14 17:30:20Z）：`DOCTOR_CONTRACT_OK`；容器 running、restart=0，镜像为 v7.2.158；CPA loopback-only、Nginx 8443、随机路径 401/404/404、management remote disabled、identity-confuse absent、`request-retry=0`、`max-retry-credentials=1`、60 秒 transient cooldown 均符合既有契约。
- 远端只读归一化探针：`running-image-id-match=OK`；随后 `auto-update.sh --check` 返回 `current=v7.2.158 target=v7.2.158`，备份健康通过。

## 残余风险与边界

本次更新和清理修复不等于 provider 容量恢复。最终 doctor 仍能看到当前 access log 24 小时窗口累计 `503/503=55`、`502/502=37`，保留错误文件有 `server_is_overloaded` 标记；这些仍支持“上游容量/过载或其冷却链路”的判断，不能据此证明封号、降智或账号级限流。未进行额外合成生成请求；更新前后的 updater generation smoke 已分别返回 `HEALTH_OK`，自然使用仍需单独观察。

镜像清理的原始 `PRUNE_FAILED` 已通过代码与离线夹具定位并修复，但没有手工删除既有镜像；下次有成熟更新时由 updater 在相同备份和回滚边界内验证实际清理结果。
