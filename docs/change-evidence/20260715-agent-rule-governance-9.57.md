# Agent Rule Governance 9.57

## Scope and boundary

- repository: `vps-ssh-launcher`
- frozen baseline: `2a575120314fb578651f3ddb619ebd63d427636b`
- task branch: `codex/agent-rule-governance-9.57`
- write-set: `AGENTS.md` and this evidence file; `CLAUDE.md` remains the verified import-only wrapper
- release review: `rule_release=9.57 / project_contract_version=2.0 / coordination_schema=2.3`
- semantic basis: Claude Code's current official memory documentation permits imports up to five hops; the project WHERE/HOW contract itself is unchanged
- exclusions: no product/runtime/schema/data/dependency/auth/provider/secret/MCP/account/process/hosted-UI change

## Verification ledger

- wrapper: `CLAUDE.md` verified as the import-only `@AGENTS.md` wrapper, no BOM; control-repo `--require-all` target audit passed for all 9 isolated targets
- build/test: repository build passed; pytest passed 93 tests with 1 skip and 20 subtests; unittest passed 94 tests with 1 skip
- contract/invariant/hotspot: isolated `.venv` setup was used, then `scripts/run_gates.ps1` passed pip check, pip-audit (no known vulnerabilities), Bandit, vulture, Ruff/format, mypy, pyright, and all tests
- runtime boundary: no real SSH, VPS, credential, account, or process operation was performed
- diff hygiene and five-axis review: passed with no Critical or Required finding
- Git publication: not yet executed at this capture point

## Compatibility and rollback

- compatibility: content-release review marker only; repository commands, invariants, external behavior, data formats, and wrapper loading shape remain unchanged
- rollback: revert only `AGENTS.md` and this evidence file from the task commit; do not reset, clean, or include unrelated local history

## Completion boundary at capture

- `repo-side completed=true`
- `published branch=false`
- `default-branch effective=false`
- `hosted/manual accepted=false`
- `fully completed=false`
