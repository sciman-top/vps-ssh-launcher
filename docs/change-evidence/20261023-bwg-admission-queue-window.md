# BWG admission long-cooldown queue fix (2026-10-23)

## Scope

The `chatgpt-oauth` lane can hold a local cooldown of up to 900 s while still
permitting an early recovery probe after `early_probe_interval_seconds` (10 s).
The admission queue only compared the cooldown expiry against the 120 s queue
budget, so a request arriving mid-cooldown received an immediate 429 even when
a permitted probe slot was seconds away.

Change: when a non-strict lane's next permitted admission (early probe slot)
fits inside the existing queue budget, the request waits in the bounded FIFO
instead of being refused. Server `Retry-After`, strict-lane backoff, queue
capacity, probe count, and concurrency limits are unchanged.

## Verification

- Regression test failed on the old code (`cooldown`, `retry_after=900`,
  `waited_ms=0`) and passes after the change (`early_probe`, `waited_ms=10000`).
- `tests/test_cpa_admission.py`: 184 passed.
- Full gates: 730 passed, 1 skipped, 347 subtests; Bandit, Ruff, format, mypy
  passed. The skipped test is the opt-in real-SSH integration test.
- Commit `074815a` pushed to `origin/main`.

## Host projection

- First apply attempt refused with
  `REFUSE_ACTIVE_REQUESTS no_files_changed=true`; no file changed.
- Second attempt acquired the traffic guard and applied only
  `/opt/cliproxyapi/cpa-admission.py` and
  `/etc/vps-ssh-launcher/cpa-admission.sha256`. CPA container and Codex were
  not restarted.
- Backup: `/root/cpa-admission-queue-fix-20261023T104332`.
- Loaded file SHA-256:
  `8a8e1baef23008809be84c29af355221ac36ef8026a60f36fc219c712928a557`.
- `ADMISSION_INTEGRITY_OK`; `cpa-admission` active with `NRestarts=0`.
- Host-level replay of the deployed module: `early_probe`, `waited_ms=10000`.
- One bounded live request through the public route (`gpt-5.6-terra`)
  returned HTTP 200 `status=completed`.
- One bounded live `gpt-6.1-sol` request during the lane's remaining cooldown
  was admitted as `lane_probe ... reason=half_open` and completed in 11 s
  (`status=200 capacity=false successful=true`), clearing the lane to
  `cooldown 0`. This is the first live confirmation that the new queue window
  replaces an immediate 429 with a single recovery probe.
- Not verified: sustained Sol/Luna availability under renewed upstream
  overload, and any claim that upstream capacity or account risk is resolved.

## Rollback

Restore both files from the backup directory and restart `cpa-admission`:

```bash
cp -p /root/cpa-admission-queue-fix-20261023T104332/cpa-admission.py /opt/cliproxyapi/cpa-admission.py
cp -p /root/cpa-admission-queue-fix-20261023T104332/cpa-admission.sha256 /etc/vps-ssh-launcher/cpa-admission.sha256
systemctl restart cpa-admission
```
