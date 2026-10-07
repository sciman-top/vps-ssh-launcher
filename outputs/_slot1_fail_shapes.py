"""只读复核（第二层）：该模型失败的 http_status / error_message / 路由分布。

用于区分「上游 5xx」与「本机别名层/路由解析失败」。
"""

import sqlite3
import pathlib
import datetime as dt

DB = pathlib.Path.home() / ".antigravity_cockpit" / "codex_local_access_logs.sqlite"
MODEL = "deepseek-v4.1-flash"


def ts(ms) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M") if ms else "-"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print("=== 按日 × http_status 分布 ===")
    q = (
        "SELECT strftime('%m-%d', timestamp/1000,'unixepoch','localtime') d, http_status,"
        " error_category, gateway_mode, COUNT(*) n"
        " FROM request_logs WHERE requested_model LIKE ?"
        " GROUP BY d, http_status, error_category, gateway_mode ORDER BY d, n DESC"
    )
    for r in cur.execute(q, (f"%{MODEL}%",)):
        print(f"  {r['d']}  http={r['http_status']}  cat={r['error_category']}  mode={r['gateway_mode']}  n={r['n']}")

    print("\n=== 全库 http_status 分布（同期对照，10-04 当天全部模型）===")
    q2 = (
        "SELECT http_status, error_category, gateway_mode, COUNT(*) n FROM request_logs"
        " WHERE timestamp BETWEEN 1791052800000 AND 1791139199000"
        " GROUP BY http_status, error_category, gateway_mode ORDER BY n DESC LIMIT 12"
    )
    for r in cur.execute(q2):
        print(f"  http={r['http_status']}  cat={r['error_category']}  mode={r['gateway_mode']}  n={r['n']}")

    print("\n=== 该模型 error_message 取值（去重）===")
    for r in cur.execute(
        "SELECT error_message, COUNT(*) n FROM request_logs WHERE requested_model LIKE ?"
        " GROUP BY error_message ORDER BY n DESC LIMIT 6",
        (f"%{MODEL}%",),
    ):
        msg = (r["error_message"] or "(空)")[:300]
        print(f"  n={r['n']:<5} {msg}")

    print("\n=== 该模型 proxy_route_json 取值（去重）===")
    for r in cur.execute(
        "SELECT proxy_route_json, COUNT(*) n FROM request_logs WHERE requested_model LIKE ?"
        " GROUP BY proxy_route_json ORDER BY n DESC LIMIT 5",
        (f"%{MODEL}%",),
    ):
        print(f"  n={r['n']:<5} {(r['proxy_route_json'] or '(空)')[:300]}")

    print("\n=== 10-04 那 31 条的逐条时间线 ===")
    for r in cur.execute(
        "SELECT timestamp, http_status, latency_ms FROM request_logs"
        " WHERE requested_model LIKE ? AND timestamp BETWEEN 1791052800000 AND 1791139199000"
        " ORDER BY timestamp",
        (f"%{MODEL}%",),
    ):
        print(f"  {ts(r['timestamp'])}  http={r['http_status']}  {r['latency_ms']}ms")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
