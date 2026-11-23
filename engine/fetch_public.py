"""抓公開的指數日線 → data/public/：加權指數（^TWII，高低收）、費半（^SOX）、美元兌台幣（期交所參考匯率）。先試 Yahoo，失敗改用 Stooq。在 GitHub Actions 上執行。"""
import csv, io, json, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

PUB = Path(__file__).resolve().parents[1] / "data" / "public"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode()


def yahoo(sym, tz_hours):
    j = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=1388534400&period2={int(time.time())}&interval=1d"))
    r = j["chart"]["result"][0]; q = r["indicators"]["quote"][0]
    tz = timezone(timedelta(hours=tz_hours))
    return [(datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d"), o, h, l, c)
            for t, o, h, l, c in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"]) if c and h and l]


def stooq(sym):
    rows = csv.DictReader(io.StringIO(get(f"https://stooq.com/q/d/l/?s={sym}&i=d")))
    return [(r["Date"], float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])) for r in rows if r["Date"] >= "2014-01-01"]


def fetch(name, sources, cols):
    for label, fn in sources:
        try:
            rows = fn()
            assert len(rows) > 2500, f"only {len(rows)} rows"
            PUB.mkdir(parents=True, exist_ok=True)
            with open(PUB / f"{name}.csv", "w", newline="") as f:
                w = csv.writer(f); w.writerow(["date"] + cols)
                for d, o, h, l, c in rows:
                    w.writerow([d] + ([round(o or c, 2), round(h, 2), round(l, 2), round(c, 2)] if len(cols) == 4 else [round(c, 2)]))
            print(f"{name} ← {label}: {len(rows)} rows {rows[0][0]} ~ {rows[-1][0]}  last close {rows[-1][4]:.2f}")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"{name} ← {label} failed: {e}")
    return False


def twse_topup():
    """用證交所官方的當月、上月日線補上 Yahoo 還沒更新的交易日。失敗不影響其餘流程。"""
    path = PUB / "twii.csv"
    rows = {r[0]: r for r in csv.reader(open(path)) if r and r[0] != "date"}
    now = datetime.now(timezone(timedelta(hours=8)))
    added = 0
    for m in (now.replace(day=1) - timedelta(days=1), now):
        try:
            j = json.loads(get(f"https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={m:%Y%m}01&response=json"))
            for d, o, h, l, c in j.get("data", []):
                y, mo, da = d.strip().split("/")
                key = f"{int(y) + 1911}-{mo}-{da}"
                added += key not in rows
                rows[key] = [key] + [round(float(x.replace(",", "")), 2) for x in (o, h, l, c)]
        except Exception as e:  # noqa: BLE001
            print(f"twse {m:%Y-%m} failed: {e}")
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "open", "high", "low", "close"]); w.writerows(rows[k] for k in sorted(rows))
    print(f"twii ← twse 官方補 {added} 天，最新 {max(rows)}")


def taifex_fx():
    """期交所每日外幣參考匯率（美元／新台幣），2014 起。第一次全抓，之後只補最近 40 天。"""
    sys.path.insert(0, str(PUB.parents[1] / "chip"))
    from common import post_bytes, session  # 與期貨資料相同的連線方式（含重試）
    ses = session()
    path = PUB / "fx.csv"
    rows = {}
    if path.exists():
        rows = {r[0]: r[1] for r in csv.reader(open(path)) if r and r[0] != "date"}
    start = (datetime.strptime(max(rows), "%Y-%m-%d") - timedelta(days=40)) if len(rows) > 2500 else datetime(2014, 1, 1)
    end = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    a, n = start, 0
    while a <= end:
        b = min(a + timedelta(days=30), end)
        raw = post_bytes(ses, "https://www.taifex.com.tw/cht/3/dailyFXRateDown",
                         {"queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}"})
        for r in csv.reader(io.StringIO(raw.decode("big5", errors="replace"))):
            if len(r) > 1 and r[0][:2] in ("19", "20") and "/" in r[0]:
                try:
                    v = float(r[1])
                except ValueError:
                    continue
                rows[datetime.strptime(r[0].strip(), "%Y/%m/%d").strftime("%Y-%m-%d")] = v; n += 1
        a = b + timedelta(days=1)
        time.sleep(1.5)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "usdtwd"]); w.writerows([k, rows[k]] for k in sorted(rows))
    print(f"fx ← 期交所：本次 {n} 筆，共 {len(rows)} 筆，最新 {max(rows)} {rows[max(rows)]}")


ok1 = fetch("twii", [("yahoo", lambda: yahoo("%5ETWII", 8)), ("stooq", lambda: stooq("^twse"))], ["open", "high", "low", "close"])
ok2 = fetch("sox", [("yahoo", lambda: yahoo("%5ESOX", -5)), ("stooq", lambda: stooq("^sox"))], ["sox"])
try:
    taifex_fx()
except Exception as e:  # noqa: BLE001
    print("匯率抓取失敗，沿用既有資料：", e)
if ok1:
    twse_topup()
sys.exit(0 if ok1 else 1)
