"""集保戶股權分散表（神秘金字塔同源資料）：大戶持股參考，不參與水位。

  python engine/tdcc.py weekly           抓集保開放資料（最新一週，全部股票）
  python engine/tdcc.py backfill [分鐘]   用集保個股查詢回補近一年（市值前 N 大），可重複執行、自動接續
  python engine/tdcc.py probe 2330        測試個股查詢
  python engine/tdcc.py build            產出 docs/chip/data/tdcc.json

儲存：data/market/tdcc/YYYYMMDD.csv，每檔一列：
  code, holders（總人數）, shares（集保總股數）, p1000（千張以上持股%）, p400（400 張以上%）,
  p50（50 張以下%，散戶）, n1000（千張大戶人數）, n400（400 張以上人數）
持股分級：1=1–999 股、2=1–5 張、…、8=40–50 張、9=50–100、10=100–200、11=200–400、12=400–600、13=600–800、
          14=800–1000、15=1000 張以上、16=差異數調整、17=合計
"""
from __future__ import annotations

import csv, gzip, io, json, re, sys, time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "market" / "tdcc"
DAY = ROOT / "data" / "market" / "day"
OUT = ROOT / "docs" / "chip" / "data" / "tdcc.json"
OD = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
QRY = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FIELDS = ["code", "holders", "shares", "p1000", "p400", "p50", "n1000", "n400"]
TOP_N = 300


def summarize(levels: dict[int, tuple[float, float, float]]) -> dict | None:
    """levels: 分級 → (人數, 股數, 比例%)。"""
    tot = levels.get(17)
    if not tot or not tot[1]:
        return None
    sh = tot[1]
    pct = lambda ks: round(sum(levels.get(k, (0, 0, 0))[1] for k in ks) / sh * 100, 2)
    return {"holders": int(tot[0]), "shares": int(tot[1]), "p1000": pct([15]), "p400": pct([12, 13, 14, 15]),
            "p50": pct(range(1, 9)), "n1000": int(levels.get(15, (0, 0, 0))[0]),
            "n400": int(sum(levels.get(k, (0, 0, 0))[0] for k in (12, 13, 14, 15)))}


def save_week(date: str, recs: dict[str, dict]):
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{date}.csv"
    old = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            old = {r["code"]: r for r in csv.DictReader(f)}
    old.update({k: {"code": k, **v} for k, v in recs.items()})
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for k in sorted(old):
            w.writerow({x: old[k].get(x, "") for x in FIELDS})


def is_stock(code: str) -> bool:
    return len(code) == 4 and code.isdigit() and code[0] != "0"


def weekly():
    r = requests.get(OD, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig", errors="replace")
    by: dict[str, dict[int, tuple]] = {}
    date = None
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or not row[0].strip().isdigit():
            continue
        date, code = row[0].strip(), row[1].strip()
        if not is_stock(code):
            continue
        by.setdefault(code, {})[int(row[2])] = (float(row[3] or 0), float(row[4] or 0), float(row[5] or 0))
    recs = {c: s for c, lv in by.items() if (s := summarize(lv))}
    save_week(date, recs)
    print(f"集保 {date}：{len(recs)} 檔")
    names()


def names():
    """股票名稱與市場別（證交所、櫃買 OpenAPI）。"""
    out = {}
    try:
        for r in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers={"User-Agent": UA}, timeout=60).json():
            if is_stock(r.get("Code", "")): out[r["Code"]] = [r["Name"].strip(), "上市"]
    except Exception as e:  # noqa: BLE001
        print("上市名稱失敗", str(e)[:60])
    try:
        for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers={"User-Agent": UA}, timeout=60).json():
            c = r.get("SecuritiesCompanyCode", "")
            if is_stock(c): out[c] = [r.get("CompanyName", "").strip(), "上櫃"]
    except Exception as e:  # noqa: BLE001
        print("上櫃名稱失敗", str(e)[:60])
    if out:
        p = DIR.parent / "names.json"
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        old.update(out); p.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")


# ---------- 個股查詢回補 ----------
def _num(x):
    x = re.sub(r"<[^>]+>", "", x).replace(",", "").strip()
    try:
        return float(x)
    except ValueError:
        return None


def qry_session():
    s = requests.Session(); s.headers.update({"User-Agent": UA, "Referer": QRY})
    html = s.get(QRY, timeout=60).text
    tok = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', html)
    dates = re.findall(r'<option value="(\d{8})"', html)
    return s, (tok.group(1) if tok else ""), sorted(set(dates))


