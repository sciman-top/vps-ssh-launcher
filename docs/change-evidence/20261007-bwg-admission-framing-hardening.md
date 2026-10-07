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
