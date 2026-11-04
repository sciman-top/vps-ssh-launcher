# Issue B — `"code":"11-128"` 进不了渠道风控分类

**目标仓库**：`mmqz/cpa-multi-plugins`
**类型**：缺陷（分类器与文案/设计不一致）
**建议标题**：`[workbuddy] isChannelRiskControl matches 11128 only — the hyphenated "11-128" wire code falls through`
**核对基线**：v0.8.93 / commit `c326248ef6fd3dbeee9e16c53adf279ffa5904cf`

---

**TL;DR** — `isChannelRiskControl` can never match the hyphenated code
`"11-128"`, although the error copy for that very branch is written for 11-128
and the branch exists to stop the host from cooling accounts.

**Facts (v0.8.93)**

```go
// chat_error.go:25-28
type upstreamErrorShape struct {
	Code int    `json:"code"`
	Msg  string `json:"msg"`
}

// chat_error.go:317-328
func isChannelRiskControl(statusCode int, payload string) bool {
	if statusCode < 400 {
		return false
	}
	var shape upstreamErrorShape
	if err := json.Unmarshal([]byte(payload), &shape); err == nil && shape.Code == 11128 {
		return true
	}
	low := strings.ReplaceAll(payload, " ", "")
	return strings.Contains(low, `"code":11128`) || strings.Contains(low, `"code":"11128"`)
}
```

For `{"code":"11-128","msg":"request illegal"}`:

- `json.Unmarshal` into `Code int` fails on the string value;
- the substring fallback only looks for `11128`;
- so the function returns `false`.

**Why it matters** — `upstreamStatusError` (`chat_error.go:156-183`) attaches
`400` (request-scoped, `clienterror.IsRequestFault`) only through this matcher.
Its own comment at `:158` and the user-facing copy at `:431-441` both describe
11-128. If the CN gate emits the hyphenated string code — our capture on
2026-11-02 was `{"code":"11-128"}` — the rejection is classified as a generic
4xx, so the host may read it as a credential fault, walk the pool and cool every
account. That is exactly the failure mode the comment at `:158-163` says this
branch exists to prevent.

**Repro** (verified on v0.8.93 logic, run in isolation)

```go
if !isChannelRiskControl(400, `{"code":"11-128","msg":"request illegal"}`) {
	t.Fatal(`"code":"11-128" must classify as channel risk control`)
}
```

Measured against the exact function body: `{"code":"11-128",...}` → `false`,
`{"code":11128,...}` → `true`, `{"code":"11128"}` → `true`.

**Proposed fix** — decode `code` as `json.RawMessage` (or `any`) and normalise:
accept `11128`, `"11128"` and `"11-128"` by comparing after stripping `-`. Keep
the existing substring fallback for wrapped payloads, extended with
`"code":"11-128"`.

Two tests: the hyphenated string above must be `true`; `{"code":11115}` and a
bare 403 must stay `false` (the existing cases in `large_input_test.go:43-54`
already cover the negative side).

**Caveat** — I only hold the string-shape capture from the CN realm. If you
consider `"11-128"` a display-only spelling and the wire code is always `11128`,
then the fix belongs in the copy at `:431` instead — but today the matcher and
the copy disagree, and one of them is wrong.
