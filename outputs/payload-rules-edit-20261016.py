"""Phase 1 of the codex-family payload-rules extension (2026-10-16).

Runs on the VPS. Prints the exact "requests:" section text of the deployed
config (no credentials in that section) so the insert can be crafted
byte-faithfully. No writes.
"""

import sys

lines = open("/opt/cliproxyapi/config.yaml", encoding="utf-8").read().splitlines()
start = None
for index, line in enumerate(lines):
    if line.rstrip() == "requests:" and (not line.startswith(" ")):
        start = index
        break
if start is None:
    print("REQUESTS_SECTION_NOT_FOUND")
    sys.exit(1)
end = len(lines)
for index in range(start + 1, len(lines)):
    if lines[index] and not lines[index].startswith(" "):
        end = index
        break
for number in range(start, end):
    print("%d\t%s" % (number + 1, lines[number]))
