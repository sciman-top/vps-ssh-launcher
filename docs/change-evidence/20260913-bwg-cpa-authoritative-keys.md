# 2026-09-13 BWG CPA 凭据同步与 OAuth overload 核查

## 授权与真源

用户指定仓库根私有 `- 副本.env` 为最新凭据真源，要求 1 个 ChatGPT OAuth、
两个 Codex 中转站共 3 个 key、1 个 GLM Plan key；删除不一致的旧上游 key。
该文件被 *.env 忽略。所有比对在内存中完成，秘密不写日志、命令参数或 Git。
该文件仅含上游凭据，不是 CPA 对外客户端 key，未轮换客户端 key。

## 同步与验证

- 真源第 1/4 项为同一站，第 2 项为另一站，第 3 项为 GLM。
- 更新前只有第 4 项与 GLM，均与文件相同；不存在额外旧活动 key。
- 活动配置重建为 r1 两项、r2 一项、GLM 一项；OAuth 文件仍为原有单个账号。
- 保留 force-model-prefix、零额外重试、单凭据上限、冷却、会话粘性和公网随机路径。
- 按实测将正常第 4 项排在 r1 首位；未删除用户确认的第 1/2 项。
- 备份 `/root/cpa-key-sync-20260913/` 保存维护前 config.yaml、auth/、auto-update.sh。
  修改配置后重启 CPA，目录就绪检查成功；后续仅调整 r1 顺序由配置热加载处理。
- 第 1 项直连生成 Luna HTTP 404；目录 HTTP 200 / 3 个模型 / 不含 Luna。
- 第 2 项直连生成 Luna HTTP 503；目录 HTTP 200 / 12 个模型 / 含 Luna。
- 第 3 项 GLM 生成 HTTP 200 / exact OK；第 4 项 Luna 生成 HTTP 200 / exact OK。
- 用户补充：近半个月第 1/2 项均不可用，其余正常。故它们不能被描述为可用通道，
  也不应归因于短暂 OAuth 冷却；分别需要上游确认模型权限和生成端供给。
- 公网 CPA：OAuth Luna HTTP 200 / 3.33s，r1 Luna HTTP 200 / 4.25s；
  裸 gpt-5.6-sol HTTP 400 / model_not_found。裸目录只有 Luna 与 GLM。

## OAuth 过载与恢复边界

- OAuth 限制使用真实字段 `excluded_models`（下划线），不是 `excluded-models`。
  当前 exclusions 保留 codex-*、gpt-5.3-*、gpt-5.4*、gpt-5.5*、sol、terra、gpt-6-*、
  gpt-image-*；实际仅 Luna 开放。升级检查改为裸目录必须严格等于 Luna + GLM，
  避免未来新裸模型绕过原来的有限 sentinel 检查。
- 现容器 docker logs 未出现 server_is_overloaded，但持久化错误日志找到 9 份请求记录，
  都涉及 Luna，其中 8 份包含 ChatGPT OAuth 上游标识；每份重复出现 2 次错误码，
  不按 18 次故障计数。未输出原始请求、地址或认证信息。
- 设置 codex.stream-bootstrap-buffering=true，将流开头已识别的 overload 在发送成功头前
  表现为真实错误；transient-error-cooldown-seconds=60、disable-cooling=false、
  save-cooldown-status=true、request-retry=0 保持。60s 到期后后续请求可再次调度，
  不自动重放已失败请求，不保证上游容量恢复，不跨前缀转去中转站。
- [固定版本处理源码](https://github.com/router-for-me/CLIProxyAPI/blob/v7.2.156/internal/runtime/executor/codex_executor_terminal.go)
  明确识别 server_is_overloaded 并为 buffered bootstrap 映射 503；
  [冷却实现](https://github.com/router-for-me/CLIProxyAPI/blob/v7.2.156/sdk/cliproxy/auth/conductor_cooldown.go)
  为暂时错误设置恢复时间；仅只读参考，未复制或执行外部实现。
- 当前成功请求证明现已恢复可用；此次没有自然重现 overload，未验证同一进程内完整
  overload -> cooldown -> recovery 周期，不作无感恢复保证。

## 本仓修复与回滚

- 移除 guardrails -Apply 中按历史标签自动删除 r2/DeepSeek 的逻辑，保留用户新确认凭据。
  兼容当前 YAML 解析型 updater 的 key 提取检查；不执行历史全量 Apply。
- 更新 updater 的精确裸模型集合检查并投影到远端；bash -n 与 --check 成功，无再次升级。
- strict doctor 通过；focused test_scripts.py 为 25 passed / 43 subtests。
- 回滚只恢复本次备份的 config.yaml、auto-update.sh，重启 CPA 并复验目录/公网；
  不默认回写 auth，避免覆盖更新后的 OAuth token。回滚会撤去刚恢复的两个 key。
