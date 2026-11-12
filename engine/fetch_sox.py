"""抓費半指數（^SOX）日收盤 → data/public/sox.csv。先試 Yahoo，失敗改用 Stooq。在 GitHub Actions 上執行。"""
import csv, io, json, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "public" / "sox.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode()


def yahoo():
    j = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?period1=1388534400&period2={int(time.time())}&interval=1d"))
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), round(c, 2)) for t, c in zip(r["timestamp"], q) if c]


def stooq():
    rows = list(csv.DictReader(io.StringIO(get("https://stooq.com/q/d/l/?s=^sox&i=d"))))
    return [(r["Date"], float(r["Close"])) for r in rows if r["Date"] >= "2014-01-01"]


for name, fn in (("yahoo", yahoo), ("stooq", stooq)):
    try:
        rows = fn()
        assert len(rows) > 2500, f"only {len(rows)} rows"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["date", "sox"]); w.writerows(rows)
        print(f"{name}: {len(rows)} rows {rows[0][0]} ~ {rows[-1][0]}")
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"{name} failed: {e}")
sys.exit(1)
