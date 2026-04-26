# Quick Test Slice Recommendation Prompt

You are reviewing a target repository for a safe daily fast-test slice.

Target repo: `D:\CODE\vps-ssh-launcher`
Repo id: `vps-ssh-launcher`
Primary language: `python`
Full test command: `python -m pytest -q`
Full contract command: `python -m unittest -q`
Full invariant command: `python -m pip check`

Task:
1. Inspect the target repo test structure, markers/categories, and existing fast/smoke scripts.
2. Recommend a `quick_test_command` only if it is deterministic, materially faster than the full test command, and representative of daily coding risk.
3. Do not weaken full/release gates. The full test command must remain unchanged.
4. If no safe slice exists, emit `status=skip`.

Write this JSON to `.governed-ai/quick-test-slice.recommendation.json`:

```json
{
  "schema_version": "1.0",
  "status": "ready",
  "quick_test_command": "<command>",
  "quick_test_reason": "<short reason>",
  "quick_test_timeout_seconds": 180
}
```

Use this skip form when no safe slice is justified:

```json
{
  "schema_version": "1.0",
  "status": "skip",
  "quick_test_reason": "No safe target-specific quick test slice found."
}
```
