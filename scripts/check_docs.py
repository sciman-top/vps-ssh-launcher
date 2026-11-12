"""Validate paths and test commands in current repository documentation.

Historical change evidence is intentionally excluded: it records what existed
at that time and must not be rewritten when files are moved. This checker
covers the documents that guide current work.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "README.en.md", ROOT / "AGENTS.md"]
DOCS.extend(sorted((ROOT / "docs" / "runbooks").glob("*.md")))
DOCS.extend(sorted((ROOT / "HG03_Final_v4").glob("*.md")))

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^)\"]*[\"'])?\)")
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
PATH_TOKEN = re.compile(
    r"(?:^|[^\w])((?:\./)?(?:tests|scripts|docs|outputs|config)/[A-Za-z0-9._/-]+)"
)
# Inline (backtick) path spans: a wrong prefix in prose survives every fenced
# block check and is exactly what a reader copy-pastes (cpa-admission-risk-aub
# once pointed at scripts/cpa-health.py instead of scripts/remote/).
INLINE_PATH_TOKEN = re.compile(
    r"`((?:tests|scripts|docs|config|vps_ssh_launcher)"
    r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+)`"
)
OPTIONAL_PATHS = {"outputs/doctor-latest.txt"}
# Inline spans that intentionally reference something outside the tree:
# cockpit-fq-access-mode-options.md cites a retracted runbook in its
# correction record (the reference IS the point of that section).
INLINE_ALLOWED: set[str] = {"docs/runbooks/cockpit-direct-mode-switch.md"}
# Commands in the HG-03 handoff are relative to its separately selected kit
# directory. Resolve those references against the kit, never the repository root.
HG03_SOURCE = (ROOT / "HG03_Final_v4").resolve()


def _is_hg03_kit_path(doc: Path, target: str) -> bool:
    if not doc.resolve().is_relative_to(HG03_SOURCE):
        return False
    relative = (doc.parent / target).resolve()
    return relative.is_relative_to(HG03_SOURCE) and relative.exists()


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def check() -> list[str]:
    errors: list[str] = []
    for doc in DOCS:
        if not doc.is_file():
            errors.append(
                f"missing current document: {doc.relative_to(ROOT).as_posix()}"
            )
            continue
        text = doc.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            resolved = (doc.parent / target).resolve()
            if ROOT not in resolved.parents and resolved != ROOT:
                continue
            if not resolved.exists():
                errors.append(
                    f"{doc.relative_to(ROOT).as_posix()}:{line_number(text, match.start())}: "
                    f"broken link {match.group(1)}"
                )
        for match in re.finditer(r"\]\(([^)]+)\)", text):
            target = match.group(1).strip("<>")
            path_part = target.split("#", 1)[0]
            if "://" in path_part or "@" in path_part:
                continue
            if path_part.startswith(("/", "\\", "D:", "d:")):
                errors.append(
                    f"{doc.relative_to(ROOT).as_posix()}:{line_number(text, match.start())}: "
                    "link is not repository-relative"
                )

        for block in FENCED_BLOCK.finditer(text):
            block_text = block.group(1).replace("\\", "/")
            for match in PATH_TOKEN.finditer(block_text):
                target = match.group(1).lstrip("./").rstrip(".,;:")
                if target in OPTIONAL_PATHS or target.endswith("-"):
                    continue
                if not (ROOT / target).exists() and not _is_hg03_kit_path(doc, target):
                    errors.append(
                        f"{doc.relative_to(ROOT).as_posix()}:{line_number(text, block.start(1) + match.start())}: "
                        f"command path does not exist: {target}"
                    )

        for match in INLINE_PATH_TOKEN.finditer(text):
            target = match.group(1)
            if target in INLINE_ALLOWED or target in OPTIONAL_PATHS:
                continue
            if not (ROOT / target).exists() and not _is_hg03_kit_path(doc, target):
                errors.append(
                    f"{doc.relative_to(ROOT).as_posix()}:{line_number(text, match.start())}: "
                    f"inline path does not exist: {target}"
                )

        for match in re.finditer(r"`(test_[A-Za-z0-9_]+\.py)`", text):
            name = match.group(1)
            paths = [ROOT / "tests" / name, ROOT / name]
            if not any(path.exists() for path in paths):
                errors.append(
                    f"{doc.relative_to(ROOT).as_posix()}:{line_number(text, match.start())}: "
                    f"missing test module {name}"
                )
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("\n".join(errors))
        print(f"DOCS_CHECK=FAIL ({len(errors)} issues)")
        return 1
    print(f"DOCS_CHECK=PASS ({len(DOCS)} documents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
