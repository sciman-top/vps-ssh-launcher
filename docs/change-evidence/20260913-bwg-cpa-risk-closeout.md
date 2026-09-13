# 2026-09-13 CPA 风控建议实施闭环

用户授权连续实施上一轮建议，仅 BWG；保留已确认凭据与公网随机路径。

## 改动

- 第 1/2 项持续故障 key 保留在配置，excluded-models=["*"] 退出正常调度。
  第 4 项成为 r1 唯一模型候选，消除 YAML 排序不能控制 fill-first ID 排序的问题。
  恢复故障 key 需先单独验证，再移除对应 exclusion；不增加账号或重试。
- Nginx 保留每 IP 6 并发、10r/s、burst 20，增加整个公网 CPA 入口总并发 3。
  这是可回滚的保守初值，会约束 GLM/r1 在内的所有通道，不是上游账号官方安全配额。
- Nginx auth_request 内部子请求使用 CPA /v1/models 鉴权，透传原认证头和 query 参数，
  不消耗模型额度；日志只增加 auth_status，fail2ban 只计 auth_status=401/403。
  原先有效 key 遭遇上游 403 也会累计封禁的风险已消除。
- 复用现有 updater，抽出 cpa-health.py 判定 readiness、上游失败与本地契约失败。
  更新前失败暂缓，更新后暂时故障仅冷却 65s 后复核一次，仍失败保持本地就绪版本、
  exit 10 报未验收；契约失败回滚并验证旧服务就绪。信号退出不再可能误报 exit 0。
- 无新候选版本时也验证一次当前健康。更新备份只复制顶层 auth JSON/CDS，
  不复制请求日志。日志总上限 32MB、错误文件上限 10，未删除现有备份。
- doctor 按时间戳及 status 字段统计当前 access log，明确不覆盖轮转文件；
  单独统计保留的 overload 请求文件，按文件计数，不输出正文，不冒充恢复轨迹。

## 备份与回滚

`/root/cpa-risk-closeout-20260913/`：config、nginx、filter、updater 分别对应
CPA config.yaml、Nginx cpa-gateway.conf、fail2ban filter.d/cpa-gateway.conf、auto-update.sh。
只恢复本次对应文件，校验 nginx -t / fail2ban-client -t / bash -n；重启 CPA 并复验
就绪，reload Nginx 和 cpa-gateway jail。新 cpa-health.py 在恢复旧 updater 后不再消费，
可单独移除。auth 和凭据值未改动，不需要 OAuth 回滚。

## 验证

- 远端 nginx/fail2ban/bash 语法、CPA readiness、strict doctor 均通过。
- 公网 TLS 无代理：现有 key models 200，错误 key 401；OAuth Luna 200/1.54s，
  GLM 200/3.82s，r1 Luna 200/7.46s。保留全部 3 个中转 key，2 个 excluded=["*"]。
- 限流受控测试：保持 3 个未提交完整正文的请求，第 4 个 models 请求 429；
  关闭连接后立即 200。未向上游发起这 3 个生成请求。
- 新鉴权日志：正常请求 auth_status=200，错误 key auth_status=401，实际 jail 计数增加。
  两条合成日志测试：有效客户端/上游 403 不匹配，鉴权失败 401 匹配（1 matched/1 missed）。
- OAuth JSON 运算：17+25 与数组排序均正确；强制工具调用名称和 value=42 参数均正确。
  不将两条样本外推为全面质量保证。
- 缓存复测 3039 prompt tokens：一次 cached_tokens=2816，下一次=0；有缓存能力，
  命中不稳定，保持原配置，不承诺日常 92.7%。
- 实际 post-update shell 分支用替代 health/sleep 验证 success=0、持续暂时失败=10、
  契约失败非零；未人为制造上游过载或在生产强制失败回滚。
- systemd 更新入口本次成功/exit 0；本地与远端 updater/helper 哈希一致：
  updater e418982686aef62ea369f2f9ddf94e641071fb90d7e396d6574d5752c79e3adf；
  helper 560a3fd37c93ef3aa6306e7f910bb9d789c1d589e80d23f2952af8e0d0cfe467。
- 执行时 v7.2.157 已满 72h，因此真实完成 v7.2.156 -> v7.2.157，
  10:59:55 UTC success，digest 7ad14e95aa5347325a0f727cb9be30673f619369624b9fc89673da60736f7cb5，
  更新备份 `/opt/cliproxyapi/backups/20260913T105947Z-from-v7.2.156/`。
  新版再次公网 catalog 200、Luna exact OK / 200 / 1.81s、strict doctor 通过，
  容器 running / restart=0，nginx/fail2ban/xray/timer active。
- 本地 focused script suite 25 passed / 45 subtests；新增分类测试后 CPA 切片
  4 passed / 9 subtests；Ruff、format、Mypy、git diff --check 均通过。

## 边界

没有自然过载周期的同请求恢复验收，也没有封号豁免、真实上游模型身份或长期质量保证。
公网并发 3 是初始运营选择，不是性能压测结论；如确有正常并发受损，再据测量调整。
鉴权子请求增加一次本机只读调用；若 CPA 鉴权依赖不可用，则入口失败关闭。
当前统计明确区分保留窗口，复用现有日志/更新服务，不新增长期监控治理面。
