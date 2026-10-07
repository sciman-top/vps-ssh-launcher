"""报告两个 MEMORY.md 的字符数是否落在宿主注入预算内。

宿主常量（app.asar 实证）：
  MAX_MEMORY_CHARS      = 10000  （工作区 MEMORY.md）
  MAX_USER_MEMORY_CHARS = 4000   （用户级 MEMORY.md）
按 JS 字符串长度（≈ UTF-16 码元）截断，不是字节。
"""

import pathlib

TARGETS = [
    ("user   ", pathlib.Path(r"C:\Users\sciman\.workbuddy-ai\MEMORY.md"), 4000),
    ("project", pathlib.Path(r"D:\CODE\vps-ssh-launcher\.workbuddy-ai\memory\MEMORY.md"), 10000),
]


def main() -> int:
    rc = 0
    for label, path, limit in TARGETS:
        text = path.read_text(encoding="utf-8").strip()
        n = len(text)
        raw = path.read_bytes()
        verdict = "OK  " if n <= limit else "OVER"
        if n > limit:
            rc = 1
        print(
            f"[{verdict}] {label} {n:6d} / {limit} chars "
            f"(余 {limit - n:5d})  {len(raw):6d} bytes  {path.name}"
        )
    arch = pathlib.Path(r"C:\Users\sciman\.workbuddy-ai\memory-archive")
    files = sorted(p.name for p in arch.glob("*.md"))
    print(f"\n归档 {len(files)} 个文件 @ {arch}")
    for f in files:
        print("  -", f)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
