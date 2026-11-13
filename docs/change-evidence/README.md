# change-evidence 目录治理（索引与政策）

本目录存放**真实远端写入、事故排障与 release** 的脱敏收据（AGENTS.md「证据分级管理」条款）。
本页只落政策与流程，不维护文件清单——清单靠引用关系推断，手工名单必然漂移。

## 判定规则

- **活跃收据**：被 README、`docs/runbooks/`、脚本或测试显式引用的收据（例如
  `README.md` 指定的交接书 `20261108-bwg-cpa-handoff.md`、runbook 收据链中的条目）。
  判断某文件是否活跃，用引用源 grep 文件名即可。
- **纯历史收据**：未被任何活跃面引用、仅供追溯的收据。它们**不进入默认编码上下文**：
  agent 会话与人工排查默认不读它们，只在明确追溯历史时按名取用。

## 存量快照（2026-11-14 复核）

- 在树收据 127 份（全部 `.md`）：202609 共 7 份、202610 共 23 份、202611 共 97 份。
- 2026-11-14 归档动作：207 份 2026-11 之前且未被任何活跃面引用的纯历史收据移入
  git 历史（含旧例外命名件 `2026-10-17-claude-oauth-admission.md`），
  `git show <rev>:<path>` 按名取回；被 runbook/脚本/测试点名的 31 份 11 月前收据
  与全部 2026-11 收据留在树内。
- 按名被引用 45 份；其余 82 份**全部是本月**收据——它们是当期远端写入的原始记录，
  按上一条留在树内，不是待清理存量。判定"该不该删"只看两条：是否跨月、以及是否
  被活跃面引用；本月收据一律不删。
- 命名约定：`YYYYMMDD-主题.md`。

## 归档路径（政策，按 AGENTS.md 执行）

1. 纯历史收据应迁往**仓外归档**或**只读归档分支**；Git 历史本身即完整保留，迁移
   （删除工作树文件）不丢任何信息，回滚 = `git show <rev>:<path>` 取回。
2. 迁移前核对：目标文件必须不在活跃引用集合内；仍在引用链上的收据留在树内。
3. 归档动作单独授权执行（批量删除类变更），不随常规功能改动捎带。
4. 需要现场盘点时按需运行（不进默认门禁——这是一次盘点，不是每次改动的成本）。
   把下面片段存成临时文件后运行，输出即"未被活跃面引用"的收据名：

   ```python
   from pathlib import Path

   root = Path(".")
   sources = [root / n for n in ("README.md", "README.en.md", "AGENTS.md")]
   for sub in ("docs/runbooks", "scripts", "tests"):
       sources.extend(p for p in (root / sub).rglob("*") if p.is_file())

   live = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources)
   receipts = sorted((root / "docs/change-evidence").glob("*.md"))
   for path in receipts:
       if path.name != "README.md" and path.name not in live:
           print(path.name)
   ```

   跨月且未被引用的才进入归档候选；本月收据不删。
