"""Updater release-selection behaviour, kept in its own module.

These cases used to live inside tests/test_cpa_updater_apply.py. A parallel
session twice reverted that shared file while it was being edited, taking the
tests (but not the script change) with it. Selection is a self-contained
contract, so it gets its own file: a concurrent edit to the big module can no
longer silently delete the coverage for SOAK_PENDING or fork-tag parsing.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

UPDATER = Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"


def selection_program() -> str:
    source = UPDATER.read_text(encoding="utf-8")
    start = source.index("if ! SELECTION=$(python3")
    return source[start:].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def run_selection(compose_text: str, releases: list[dict], tags: list[dict]) -> str:
    program = selection_program()
    with tempfile.TemporaryDirectory() as directory:
        compose = Path(directory) / "compose.yml"
        compose.write_text(compose_text)
        # The releases endpoint returns a bare list; the Docker Hub tags
        # endpoint wraps its rows in "results".
        responses = [
            io.BytesIO(json.dumps(releases).encode()),
            io.BytesIO(json.dumps({"results": tags}).encode()),
        ]
        output = io.StringIO()
        with (
            mock.patch("urllib.request.urlopen", side_effect=responses),
            mock.patch.object(sys, "argv", ["select", str(compose)]),
            contextlib.redirect_stdout(output),
        ):
            exec(compile(program, "updater-selection", "exec"), {})
    return output.getvalue()


def rows(*pairs: tuple[str, str]) -> tuple[list[dict], list[dict]]:
    now = dt.datetime.now(dt.timezone.utc)

    def stamp(age: dt.timedelta) -> str:
        return (now - age).isoformat()

    ages = {"old": dt.timedelta(days=4), "fresh": dt.timedelta(hours=1)}
    releases = [
        {
            "tag_name": tag,
            "published_at": stamp(ages[age]),
            "draft": False,
            "prerelease": False,
        }
        for tag, age in pairs
    ]
    tags = [
        {"name": tag, "last_updated": stamp(ages[age]), "digest": "sha256:" + "a" * 64}
        for tag, age in pairs
    ]
    return releases, tags


class UpdaterSelectionTests(unittest.TestCase):
    def test_soak_pending_is_announced_when_nothing_is_mature(self) -> None:
        releases, tags = rows(("v8.0.61", "old"), ("v8.0.62", "fresh"))
        out = run_selection("image: eceasy/cli-proxy-api:v8.0.61\n", releases, tags)
        self.assertEqual(out.splitlines()[0].split()[:2], ["v8.0.61", "v8.0.61"])
        soak = [line for line in out.splitlines() if line.startswith("SOAK_PENDING ")]
        self.assertEqual(len(soak), 1)
        self.assertIn("newest=v8.0.62", soak[0])
        self.assertIn("remaining_hours=", soak[0])

    def test_soak_pending_reports_only_the_newest_soaking_tag(self) -> None:
        releases, tags = rows(
            ("v8.0.59", "old"),
            ("v8.0.61", "old"),
            ("v8.0.62", "fresh"),
            ("v8.0.63", "fresh"),
        )
        out = run_selection("image: eceasy/cli-proxy-api:v8.0.61\n", releases, tags)
        lines = out.splitlines()
        self.assertEqual(lines[0].split()[:2], ["v8.0.61", "v8.0.61"])
        soak = [line for line in lines if line.startswith("SOAK_PENDING ")]
        self.assertEqual(len(soak), 1)
        self.assertIn("newest=v8.0.63", soak[0])
        self.assertNotIn("v8.0.59", soak[0])

    def test_no_soak_line_when_a_mature_patch_candidate_exists(self) -> None:
        releases, tags = rows(("v8.0.61", "old"), ("v8.0.62", "old"))
        out = run_selection("image: eceasy/cli-proxy-api:v8.0.61\n", releases, tags)
        self.assertEqual(out.splitlines()[0].split()[:2], ["v8.0.61", "v8.0.62"])
        self.assertEqual(
            [line for line in out.splitlines() if line.startswith("SOAK_PENDING ")], []
        )

    def test_fork_image_tag_reports_its_upstream_version(self) -> None:
        releases, tags = rows(("v8.0.61", "old"), ("v8.0.62", "fresh"))
        for declaration in (
            "cli-proxy-api-fork:v8.0.61-gemini-raw",
            "cli-proxy-api-fork:v8.0.61-gemini-raw@sha256:" + "d" * 64,
        ):
            with self.subTest(declaration=declaration):
                out = run_selection("image: " + declaration + "\n", releases, tags)
                first = out.splitlines()[0].split()
                # The derived image still reports the upstream version it was
                # built from, so soak/minor/major math keeps working.
                self.assertEqual(first[0], "v8.0.61")
                self.assertEqual(first[1], "v8.0.61")
                soak = [
                    line
                    for line in out.splitlines()
                    if line.startswith("SOAK_PENDING ")
                ]
                self.assertEqual(len(soak), 1)
                self.assertIn("newest=v8.0.62", soak[0])


if __name__ == "__main__":
    unittest.main()
