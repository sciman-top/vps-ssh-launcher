"""只读复核：`deepseek-v4.1-flash` 在最近 24h 的失败形态。

数据源 = ~/.antigravity_cockpit/codex_local_access_logs.sqlite 的 request_logs（本机权威口径）。
只读打开（mode=ro），不做任何写入。
"""

import sqlite3
import pathlib
import datetime as dt
import json

DB = pathlib.Path.home() / ".antigravity_cockpit" / "codex_local_access_logs.sqlite"
MODEL = "deepseek-v4.1-flash"


def ts(ms: int | None) -> str:
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


def main() -> int:
    if not DB.exists():
        print("DB 不存在:", DB)
        return 2
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(request_logs)")]
    print("=== request_logs 列 ===")
    print(", ".join(cols))

    newest = cur.execute("SELECT MAX(timestamp) FROM request_logs").fetchone()[0]
    oldest = cur.execute("SELECT MIN(timestamp) FROM request_logs").fetchone()[0]
    print(f"\n=== 窗口 ===\n  oldest={ts(oldest)}  newest={ts(newest)}  now={ts(int(dt.datetime.now().timestamp()*1000))}")

    print(f"\n=== {MODEL} 逐日分布 ===")
    for row in cur.execute(
        "SELECT strftime('%m-%d', timestamp/1000, 'unixepoch', 'localtime') AS d,"
        " COUNT(*) AS n, SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS ok,"
        " MIN(timestamp) AS t0, MAX(timestamp) AS t1"
        " FROM request_logs WHERE requested_model LIKE ? GROUP BY d ORDER BY d",
        (f"%{MODEL}%",),
    ):
        print(f"  {row['d']}  n={row['n']:<4} ok={row['ok']:<4}  {ts(row['t0'])} → {ts(row['t1'])}")

    print(f"\n=== {MODEL} 最近 8 条明细 ===")
    sel = "SELECT * FROM request_logs WHERE requested_model LIKE ? ORDER BY timestamp DESC LIMIT 8"
    for row in cur.execute(sel, (f"%{MODEL}%",)):
        d = dict(row)
        keys = [k for k in d if d[k] not in (None, "", 0)]
        print(f"  --- {ts(d.get('timestamp'))}")
        for k in keys:
            v = d[k]
            if isinstance(v, str) and len(v) > 220:
                v = v[:220] + "…"
            print(f"      {k} = {v}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