def qry(s, tok, code, date):
    html = s.post(QRY, data={"SYNCHRONIZER_TOKEN": tok, "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock", "method": "submit",
                             "firDate": date, "scaDate": date, "sqlMethod": "StockNo", "stockNo": code, "stockName": ""}, timeout=60).text
    nt = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', html)
    levels = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        td = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(td) != 5:
            continue
        lab = re.sub(r"<[^>]+>|\s|　", "", td[0] + td[1])
        k = int(_num(td[0])) if _num(td[0]) is not None else (17 if "合計" in lab else 16 if "差異" in lab else None)
        if k is not None:
            levels[k] = (_num(td[2]) or 0, _num(td[3]) or 0, _num(td[4]) or 0)
    return levels, (nt.group(1) if nt else tok)


def latest_prices() -> dict[str, float]:
    files = sorted(DAY.glob("*/*.json.gz"))
    out = {}
    for p in files[-5:]:
        d = json.load(gzip.open(p))
        for mk in ("twse", "tpex"):
            for k, v in (d.get(mk, {}).get("close") or {}).items():
                if v:
                    out[k] = v
    return out


def top_codes(n=TOP_N) -> list[str]:
    weeks = sorted(DIR.glob("*.csv"))
    if not weeks:
        weekly(); weeks = sorted(DIR.glob("*.csv"))
    px = latest_prices()
    with open(weeks[-1], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cap = sorted(((float(r["shares"]) * px.get(r["code"], 0), r["code"]) for r in rows), reverse=True)
    return [c for _, c in cap[:n]]


def backfill(minutes=40.0):
    t0 = time.time()
    codes = top_codes()
    s, tok, dates = qry_session()
    have = {}
    for p in DIR.glob("*.csv"):
        with open(p, encoding="utf-8") as f:
            have[p.stem] = {r["code"] for r in csv.DictReader(f)}
    todo = [(d, c) for d in reversed(dates) for c in codes if c not in have.get(d, set())]
    print(f"查詢日期 {len(dates)} 週（{dates[0] if dates else '-'}～{dates[-1] if dates else '-'}），待補 {len(todo)} 筆")
    buf, n, fail = {}, 0, 0
    for d, c in todo:
        if time.time() - t0 > minutes * 60:
            break
        try:
            lv, tok = qry(s, tok, c, d)
            sm = summarize(lv)
            if sm:
                buf.setdefault(d, {})[c] = sm
            else:
                fail += 1
        except Exception as e:  # noqa: BLE001
            fail += 1; print(d, c, str(e)[:60])
            time.sleep(5); s, tok, _ = qry_session()
        n += 1
        if n % 200 == 0:
            for dd, rr in buf.items(): save_week(dd, rr)
            buf = {}; print(f"  已查 {n}／{len(todo)}，失敗 {fail}")
        time.sleep(0.6)
    for dd, rr in buf.items(): save_week(dd, rr)
    print(f"本次查 {n} 筆，失敗 {fail}，尚餘 {len(todo) - n} 筆，{(time.time() - t0) / 60:.0f} 分鐘")


def probe(code="2330"):
    s, tok, dates = qry_session()
    print("token", tok[:12], "dates", len(dates), dates[:2], dates[-2:])
    for i, (c, d) in enumerate([(code, dates[-1]), ("2317", dates[-2]), ("2454", dates[-3]), ("3017", dates[-4]), (code, dates[0])]):
        t0 = time.time()
        lv, nt = qry(s, tok, c, d)
        print(i, c, d, "levels", len(lv), "has17", 17 in lv, "newtok", nt[:12], "same", nt == tok, f"{time.time()-t0:.1f}s", summarize(lv))
        tok = nt
        time.sleep(0.6)
    # 同一個 token 不更新再查一次
    lv, _ = qry(s, tok, "2303", dates[-5]); print("reuse", len(lv))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if cmd == "weekly": weekly()
    elif cmd == "backfill": backfill(float(sys.argv[2]) if len(sys.argv) > 2 else 40)
    elif cmd == "probe": probe(*(sys.argv[2:3]))
    elif cmd == "names": names()
    elif cmd == "build":
        sys.path.insert(0, str(Path(__file__).parent)); import tdcc_build; tdcc_build.main()
