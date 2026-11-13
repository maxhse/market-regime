"""回補歷史資料。

用法：
    python scripts/backfill.py 40                     一般回補，已有資料的欄位會跳過
    python scripts/backfill.py 40 --force              強制全部重抓，不管有沒有舊資料
    python scripts/backfill.py 40 --force twse_insti tpex_insti
                                                        只強制重抓指定的欄位（省時間）

修正計算邏輯之後要用 --force，不然已經存在的舊資料（用錯的邏輯算出來的）
不會被覆蓋——這是這支腳本原本的設計：正常情況下已有資料就跳過，避免浪費 API 請求，
但邏輯本身改了的時候，「已經有資料」不代表「資料是對的」。
"""
from __future__ import annotations

import sys
import time
from datetime import timedelta

import sources as S
from common import (daily_path, iso, load_json, save_json, session, today_tpe)

JOBS = [
    ("twse_insti",  S.twse_institutional),
    ("tpex_insti",  S.tpex_institutional),
    ("twse_margin", S.twse_margin),
    ("tpex_margin", S.tpex_margin),
    ("taifex_tx",   S.taifex_tx_foreign),
    ("flows",       S.stock_flows),
]


def main() -> None:
    args = sys.argv[1:]
    back = int(args[0]) if args and args[0].lstrip("-").isdigit() else 40
    rest = args[1:] if args and args[0].lstrip("-").isdigit() else args
    force = "--force" in rest
    only = [a for a in rest if not a.startswith("--")] or None

    s = session()

    d = today_tpe()
    for _ in range(back):
        if d.weekday() < 5:
            key = iso(d)
            rec = load_json(daily_path(key), {}) or {}
            rec["date"] = key
            got = []
            for field, fn in JOBS:
                if only and field not in only:
                    continue
                if rec.get(field) and not force:
                    got.append(f"{field}=skip")
                    continue
                try:
                    rec[field] = fn(s, d)
                    got.append(field + ("*" if force else ""))
                except Exception as e:  # noqa: BLE001
                    got.append(f"{field}!({str(e)[:40]})")
            if any(rec.get(f) for f, _ in JOBS):
                save_json(daily_path(key), rec)
            print(f"{key}: {' '.join(got)}")
            time.sleep(1)
        d -= timedelta(days=1)


if __name__ == "__main__":
    main()
