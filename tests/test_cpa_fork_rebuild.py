import subprocess
import unittest
from pathlib import Path


def test_production_build_image_requires_a_digest_pin() -> None:
    source = (Path(__file__).parents[1] / "scripts/cpa_fork_rebuild.ps1").read_text(
        encoding="utf-8"
    )
    assert "-Apply requires -BuildImage" in source
    assert r"^golang:[0-9]+\.[0-9]+\.[0-9]+-bookworm@sha256:[0-9a-f]{64}$" in source
    assert '"BUILD_IMAGE_ID=$BUILD_IMAGE_ID image=$BUILD_IMAGE"' in source
    assert '"BUILD_IMAGE_DIGEST_VERIFIED $EXPECTED_PLATFORM_DIGEST"' in source
    assert "BUILD_IMAGE_DIGEST_MISMATCH" in source
    assert 'BUILD_IMAGE_REPO_DIGEST" = "golang@$EXPECTED_PLATFORM_DIGEST"' in source


def test_builder_digest_selector_matches_the_exact_pinned_repo_digest() -> None:
    source = (Path(__file__).parents[1] / "scripts/cpa_fork_rebuild.ps1").read_text(
        encoding="utf-8"
    )
    assert '"$BUILD_IMAGE_REPO_DIGEST" = "golang@$EXPECTED_PLATFORM_DIGEST"' in source
    assert 'done <<<"$BUILD_PLATFORM_DIGESTS"' in source


def test_builder_digest_check_rejects_missing_expected_digest() -> None:
    source = (Path(__file__).parents[1] / "scripts/cpa_fork_rebuild.ps1").read_text(
        encoding="utf-8"
    )
    assert '[ "$BUILD_DIGEST_MATCHED" != 1 ]' in source
    assert "BUILD_IMAGE_DIGEST_MISMATCH" in source
    assert "BUILD_IMAGE_DIGEST_UNREADABLE" in source
    assert "BUILD_IMAGE_DIGEST_INVALID" in source
    assert "| sort -u" in source


def test_builder_digest_selector_checks_failure_by_one_or_zero_status() -> None:
    source = (Path(__file__).parents[1] / "scripts/cpa_fork_rebuild.ps1").read_text(
        encoding="utf-8"
    )
    assert 'if [ "$BUILD_DIGEST_MATCHED" != 1 ]; then' in source
    assert '"$BUILD_DIGEST_MATCHED" -ne 1' not in source


def test_builder_digest_selector_rejects_mismatch_on_real_bash() -> None:
    """Prove the exact selector does not pass merely because a command failed."""
    bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if not bash.is_file():
        raise unittest.SkipTest("Git Bash is unavailable")
    script = (
        'BUILD_PLATFORM_DIGESTS="golang@sha256:'
        + "b" * 64
        + '"\n'
        + "EXPECTED_PLATFORM_DIGEST=sha256:"
        + "a" * 64
        + "\n"
        + "MATCHED=0\n"
        + "while IFS= read -r REPO_DIGEST; do\n"
        + '  if [ "$REPO_DIGEST" = "golang@$EXPECTED_PLATFORM_DIGEST" ]; then MATCHED=1; break; fi\n'
        + 'done <<<"$BUILD_PLATFORM_DIGESTS"\n'
        + 'if [ "$MATCHED" -ne 1 ]; then printf reject; fi\n'
    )
    result = subprocess.run(
        [bash.as_posix(), "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "reject"
