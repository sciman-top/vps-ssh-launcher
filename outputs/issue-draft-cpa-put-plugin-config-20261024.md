# Issue draft — for the maintainer to review and post (user-gated, not auto-filed)

Target repos: `router-for-me/CLIProxyAPI` (primary), optionally
`mmqz/cpa-multi-plugins` (docs cross-link). Do not post without owner review;
adjust tone/wording as desired.

---

**Title**: Management `PUT /v8/management/config/plugins/configs/<id>` replaces
the whole section, and the config-change log does not report `plugins.*`
changes — partial-form clients silently drop plugin fields

**Body**:

Observed on v8.0.40/41 with a third-party management panel
(anonymized details; happy to add versions/logs if useful).

1. `PutPluginConfig`
   (`internal/api/handlers/management/plugins.go`) assigns
   `h.cfg.Plugins.Configs[id] = updated` — full-section replace. `PATCH` is
   the shallow-merge variant. A panel whose edit form does not render every
   registered `ConfigField` will, on save, silently delete the fields it did
   not include (in our case a plugin's configured model pin list
   `models_cn`/`models_intl`). The plugin then re-registers with the reduced
   config and legitimately changes behaviour (advertises its full upstream
   model menu instead of the pinned list).

2. The reload-time change report
   (`internal/watcher/diff/config_diff.go`, `BuildConfigChangeDetails`) does
   not cover the `plugins.*` section at all, so the destructive rewrite is
   invisible in the "config changes detected" log — we only found it because
   the plugin's own diagnostics and an unrelated strict doctor gate
   (`MODEL_IDS_UNKNOWN`) went red.

Suggestions (any one would have contained the blast radius):

- Include `plugins.configs.<id>` key-level changes (names only, values
  redacted) in `BuildConfigChangeDetails`.
- Document `PUT` = replace / `PATCH` = merge prominently in the plugins
  management API docs, so panel authors default to `PATCH`.
- Optionally: a server-side guard that lists keys present before but absent
  after a `PUT` in the response payload, so clients can detect accidental
  drops.

Our containment notes (for other plugin operators): pin critical plugin
config fields after every panel save (`grep`/doctor), and prefer `PATCH` for
programmatic edits.

---

## 本仓判读锚（不进 issue）

- 事故时间线与源码锚：`docs/change-evidence/20261024-bwg-cpa-v8041-canary.md`
  根因修正节。
- 已核实：`PutPluginConfig` 替换、`PatchPluginConfig` 浅合并、
  `BuildConfigChangeDetails` 白名单不含 plugins.*、
  `defaultRuntimeItemConfig` 无调用方（不存在空 payload 注册路径）。
- 发布动作属对外行为，由所有者在 GitHub 上手动完成。
