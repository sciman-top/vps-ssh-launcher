# Global rule 9.73 project-contract evidence

- Repository: `vps-ssh-launcher`
- Scope: project rule mapping only; no business-code or host-runtime mutation.
- Official basis: current Codex AGENTS loading/precedence and rules semantics; Claude platform delta remains separately verified.
- Git profile: baseline=`main`; upstream=`origin/main`.
- Before AGENTS SHA-256: `E6966B4E116BB75298AF7E1D90B208D34081C6CDFA38E2429186E1BFD5118632`
- After AGENTS SHA-256: `698EB609A221866434939E65DD265A692C67189C67E02BD80DC9C06188747E80`
- Planned gate: `pwsh -NoProfile -File scripts/run_gates.ps1`
- Current verification: full gate passed after restoring the tested sequential-safety wording; 93 pytest passed/1 skipped, 94 unittest passed/1 skipped, dependency/security/lint/format/type gates passed (pyright: 0 errors, 1 source-resolution warning).
- N/A: host loading and live acceptance remain outside repository-static verification.
- Rollback: revert only this repository's `AGENTS.md` and this evidence file to the recorded before hash.
- Truth boundary: `repo_verified=passed`; `host_loaded=codex_fresh_prompt_verified`; `claude_loaded=not_run`; `live_accepted=not_run`.
