"""台指期（TX）日線歷史，含夜盤。來源：期交所「期貨每日交易行情下載」futDataDown（2017/5 起有夜盤）。

  data/market/tx_raw.csv    date, contract, session(day/night), open, high, low, close, volume, oi, settle
    只留月契約（不含週契約），每天最近的兩個月份。
    夜盤列的 date 是它所屬的交易日：前一個交易日 15:00 開盤、當日 05:00 收盤。
  python engine/tx_hist.py [起始 YYYY-MM]      逐月下載，已有的月份會跳過（最近兩個月每次重抓）
"""
from __future__ import annotations

import csv, io, sys, time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chip"))
from common import post_bytes, session  # noqa: E402

OUT = ROOT / "data" / "market" / "tx_raw.csv"
F = ["date", "contract", "session", "open", "high", "low", "close", "volume", "oi", "settle"]


def num(x):
    x = str(x).strip().replace(",", "")
    try:
        return float(x)
    except ValueError:
        return ""


def month(s, y, m):
    a = date(y, m, 1)
    b = date(y + (m == 12), m % 12 + 1, 1)
    raw = post_bytes(s, "https://www.taifex.com.tw/cht/3/futDataDown",
                     {"down_type": "1", "commodity_id": "TX", "queryStartDate": f"{a:%Y/%m/%d}",
                      "queryEndDate": f"{date.fromordinal(b.toordinal() - 1):%Y/%m/%d}"})
    rows = []
    for r in csv.reader(io.StringIO(raw.decode("big5", errors="replace"))):
        if len(r) < 18 or not r[0][:1].isdigit() or r[1].strip() != "TX":
            continue
        c = r[2].strip()
        if not (len(c) == 6 and c.isdigit()):          # 跳過週契約、價差
            continue
        rows.append({"date": datetime.strptime(r[0].strip(), "%Y/%m/%d").strftime("%Y-%m-%d"), "contract": c,
                     "session": "night" if "盤後" in r[17] else "day", "open": num(r[3]), "high": num(r[4]),
                     "low": num(r[5]), "close": num(r[6]), "volume": num(r[9]), "settle": num(r[10]), "oi": num(r[11])})
    # 每天只留最近兩個月份
    keep = {}
    for r in rows:
        keep.setdefault(r["date"], set()).add(r["contract"])
    keep = {d: sorted(v)[:2] for d, v in keep.items()}
    return [r for r in rows if r["contract"] in keep[r["date"]]]


def main(start="2010-01"):
    old = []
    if OUT.exists():
        with open(OUT, encoding="utf-8") as f:
            old = list(csv.DictReader(f))
    have = {r["date"][:7] for r in old}
    today = date.today()
    recent = {f"{today:%Y-%m}", f"{date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1):%Y-%m}"}
    y, m = map(int, start.split("-"))
    s, new, empty = session(), {}, []
    while (y, m) <= (today.year, today.month):
        ym = f"{y}-{m:02d}"
        if ym not in have or ym in recent:
            try:
                got = month(s, y, m)
                (new.__setitem__(ym, got) if got else empty.append(ym))
            except Exception as e:  # noqa: BLE001
                print(ym, "失敗", str(e)[:80])
            time.sleep(2)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    rows = [r for r in old if r["date"][:7] not in new] + [r for v in new.values() for r in v]
    rows.sort(key=lambda r: (r["date"], r["contract"], r["session"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=F); w.writeheader(); w.writerows(rows)
    print(f"更新 {len(new)} 個月；無資料的月份 {len(empty)} 個：{empty[:3]}…{empty[-3:]}；共 {len(rows)} 列，"
          f"{rows[0]['date'] if rows else '-'} ~ {rows[-1]['date'] if rows else '-'}")


if __name__ == "__main__":
    main(*sys.argv[1:])
