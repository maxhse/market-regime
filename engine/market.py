"""
公開市場資料：抓取（每日／歷史回補）與彙整。全部來自證交所、櫃買中心、期交所的公開資料。

  python engine/market.py daily                 抓最近幾個交易日缺的資料，再彙整
  python engine/market.py backfill [分鐘數]      由新到舊回補，跑到時間上限就停（可重複執行、會接續）
  python engine/market.py build                 只彙整

原始資料（只增不改，每天一個小檔，避免 repo 膨脹）：
  data/market/day/YYYY/YYYY-MM-DD.json.gz   當日：上市＋上櫃個股收盤、官方漲跌家數、報酬指數、櫃買指數與市值、成交金額
  data/market/exright.csv                   上市＋上櫃除權息（除權息前收盤價、參考價）→ 還原因子
  data/market/pcratio.csv                   期交所 P/C ratio
彙整結果（網站與回測用）：
  data/public/market.csv                    每日：成交金額、報酬指數、櫃買指數、市值、漲跌家數、漲跌停、站上 60 日線比例、新高新低家數
  data/public/tsmc.csv                      台積電收盤（原始價）
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "data" / "market"
DAY = MKT / "day"
PUB = ROOT / "data" / "public"
TPE = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE   # 證交所憑證鏈在部分環境驗證失敗；只讀公開資料
GAP = {"twse": 3.2, "tpex": 2.2, "taifex": 2.0}   # 同一網站兩次請求的最短間隔（秒），避免被封鎖
_last: dict[str, float] = {}


class Blocked(Exception):
    """疑似被暫時封鎖：停止本次執行，下次再接續。"""


# ---------------------------------------------------------------- HTTP
def fetch(site: str, url: str, params: dict | None = None, data: dict | None = None, tries: int = 3) -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for i in range(tries):
        wait = GAP[site] - (time.time() - _last.get(site, 0))
        if wait > 0:
            time.sleep(wait)
        _last[site] = time.time()
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                         headers={"User-Agent": UA, "Accept": "application/json, text/csv, */*"})
            with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
                body = r.read()
            if site != "taifex" and body.lstrip()[:1] == b"<":   # HTML 而非 JSON：多半是封鎖頁
                raise Blocked(f"{site} 回傳 HTML：{body[:120]!r}")
            return body
        except Blocked:
            if i == tries - 1:
                raise
            time.sleep(60 * (i + 1))
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))
    raise RuntimeError("unreachable")


def jget(site, url, params=None):
    return json.loads(fetch(site, url, params))


# ---------------------------------------------------------------- 解析工具
def num(v):
    t = str(v).replace(",", "").replace(" ", "").strip()
    if t in ("", "-", "--", "---", "----", "N/A", "除息", "除權", "除權息"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def roc(d: str) -> str:
    """'115/11/17'、'115年11月17日'、'2026/11/17' → '2026-11-17'"""
    p = [int(x) for x in re.findall(r"\d+", d)]
    y = p[0] + 1911 if p[0] < 1911 else p[0]
    return f"{y:04d}-{p[1]:02d}-{p[2]:02d}"


def is_stock(code: str) -> bool:
    """普通股：4 碼數字、不以 00 開頭（排除 ETF、權證、債券）。"""
    return len(code) == 4 and code.isdigit() and not code.startswith("00")


def counts(cell: str):
    """'2,159(21)' → (2159, 21)；'402' → (402, None)"""
    m = re.match(r"\s*([\d,]+)\s*(?:\(([\d,]+)\))?", str(cell))
    return (int(m.group(1).replace(",", "")), int(m.group(2).replace(",", "")) if m.group(2) else None) if m else (None, None)


# ---------------------------------------------------------------- 單日抓取
def twse_day(d: date) -> dict | None:
    j = jget("twse", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
             {"response": "json", "type": "ALLBUT0999", "date": f"{d:%Y%m%d}"})
    if j.get("stat") != "OK":
        return None
    out = {"close": {}}
    for t in j.get("tables") or []:
        title, fields, rows = t.get("title") or "", t.get("fields") or [], t.get("data") or []
        if title.startswith("報酬指數(臺灣證券交易所)"):
            for r in rows:
                if r[0].strip() == "發行量加權股價報酬指數":
                    out["tr"] = num(r[1])
        elif "價格指數(臺灣證券交易所)" in title:
            for r in rows:
                if r[0].strip() == "發行量加權股價指數":
                    out["taiex"] = num(r[1])
        elif "大盤統計資訊" in title:
            for r in rows:
                if r[0].strip().startswith("總計"):
                    out["value_all"] = num(r[1])
        elif title.startswith("漲跌證券數合計"):
            k = fields.index("股票") if "股票" in fields else 2
            for r in rows:
                a, b = counts(r[k])
                if r[0].startswith("上漲"):
                    out["up"], out["limit_up"] = a, b
                elif r[0].startswith("下跌"):
                    out["down"], out["limit_down"] = a, b
                elif r[0].startswith("持平"):
                    out["flat"] = a
        elif "每日收盤行情" in title:
            ci, cc = fields.index("證券代號"), fields.index("收盤價")
            for r in rows:
                c = str(r[ci]).strip()
                if is_stock(c):
                    out["close"][c] = num(r[cc])
    return out if out["close"] else None


def tpex_day(d: date) -> dict | None:
    j = jget("tpex", "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc",
             {"date": f"{d:%Y/%m/%d}", "type": "EW", "response": "json"})
    tables = j.get("tables") or []
    if not tables or not tables[0].get("data"):
        return None
    fields = ["".join(str(f).replace("<br>", "").split()) for f in tables[0]["fields"]]
    ci, cc = fields.index("代號"), fields.index("收盤")
    out = {"close": {str(r[ci]).strip(): num(r[cc]) for r in tables[0]["data"] if is_stock(str(r[ci]).strip())}}
    h = jget("tpex", "https://www.tpex.org.tw/www/zh-tw/afterTrading/highlight",
             {"date": f"{d:%Y/%m/%d}", "response": "json"})
    t = (h.get("tables") or [{}])[0]
    if t.get("data"):
        f, r = t["fields"], t["data"][0]
        g = lambda name: num(r[f.index(name)]) if name in f else None  # noqa: E731
        out.update({"index": g("收市指數"), "mktcap": g("總市值(佰萬元)"), "value": g("本日總成交值(佰萬元)"),
                    "up": g("上漲家數"), "limit_up": g("漲停家數"), "down": g("下跌家數"),
                    "limit_down": g("跌停家數"), "flat": g("平盤家數")})
    return out


def day_path(ds: str) -> Path:
    return DAY / ds[:4] / f"{ds}.json.gz"


def save_day(ds: str, rec: dict):
    p = day_path(ds)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_day(ds: str) -> dict | None:
    p = day_path(ds)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def get_day(d: date) -> bool:
    from concurrent.futures import ThreadPoolExecutor
    ds = f"{d:%Y-%m-%d}"
    with ThreadPoolExecutor(2) as ex:
        ftw, ftp = ex.submit(twse_day, d), ex.submit(tpex_day, d)
        tw = ftw.result()
        try:
            tp = ftp.result()
        except Exception:  # noqa: BLE001
            tp = None
    if tw is None:
        return False
    save_day(ds, {"date": ds, "twse": tw, "tpex": tp or {}})
    return True


# ---------------------------------------------------------------- 月／年資料
def fmtqik_month(d: date) -> dict[str, float]:
    j = jget("twse", "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK", {"response": "json", "date": f"{d:%Y%m}01"})
    if j.get("stat") != "OK":
        return {}
    k = j["fields"].index("成交金額")
    return {roc(r[0]): num(r[k]) for r in j.get("data") or []}


def tpex_index_month(d: date) -> dict[str, float]:
    j = jget("tpex", "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx", {"date": f"{d:%Y/%m}/01", "response": "json"})
    t = (j.get("tables") or [{}])[0]
    if not t.get("data"):
        return {}
    k = t["fields"].index("收市")
    return {roc(r[0]): num(r[k]) for r in t["data"]}


def exrights(start: date, end: date) -> list[dict]:
    rows = []
    j = jget("twse", "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
             {"response": "json", "startDate": f"{start:%Y%m%d}", "endDate": f"{end:%Y%m%d}"})
    if j.get("stat") == "OK":
        f = j["fields"]
        for r in j.get("data") or []:
            c = str(r[f.index("股票代號")]).strip()
            if is_stock(c):
                rows.append({"date": roc(r[f.index("資料日期")]), "code": c,
                             "before": num(r[f.index("除權息前收盤價")]), "ref": num(r[f.index("除權息參考價")])})
    j = jget("tpex", "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ",
             {"startDate": f"{start:%Y/%m/%d}", "endDate": f"{end:%Y/%m/%d}", "response": "json"})
    t = (j.get("tables") or [{}])[0]
    if t.get("data"):
        f = ["".join(str(x).split()) for x in t["fields"]]
        kb = next(i for i, x in enumerate(f) if x.startswith("除權息前收盤價"))
        kr = next(i for i, x in enumerate(f) if x.startswith("除權息參考價"))
        for r in t["data"]:
            c = str(r[f.index("代號")]).strip()
            if is_stock(c):
                rows.append({"date": roc(r[f.index("除權息日期")]), "code": c, "before": num(r[kb]), "ref": num(r[kr])})
    return rows


def pcratio(start: date, end: date) -> list[dict]:
    body = fetch("taifex", "https://www.taifex.com.tw/cht/3/pcRatioDown",
                 data={"queryStartDate": f"{start:%Y/%m/%d}", "queryEndDate": f"{end:%Y/%m/%d}"})
    text = body.decode("cp950", errors="replace")
    if text.lstrip().startswith("<"):
        return []
    rows = list(csv.reader(io.StringIO(text)))
    h = [x.strip() for x in rows[0]]
    out = []
    for r in rows[1:]:
        if r and r[0].strip():
            out.append({"date": roc(r[0]), "pc_oi": num(r[h.index("買賣權未平倉量比率%")]),
                        "pc_vol": num(r[h.index("買賣權成交量比率%")])})
    return out


# ---------------------------------------------------------------- 小型 CSV 存取
def read_csv(p: Path, key=("date",)) -> dict:
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return {tuple(r[k] for k in key): r for r in csv.DictReader(f)}


def write_csv(p: Path, rows: dict, fields: list[str]):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({x: rows[k].get(x, "") for x in fields})


def merge_monthly(name: str, fn, months: list[date], field: str):
    p = MKT / f"{name}.csv"   # 回補用 name="xxx_hist"，與每日更新的檔案分開
    rows = read_csv(p)
    for m in months:
        for ds, v in fn(m).items():
            rows[(ds,)] = {"date": ds, field: v}
    write_csv(p, rows, ["date", field])


# ---------------------------------------------------------------- 流程
CLOSED = MKT / "closed.txt"   # 證交所查無資料的日子（例如颱風休市，但 Yahoo 日線仍列出）


def closed_days() -> set[str]:
    return set(CLOSED.read_text().split()) if CLOSED.exists() else set()


def mark_closed(ds: str):
    CLOSED.parent.mkdir(parents=True, exist_ok=True)
    CLOSED.write_text("\n".join(sorted(closed_days() | {ds})) + "\n")


def trading_days(since: str) -> list[date]:
    """以加權指數日線（data/public/twii.csv）當作交易日曆，加上今天（若為平日），扣掉已知休市日。"""
    days = []
    shut = closed_days()
    with open(PUB / "twii.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] >= since and r["date"] not in shut:
                days.append(datetime.strptime(r["date"], "%Y-%m-%d").date())
    today = datetime.now(TPE).date()
    if today.weekday() < 5 and (not days or days[-1] < today):
        days.append(today)
    return days


def months_between(a: date, b: date) -> list[date]:
    out, m = [], date(a.year, a.month, 1)
    while m <= b:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def daily():
    today = datetime.now(TPE).date()
    for d in trading_days(f"{today - timedelta(days=20):%Y-%m-%d}"):
        if not day_path(f"{d:%Y-%m-%d}").exists():
            try:
                ok = get_day(d)
                print(d, "OK" if ok else "無資料")
            except Exception as e:  # noqa: BLE001
                print(d, "失敗", repr(e)[:200])
    ms = months_between(today - timedelta(days=35), today)
    for name, fn, field in (("turnover", fmtqik_month, "turnover"), ("tpex_index", tpex_index_month, "tpex_index")):
        try:
            merge_monthly(name, fn, ms, field)
        except Exception as e:  # noqa: BLE001
            print(name, "失敗", repr(e)[:200])
    try:
        p = MKT / "exright.csv"
        rows = read_csv(p, ("date", "code"))
        for r in exrights(today - timedelta(days=20), today):
            rows[(r["date"], r["code"])] = r
        write_csv(p, rows, ["date", "code", "before", "ref"])
    except Exception as e:  # noqa: BLE001
        print("除權息 失敗", repr(e)[:200])
    try:
        p = MKT / "pcratio.csv"
        rows = read_csv(p)
        for r in pcratio(today - timedelta(days=20), today):
            rows[(r["date"],)] = r
        write_csv(p, rows, ["date", "pc_oi", "pc_vol"])
    except Exception as e:  # noqa: BLE001
        print("P/C ratio 失敗", repr(e)[:200])


def backfill(minutes: float):
    """由新到舊回補。月、年資料一次補完；個股日資料跑到時間上限為止。"""
    t0 = time.time()
    first = date(2014, 1, 1)
    today = datetime.now(TPE).date()
    done = MKT / "backfill_done.json"
    flags = json.loads(done.read_text()) if done.exists() else {}
    try:
        if not flags.get("monthly"):
            ms = months_between(first, today)[::-1]
            merge_monthly("turnover_hist", fmtqik_month, ms, "turnover")
            merge_monthly("tpex_index_hist", tpex_index_month, ms, "tpex_index")
            flags["monthly"] = True
        if not flags.get("exright"):
            p = MKT / "exright_hist.csv"
            rows = read_csv(p, ("date", "code"))
            for y in range(today.year, first.year - 1, -1):
                for r in exrights(date(y, 1, 1), min(date(y, 12, 31), today)):
                    rows[(r["date"], r["code"])] = r
            write_csv(p, rows, ["date", "code", "before", "ref"])
            flags["exright"] = True
        if not flags.get("pcratio"):
            p = MKT / "pcratio_hist.csv"
            rows = read_csv(p)
            s = today
            while s > first:
                a = max(first, s - timedelta(days=29))
                for r in pcratio(a, s):
                    rows[(r["date"],)] = r
                s = a - timedelta(days=1)
            write_csv(p, rows, ["date", "pc_oi", "pc_vol"])
            flags["pcratio"] = True
    finally:
        done.write_text(json.dumps(flags))
    n = 0
    recent = f"{today - timedelta(days=15):%Y-%m-%d}"   # 最近 15 天交給每日更新
    for d in reversed(trading_days(f"{first:%Y-%m-%d}")):
        if time.time() - t0 > minutes * 60:
            break
        if f"{d:%Y-%m-%d}" >= recent or day_path(f"{d:%Y-%m-%d}").exists():
            continue
        try:
            if get_day(d):
                n += 1
            else:
                mark_closed(f"{d:%Y-%m-%d}")
                print(d, "證交所查無資料，記為休市")
        except Blocked as e:
            print("疑似被封鎖，停止本次回補：", e)
            break
        except Exception as e:  # noqa: BLE001
            print(d, "失敗", repr(e)[:200])
    left = sum(1 for d in trading_days(f"{first:%Y-%m-%d}") if f"{d:%Y-%m-%d}" < recent and not day_path(f"{d:%Y-%m-%d}").exists())
    print(f"本次回補 {n} 天，尚缺 {left} 天，用時 {(time.time() - t0) / 60:.0f} 分鐘")


# ---------------------------------------------------------------- 彙整
def build():
    import numpy as np
    import pandas as pd

    files = sorted(DAY.glob("*/*.json.gz"))
    if not files:
        print("尚無個股日資料")
        return
    recs, closes = [], {}
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            r = json.load(f)
        tw, tp = r.get("twse") or {}, r.get("tpex") or {}
        recs.append({"date": r["date"], "tr": tw.get("tr"),
                     "twse_up": tw.get("up"), "twse_down": tw.get("down"), "twse_lu": tw.get("limit_up"), "twse_ld": tw.get("limit_down"),
                     "tpex_up": tp.get("up"), "tpex_down": tp.get("down"), "tpex_lu": tp.get("limit_up"), "tpex_ld": tp.get("limit_down"),
                     "tpex_close": tp.get("index"), "tpex_mktcap": tp.get("mktcap")})
        closes[r["date"]] = {**(tp.get("close") or {}), **(tw.get("close") or {})}
    m = pd.DataFrame(recs).set_index("date").sort_index()
    px = pd.DataFrame.from_dict(closes, orient="index").sort_index().astype(float)
    px.index = pd.to_datetime(px.index)
    m.index = pd.to_datetime(m.index)

    # 還原權息（向後調整）：除權息日之前的價格乘上 參考價／除權息前收盤價
    def both(name, **kw):
        fs = [MKT / f"{name}.csv", MKT / f"{name}_hist.csv"]
        fs = [pd.read_csv(f, **kw) for f in fs if f.exists()]
        return pd.concat(fs) if fs else None

    adj = px.copy()
    e = both("exright", dtype={"code": str}, parse_dates=["date"])
    if e is not None:
        e = e.drop_duplicates(["date", "code"]).dropna(subset=["before", "ref"])
        e = e[(e["before"] > 0) & (e["ref"] > 0) & e["code"].isin(px.columns)]
        e["f"] = (e["ref"] / e["before"]).clip(0.05, 1.5)
        fac = pd.DataFrame(1.0, index=px.index, columns=px.columns)
        for (code, d), f in e.groupby(["code", "date"])["f"].prod().items():
            fac.loc[fac.index < d, code] *= f
        adj = px * fac
    ma60 = adj.rolling(60, min_periods=40).mean()
    hi, lo = adj.rolling(250, min_periods=120).max(), adj.rolling(250, min_periods=120).min()
    valid = ma60.notna()
    m["n"] = px.notna().sum(axis=1)
    m["above_ma60"] = ((adj > ma60) & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)
    m["new_high"] = ((adj >= hi) & hi.notna()).sum(axis=1)
    m["new_low"] = ((adj <= lo) & lo.notna()).sum(axis=1)
    m["up"] = m["twse_up"].fillna(0) + m["tpex_up"].fillna(0)
    m["down"] = m["twse_down"].fillna(0) + m["tpex_down"].fillna(0)
    m["limit_up"] = m["twse_lu"].fillna(0) + m["tpex_lu"].fillna(0)
    m["limit_down"] = m["twse_ld"].fillna(0) + m["tpex_ld"].fillna(0)
    for name, field in (("turnover", "turnover"), ("tpex_index", "tpex_index")):
        t_ = both(name, parse_dates=["date"])
        if t_ is not None:
            s_ = t_.drop_duplicates("date", keep="first").set_index("date")[field]
            m[field] = s_.reindex(m.index)
    m["turnover"] = m["turnover"] / 1e8 if "turnover" in m else np.nan   # 元 → 億
    if "tpex_index" in m:
        m["tpex_close"] = m["tpex_close"].fillna(m["tpex_index"])
    pc = both("pcratio", parse_dates=["date"])
    if pc is not None:
        m["pc_ratio"] = pc.drop_duplicates("date").set_index("date")["pc_oi"].reindex(m.index) / 100
    cols = ["n", "up", "down", "limit_up", "limit_down", "above_ma60", "new_high", "new_low",
            "turnover", "tr", "tpex_close", "tpex_mktcap", "pc_ratio"]
    m[[c for c in cols if c in m]].round(4).to_csv(PUB / "market.csv", index_label="date", date_format="%Y-%m-%d")
    if "2330" in px:
        px[["2330"]].rename(columns={"2330": "close"}).dropna().to_csv(PUB / "tsmc.csv", index_label="date", date_format="%Y-%m-%d")
    print(f"彙整完成：{len(m)} 個交易日，{px.shape[1]} 檔個股，最新 {m.index[-1].date()}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if cmd == "daily":
        daily(); build()
    elif cmd == "backfill":
        backfill(float(sys.argv[2]) if len(sys.argv) > 2 else 50)
    else:
        build()
