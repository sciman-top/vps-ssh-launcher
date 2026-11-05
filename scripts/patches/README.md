# scripts/patches — WorkBuddy 插件本地补丁索引

上游：`github.com/mmqz/cpa-multi-plugins`（MIT）。BWG 当前生产为官方
`v0.8.94` / WorkBuddy `0.9.86`（`.so` SHA `04ed2703…`）的零补丁构建。
`workbuddy-v0.8.93-intl-shared-window.patch` 基于已被跨出口、跨 CDN 边缘和
跨 token 探针证否的 INT 共享桶假设，属于**历史/已退役**；它会把仍可用的
独立账号日窗短路，不能用于 0.9.86 生产。历史补丁仍是重建素材，不代表线上
行为；引用前先确认目标构建与 `docs/runbooks/cpa-workbuddy-plugin.md` 的当前版本段。

| 文件 | 目标 tag | 内容 | 状态 |
| --- | --- | --- | --- |
| `workbuddy-v0.8.68-selected-account.patch` | v0.8.68 | 新增 `scheduler_mode: selected`（所选账号不可用时拒绝回退） | 历史 |
| `workbuddy-v0.8.68-account-failover.patch` | v0.8.68 | 11128 → 凭据级五分钟熔断；全池不可用时 `Reject` | 历史 |
| `workbuddy-v0.8.68-intl-system-first.patch` | v0.8.68 | INTL realm 的 system-first 门 | 已退役（上游 PR #42 收编） |
| `workbuddy-v0.8.72-conversation-headers.patch` | v0.8.72 | 会话头三件套（Intl 扩展） | 已退役（上游 0.9.61+ 无条件填充 / PR #43） |
| `workbuddy-v0.8.78-truncation-guard.patch` | v0.8.78 | 200 流无 `finish_reason` 时按 `truncated_stream` 失败关闭 | 已退役（上游 PR #50） |
| `workbuddy-v0.8.84-selected-account.patch` | v0.8.84 | 同 0.8.68 版，仅 `main.go` 配置清单 hunk 适配 | 历史 |
| `workbuddy-v0.8.84-account-failover.patch` | v0.8.84 | 同上 | 历史 |
| `workbuddy-v0.8.86-selected-account.patch` | v0.8.86 | 同上（体积大，含整段上下文） | 历史 |
| `workbuddy-v0.8.87-selected-account.patch` | v0.8.87 | 同上 | 历史 |
| `workbuddy-v0.8.89-selected-account.patch` | v0.8.89 | 最后一版 selected 模式 | 历史 |
| `workbuddy-v0.8.89-account-failover.patch` | v0.8.89 | 最后一版熔断；`Reject` 语义 | 历史 |
| `workbuddy-v0.8.89-exhausted-pool-delegate.patch` | v0.8.89 | 全池冷却/额度耗尽时改判 `Handled:false` 交还宿主 | 历史；**该语义可上游** |
| `workbuddy-v0.8.93-intl-shared-window.patch` | v0.8.93 | INTL 6004 按 realm+model 共享 reset；CN/Global 保持账号级 | **历史/已退役（共享假设未证实，禁止生产）** |
| `workbuddy-v0.8.93-cn-local-replay.patch` | v0.8.93 | 私有 fixture-only 本地回放 seam；用于验证 INT 失败后独立 CN 可达 | **验收专用，禁止生产** |

## 使用约束

- **顺序**：`selected-account` → `account-failover` → `exhausted-pool-delegate`；
  第三项会改写前一项的 `Reject` 分支，单独打会失配。
- **打补丁**：`git apply --recount`（hunk 计数非规范）；打完必须
  `gofmt -l`、`go vet ./...`、`go test -count=1 ./...` 全过再构建。
- **0.9.85 及以后**：上游枚举只有 `off`/`credits`，`scheduler_mode: selected`
  会让插件把每次 pick 都 defer 给宿主，不再具备"固定账号"语义。
- **上游化**：只提交缺陷/特性 hunk，不提交入口钳制与本地策略值；评估与正文见
  `outputs/upstream-drafts-20261104/README.md`。
## workbuddy-v0.8.93-intl-shared-window.patch（历史，不可用于生产）

该补丁曾用于 `v0.8.93` / WorkBuddy `0.9.85`，把一次 INTL 6004 扩展为
`(realm=intl, model)` 共享 reset。后续宿主侧直连探针证明限制键是
`(token, model)` 独立日窗：同 token 跨 BWG/ZZ 出口与不同 CDN 边缘的 reset
相同，而不同 token 的 reset 不同。因此 realm-wide 持久短路会隐藏其他尚未
耗尽的凭据，**已退役，禁止投影或部署**。CN 与 INTL 均按独立账号/模型窗口处理。
