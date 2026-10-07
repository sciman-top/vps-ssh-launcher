# 2026-10-07 BWG admission 请求解析与维护健壮性修复

## 范围与依据

- 用户授权全面审查、连续修复和 BWG 受控维护；仅处理本项目和 BWG。
- 代码提交：`cbef29e318660e702b5de96e151a4bc61afa2449`。
- 请求体解析失败后仍保持连接，会将未读数据解释为后续请求；重复长度字段、长度与传输编码冲突、非法 chunk size、截断或超大 trailer 需要拒绝。
- 同批本地修复覆盖 Docker 多服务名称解析与恢复缺席服务、计划重建保留执行结果并刷新未执行计划时间、Windows 锁拥有者判定、明确应用刚生成的计划及全链目标配置一致性。

## 本地验证

- 完整门禁的 compileall、pytest、Bandit、Ruff check/format 通过：460 tests、366 subtests，真实 SSH integration 默认跳过 1 项。
- 初次完整门禁在新增测试变量类型标注处失败；补齐标注后，原门禁同范围 mypy（35 文件）通过，受影响测试和 Ruff 再验通过。
- `pip check`：No broken requirements found；`pip_audit -r requirements.txt`：No known vulnerabilities found。漏洞结论仅覆盖当次数据库与声明的运行依赖。
- BWG 全链 Observe：`20261007T110414Z-2e4a986a`，9 步 PASS、退出码 0；显式 TargetConfig 贯穿本次运行。回执位于本机 LocalAppData 的 `vps-ssh-launcher/full-maintenance-runs/20261007T110414Z-2e4a986a-bwg-observe.json`。

## 远端事务与回滚

- 本次只替换 `/opt/cliproxyapi/cpa-admission.py` 并重启 `cpa-admission.service`；保留 CPA 镜像、配置、凭据、Nginx、能力路径及通道限额。
- 使用本项目 SSH 连接实现，严格验证主机密钥；SFTP 上传至随机 mode-700 临时目录，文件 mode-600，操作完成后删除暂存。
- 执行前获取 `/run/vps-ssh-launcher-maintenance.lock`，确认三个通道 inflight/pending 均为 0、retired_readers 为 0，检查运行健康与旧文件精确哈希。新文件编译检查后才写入。
- 备份目录：`/root/cpa-admission-framing-backup-20261007T110815.315105941Z`，包含旧脚本及 `rollback.sh`（mode-700）。备份哈希校验与回滚脚本语法检查通过。
- 同目录暂存后原子替换文件；任何后置检查失败即恢复备份并重启、复验健康。实际回滚未触发；未在生产故意注入启动故障。
- 人工回滚入口：`bash /root/cpa-admission-framing-backup-20261007T110815.315105941Z/rollback.sh`。回滚同样获取共享锁；执行前应确认请求通道空闲，回滚后重跑严格 doctor。旧代码将不匹配新 HEAD，需同步明确回滚的源码版本。

## 部署与受控实战结果

- SHA-256：`e6b48a570a9993652aecfee9e08184eabc3c782829d796ed01b8823d8db175c1`，本仓与远端一致。
- admission 主进程 PID 从 427591 变为 446689，证明运行进程已加载新投影；`DEPLOY_HOST_LOADED=PASS`。
- 在远端 loopback 发送 5 类受控请求：oversized-length、duplicate-length、conflicting-framing、invalid-chunk-size、oversized-trailers。每类均返回 400、Connection close，拼接的 healthz 请求未产生 200；`LIVE_FRAMING=... PASS`。
- 随后独立正常 healthz 返回 200；`LIVE_HEALTH=PASS`，部署退出码 0，`DEPLOY_RESULT=PASS`。
- 后置严格 doctor：9 个投影检查全部 MATCH、`admission=OK`、`DOCTOR_CONTRACT_OK`，退出码 0。CPA、月度维护、Xray、v2ray-agent、renewtls 心跳 success/exit_code=0；sing-box 为 NOT_REQUIRED。

## 验收边界

- 本轮已证明 repo_verified、filesystem_projected、host_loaded 以及 HTTP 请求解析的 controlled_live_replay。
- 未额外发送付费模型生成、压力负载或故意中断生产服务；上游账号容量与所有未来输入不在本次修复可保证范围内。
- 真实主机维护 RunNow 已在本轮之前完成，本次不重复系统升级或内核重装。

## 最终复核补充

- 追加代码提交 `5e48d59`：Windows 使用 `msvcrt.locking`、其他平台使用 `flock`，在获取/恢复 JSON 拥有者锁及整个维护期间保持操作系统互斥，修复两个进程同时恢复过期锁时误删新锁的问题。`.guard` 文件保持原位，不应在维护运行期间删除；锁随描述符关闭或进程退出释放。
- 独立进程在拥有者文件被删除后仍被阻止进入写入阶段；进程异常退出后原生锁自动释放并能恢复过期拥有者文件，专项验证通过。原生互斥实测平台为 Windows；其他平台分支未做真实平台验收。
- 保留合法长度字段 OWS 及 chunk 扩展 BWS 的兼容，新增两项正常请求测试。
- 最终完整门禁一次执行到终态，退出码 0：464 tests、367 subtests、1 项未启用的真实 SSH 测试跳过；compileall、Bandit、Ruff check/format、35 文件 mypy 全部通过。
- 按同一备份/共享锁/原子替换/失败恢复事务重新投影最终版本，备份为 `/root/cpa-admission-framing-backup-20261007T111641.140490279Z`；对应 `rollback.sh` 恢复上一已加固版本。原始版本备份仍保留在上文首个目录。
- 最终 SHA-256：`8e4621baa3954cc61c5e355ecc67f90b664751efd4273639532c101c78716eac`；PID 从 446689 变为 447904。5 类异常请求、2 类合法空白请求及独立健康检查全部 PASS，`DEPLOY_RESULT=PASS`，退出码 0。
- 最终部署后再次执行严格 doctor：9 个投影检查全部 MATCH、`admission=OK`、`DOCTOR_CONTRACT_OK`，终态退出码 0；维护心跳继续正常。
