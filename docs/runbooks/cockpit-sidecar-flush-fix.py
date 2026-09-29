#!/usr/bin/env python3
"""结构式修补 writeProviderGatewayResponsesStream —— 给 Cockpit sidecar 的 responses 透传加 Flush。

比行号补丁更稳：按锚点定位函数体再插入，版本变化后仍能命中；已修补则跳过（幂等）。
用法：  python apply_flush_fix.py <provider_gateway.go> [--check]
退出码：0 = 已修补/本次修补成功；1 = 未命中（需人工介入）；2 = 已修补（--check 模式）
"""
import re
import sys

ANCHOR_DECL = (
    "\tflusher, ok := c.Writer.(http.Flusher)\n"
    "\tif !ok {\n"
    '\t\twriteAPIError(c, http.StatusInternalServerError, "streaming not supported", "streaming_not_supported")\n'
    "\t\treturn\n"
    "\t}\n"
)
ANCHOR_FLUSH = (
    "\t\t\t// SSE events are terminated by a blank line; flush there so the client sees\n"
    "\t\t\t// each event as it arrives instead of one bufio burst at the end.\n"
    '\t\t\tif len(bytes.TrimRight(line, "\\r\\n")) == 0 {\n'
    "\t\t\t\tflusher.Flush()\n"
    "\t\t\t}\n"
)

FUNC_RE = re.compile(
    r"(func \(s \*relayServer\) writeProviderGatewayResponsesStream\([^)]*\) \{\n)"
    r"(.*?)"
    r"(\n\}\n)",
    re.S,
)


def patch(text: str) -> tuple[str, str]:
    m = FUNC_RE.search(text)
    if not m:
        return text, "function_not_found"
    head, body, tail = m.group(1), m.group(2), m.group(3)
    if "c.Writer.(http.Flusher)" in body:
        return text, "already_patched"

    # (a) 声明 flusher：插在函数开头的 nil guard 之后
    guard = re.search(r"\tif body == nil \{\n\t\treturn\n\t\}\n", body)
    if not guard:
        return text, "nil_guard_not_found"
    body = body[: guard.end()] + ANCHOR_DECL + body[guard.end():]

    # (b) 每写完一行后，遇到空行（SSE 事件边界）就 flush
    write_blk = re.search(
        r"(\t\t\tif _, writeErr := c\.Writer\.Write\([^\n]*\); writeErr != nil \{\n"
        r"\t\t\t\treturn\n"
        r"\t\t\t\}\n)",
        body,
    )
    if not write_blk:
        return text, "write_block_not_found"
    body = body[: write_blk.end()] + ANCHOR_FLUSH + body[write_blk.end():]

    # (c) 流结束时补一次 flush（含出错提前返回）
    #     取函数体内最后一个 `if err != nil { return }` 块，不依赖其后的缩进结构。
    tail_re = re.compile(r"\n\t\tif err != nil \{\n\t\t\treturn\n\t\t\}\n")
    matches = list(tail_re.finditer(body))
    if not matches:
        return text, "tail_block_not_found"
    tail_blk = matches[-1]
    body = (
        body[: tail_blk.start()]
        + "\n\t\tif err != nil {\n\t\t\tflusher.Flush()\n\t\t\treturn\n\t\t}\n"
        + body[tail_blk.end():]
    )

    return text[: m.start()] + head + body + tail + text[m.end():], "patched"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: apply_flush_fix.py <provider_gateway.go> [--check]")
        return 1
    path = sys.argv[1]
    check_only = "--check" in sys.argv
    with open(path, encoding="utf-8", newline="") as fh:
        original = fh.read()

    # Windows 上 git 工作树常是 CRLF，而锚点按 LF 写；先归一化再匹配，
    # 最后按原文件的换行风格还原，保证只改动目标函数。
    uses_crlf = "\r\n" in original
    work = original.replace("\r\n", "\n") if uses_crlf else original

    result, status = patch(work)
    print(f"status={status} line_ending={'CRLF' if uses_crlf else 'LF'}")
    if status == "already_patched":
        return 2 if check_only else 0
    if status != "patched":
        print("FAILED: 锚点未命中，请人工处理")
        return 1
    if check_only:
        return 0
    out = result.replace("\n", "\r\n") if uses_crlf else result
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(out)
    print(f"patched: {len(original)} -> {len(out)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
