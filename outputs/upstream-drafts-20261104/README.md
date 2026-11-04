# WorkBuddy / Cockpit 上游化草稿（2026-11-04）

本目录保存已发布正文的原稿，便于后续追评与对照。核对基线：上游
`mmqz/cpa-multi-plugins` v0.8.93（commit `c326248`）与
`jlcodes99/cockpit-tools` `origin/main`（2026-11-04 读取）。

**已发布（2026-11-04）**：

| 位置 | 链接 | 状态 |
| --- | --- | --- |
| Issue A | https://github.com/mmqz/cpa-multi-plugins/issues/74 | OPEN |
| Issue B | https://github.com/mmqz/cpa-multi-plugins/issues/75 | OPEN |
| #2676 评论 | https://github.com/jlcodes99/cockpit-tools/issues/2676#issuecomment-6633758076 | 已追加 |

正文首部的 `<!-- ... -->` 是给本仓看的元信息，GitHub 渲染时不可见；文件本身
可直接 `--body-file` 复用。

| 文件 | 用途 | 目标 |
| --- | --- | --- |
| `issue-a-defer-exhausted-pool.md` | 新 issue 正文 | `mmqz/cpa-multi-plugins` |
| `issue-b-channel-risk-code.md` | 新 issue 正文 | `mmqz/cpa-multi-plugins` |
| `comment-cockpit-2676.md` | 追加评论（不改正文） | `jlcodes99/cockpit-tools` #2676 |

**复现验证状态（2026-11-04）**：

- Issue B：函数体已在隔离程序中原样跑过 —— `{"code":"11-128"}` 返回 `false`，
  `{"code":11128}` 与 `{"code":"11128"}` 返回 `true`。
- Issue A：`pickActiveAuth` 的兜底分支已原样复现（全池 exhausted → 返回 `wb-a`）；
  **完整包测试未在本机执行**，因为 `main.go` 是 cgo（`import "C"`）、本机无 C 工具链
  （`CGO_ENABLED=0` 时 `main.go` 被 Go 排除，`storedAuth` 直接 undefined）。
  首次真实运行在 CI，正文已注明。

## 1. 为什么只提这两条

本仓 `scripts/patches/` 有 12 个 workbuddy 补丁、`outputs/` 有 1 个 cockpit 补丁。
按"特性/缺陷修复可上游、workaround 与本地策略不可上游"分类：

| 补丁 | 判定 | 依据 |
| --- | --- | --- |
| `exhausted-pool-delegate`（0.8.89） | ✅ 可上游 | 上游 `active_auth.go:83-91` 在全池耗尽时仍返回凭据；属真实路由缺口 |
| 11-128 字符串码解析（0.9.84 源码内修复） | ✅ 可上游 | 上游 `upstreamErrorShape.Code int` + 仅容错 `"11128"`，与文案自称的 11-128 矛盾 |
| `selected-account`（5 个版本） | ❌ 不提 | 上游枚举只有 `off`/`credits`，`selected` 从来不是上游特性；价值已被 credits + priority reclaim 覆盖 |
| `account-failover`（11128 → 5 分钟凭据熔断） | ❌ 不提 | 上游 0.9.62 明确改为 request-scoped（`chat_error.go:158-163`），方向相反 |
| `intl-system-first` / `conversation-headers` / `truncation-guard` / `priority-reclaim` | — | 已分别由 PR #42、#43、#50、issue #71 收编，无需再提 |
| `cockpit-tools-v1.3.65-persistent-fix`（8 文件） | ❌ 不提补丁 | 四个入口钳制是生成器缺陷的 workaround；`main` 已在 `:1479/:1481` 修好，只需给 #2676 补证据 |

**当前部署状态**：`outputs/build-workbuddy-0985-20261104.ps1` 是零补丁构建
（`git clone --branch v0.8.93` + gofmt/vet/test/build，无 `git apply`），所以
`selected-account`、`account-failover`、`exhausted-pool-delegate` 三项**都没有随
0.9.85 生效**，11-128 字符串码修复也只存在于 0.9.84 构建；这些补丁是重建素材，
不是线上行为差异的来源。

## 2. 提交顺序（建议）

1. 先发 **Issue A**。它改变默认路由语义，需要维护者先给形状（无条件 defer 还是
   opt-in `scheduler_defer_exhausted_pool`）。
2. 再发 **Issue B**。它只改分类器，风险低，可在 A 讨论期间并行。
3. **#2676 评论**随时可发，不涉及代码。

上游的既有习惯是"先 design discussion、再 PR"（#57/#59/#61 → #58/#60/#64），
所以不要跳过 issue 直接开 PR。

## 3. 提交命令（2026-11-04 已执行）

```bash
export HTTPS_PROXY=http://127.0.0.1:10809 HTTP_PROXY=http://127.0.0.1:10809 NO_PROXY=''

gh issue create --repo mmqz/cpa-multi-plugins \
  --title '[workbuddy] scheduler.pick returns a credit-exhausted credential, so mixed-provider requests never reach another provider' \
  --body-file outputs/upstream-drafts-20261104/issue-a-defer-exhausted-pool.md

gh issue create --repo mmqz/cpa-multi-plugins \
  --title '[workbuddy] isChannelRiskControl matches 11128 only — the hyphenated "11-128" wire code falls through' \
  --body-file outputs/upstream-drafts-20261104/issue-b-channel-risk-code.md

gh issue comment 2676 --repo jlcodes99/cockpit-tools \
  --body-file outputs/upstream-drafts-20261104/comment-cockpit-2676.md
```

## 4. 后续 PR 的硬要求

- **独立 worktree**：`git worktree add /tmp/cpa-multi-plugins-pr main`，不要在打补丁
  的树上改；现有补丁是 `git apply --recount` 打到 0.8.89 的，必须按当前 `main` 重做。
- **只带该 issue 的 hunk**：`exhausted-pool-delegate` 补丁叠加在 `account-failover`
  之上，PR 里必须去掉 `rejectAutomaticPool` 与 5 分钟熔断相关 hunk；提交前 grep
  确认无 `oauth_pool_unavailable`、`channel_rejected`、`markModelCooldownUntil`。
- **上游自带检查**：`gofmt -l *.go`（须为空）、`go vet ./...`、`go test -count=1 ./...`。
- **PR 正文要说明生效条件**：Issue A 的改动只在"存在非 WorkBuddy 候选"时才有可观测
  差异；否则宿主只是把"耗尽凭据失败"换成"无可用凭据"，必须写清楚，避免 reviewer
  合了看不到变化。
- **fork PR 的 `action_required` 不是失败**：`gh pr checks` 会显示 "no checks reported"，
  此时应留言请维护者批准 workflow，而不是改代码。

## 5. 退役条件（提完以后怎么收口）

- Issue A 合并并出现在 release tag 后：删除 `scripts/patches/workbuddy-*-exhausted-pool-delegate.patch`，
  同步 `docs/runbooks/cpa-workbuddy-plugin.md` 的补丁矩阵。
- Issue B 合并后：删除 0.9.84 时代源码内的字符串码分支（若届时仍有该构建），
  在 runbook §4 更新 11-128 分类口径。
- #2676 关闭后：`outputs/cockpit-tools-v1.3.65-persistent-fix.patch` 可移入历史，
  `cockpit_sidecar_guardrails.ps1` 的 `SIDECAR_PATCH_RETIRED` 分支保留（防回退）。
