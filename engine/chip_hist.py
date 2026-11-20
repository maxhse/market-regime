"""
籌碼歷史回補（2014 年起），供每季檢驗用公開資料重跑回測。

沿用 chip/sources.py 的解析邏輯（與每日籌碼儀錶板一致），另存一份精簡的歷史：
  data/market/chip_hist.csv   date, twse_foreign, twse_trust, twse_dealer, tpex_foreign, tpex_trust, tpex_dealer,
                              twse_margin, tpex_margin, tx_foreign_oi（金額單位：元）
不寫 data/daily/（那是儀錶板用的，避免儀錶板一次載入 13 年資料）。

  python engine/chip_hist.py [分鐘數]     由新到舊回補，跑到時間上限就停，可重複執行、自動接續
"""
from __future__ import annotations

import csv
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chip"))
sys.path.insert(0, str(ROOT / "engine"))
import sources as S  # noqa: E402
from common import num, post_bytes, session  # noqa: E402
from market import TPE, trading_days  # noqa: E402

OUT = ROOT / "data" / "market" / "chip_hist.csv"
FIELDS = ["date", "twse_foreign", "twse_trust", "twse_dealer", "tpex_foreign", "tpex_trust", "tpex_dealer",
          "twse_margin", "tpex_margin", "tx_foreign_oi"]
GAP = {"twse": 3.2, "tpex": 2.2}
FIRST = date(2014, 1, 1)


def load() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    with open(OUT, encoding="utf-8") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def save(rows: dict[str, dict]):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({x: rows[k].get(x, "") for x in FIELDS})


def paced(site, fn, *a):
    t = time.time()
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        return e
    finally:
        rest = GAP[site] - (time.time() - t)
        if rest > 0:
            time.sleep(rest)


def twse_part(s, d):
    out, errs = {}, []
    r = paced("twse", S.twse_institutional, s, d)
    if isinstance(r, Exception):
        errs.append(f"twse_insti:{str(r)[:40]}")
        out.update(twse_foreign="NA", twse_trust="NA", twse_dealer="NA")
    else:
        out.update(twse_foreign=r["foreign"], twse_trust=r["trust"], twse_dealer=r["dealer"])
    r = paced("twse", S.twse_margin, s, d)
    if isinstance(r, Exception):
        errs.append(f"twse_margin:{str(r)[:40]}")
        out["twse_margin"] = "NA"
    else:
        out["twse_margin"] = r["balance"]
    return out, errs


def tpex_part(s, d):
    out, errs = {}, []
    r = paced("tpex", S.tpex_institutional, s, d)
    if isinstance(r, Exception):
        errs.append(f"tpex_insti:{str(r)[:40]}")
        out.update(tpex_foreign="NA", tpex_trust="NA", tpex_dealer="NA")
    else:
        out.update(tpex_foreign=r["foreign"], tpex_trust=r["trust"], tpex_dealer=r["dealer"])
    r = paced("tpex", S.tpex_margin, s, d)
    if isinstance(r, Exception):
        errs.append(f"tpex_margin:{str(r)[:40]}")
        out["tpex_margin"] = "NA"
    else:
        out["tpex_margin"] = r["balance"]
    return out, errs


def taifex_range(s, a: date, b: date) -> dict[str, float]:
    """外資台指期（TXF）未平倉多空淨額口數，一次查一段日期。"""
    raw = post_bytes(s, "https://www.taifex.com.tw/cht/3/futContractsDateDown",
                     {"queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}", "commodityId": "TXF"})
    text = raw.decode("big5", errors="replace")
    out = {}
    for r in csv.reader(io.StringIO(text)):
        if len(r) >= 15 and "外資" in str(r[2]).replace(" ", "") and r[0][:1].isdigit():
            ds = datetime.strptime(r[0].strip(), "%Y/%m/%d").strftime("%Y-%m-%d")
            out[ds] = num(r[13], 0) or 0
    return out


def main(minutes: float):
    t0 = time.time()
    rows = load()
    s1, s2, s3 = session(), session(), session()
    today = datetime.now(TPE).date()
    days = [f"{d:%Y-%m-%d}" for d in trading_days(f"{FIRST:%Y-%m-%d}") if d < today]

    # 期貨：一次 30 天
    need = [d for d in days if not rows.get(d, {}).get("tx_foreign_oi")]
    if need:
        end = datetime.strptime(max(need), "%Y-%m-%d").date()
        while end >= FIRST:
            a = max(FIRST, end - timedelta(days=29))
            try:
                for ds, v in taifex_range(s3, a, end).items():
                    rows.setdefault(ds, {"date": ds})["tx_foreign_oi"] = v
            except Exception as e:  # noqa: BLE001
                print(f"期貨 {a}~{end} 失敗：{str(e)[:80]}")
            time.sleep(2)
            end = a - timedelta(days=1)
        save(rows)

    # 法人、融資：上市與上櫃同時抓
    n, fails = 0, 0
    with ThreadPoolExecutor(2) as ex:
        for ds in reversed(days):
            if time.time() - t0 > minutes * 60:
                break
            r = rows.get(ds, {})
            if all(r.get(k) not in (None, "") for k in FIELDS[1:9]):
                continue
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            (a, ea), (b, eb) = ex.submit(twse_part, s1, d).result(), ex.submit(tpex_part, s2, d).result()
            rec = rows.setdefault(ds, {"date": ds})
            rec.update(a); rec.update(b)
            n += 1
            if ea or eb:
                fails += 1
                print(ds, " ".join(ea + eb))
            if n % 50 == 0:
                save(rows)
    save(rows)
    left = sum(1 for ds in days if not all(rows.get(ds, {}).get(k) not in (None, "") for k in FIELDS[1:9]))
    print(f"本次回補 {n} 天（其中 {fails} 天有欄位失敗），尚缺 {left} 天，用時 {(time.time() - t0) / 60:.0f} 分鐘")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 40)
