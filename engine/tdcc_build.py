"""集保大戶持股 → docs/chip/data/tdcc.json（參考資料，不影響水位）。"""
from __future__ import annotations
import csv, gzip, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR, DAY = ROOT / "data" / "market" / "tdcc", ROOT / "data" / "market" / "day"
OUT = ROOT / "docs" / "chip" / "data" / "tdcc.json"
TOP_N = 300


def load():
    fr = []
    for p in sorted(DIR.glob("*.csv")):
        d = pd.read_csv(p, dtype={"code": str}); d["w"] = p.stem; fr.append(d)
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def price_on(dates):
    """每個週日期（YYYYMMDD）對應當天或之前最近一個交易日的收盤。"""
    files = {p.name[:10]: p for p in DAY.glob("*/*.json.gz")}
    keys = sorted(files)
    out = {}
    for w in dates:
        ds = f"{w[:4]}-{w[4:6]}-{w[6:]}"
        k = max([x for x in keys if x <= ds], default=None)
        if not k: continue
        d = json.load(gzip.open(files[k])); px = {}
        for mk in ("twse", "tpex"):
            px.update({c: v for c, v in (d.get(mk, {}).get("close") or {}).items() if v})
        out[w] = px
    return out


def main():
    df = load()
    if df.empty:
        print("沒有集保資料"); return
    weeks = sorted(df.w.unique())
    px = price_on(weeks)
    df["px"] = [px.get(w, {}).get(c) for w, c in zip(df.w, df.code)]
    df["cap"] = df.shares * df.px / 1e8
    last = weeks[-1]
    L = df[df.w == last].dropna(subset=["cap"]).sort_values("cap", ascending=False)
    top = list(L.code[:TOP_N])
    names = json.loads((ROOT / "data" / "market" / "names.json").read_text(encoding="utf-8")) if (ROOT / "data" / "market" / "names.json").exists() else {}

    # 市場層級（固定用最新一週的市值前 300 大，避免成分變動造成跳動）
    T = df[df.code.isin(top)].copy()
    piv = {k: T.pivot(index="w", columns="code", values=k) for k in ("p1000", "p400", "p50", "holders", "cap")}
    mk = []
    for i, w in enumerate(weeks):
        row = {"w": w}
        cap = piv["cap"].loc[w] if w in piv["cap"].index else None
        if cap is None or cap.notna().sum() < 50:
            continue
        for k in ("p1000", "p400", "p50"):
            v = piv[k].loc[w]; m = v.notna() & cap.notna()
            row[k] = round(float((v[m] * cap[m]).sum() / cap[m].sum()), 2)
        row["n"] = int(cap.notna().sum())
        if i > 0 and weeks[i - 1] in piv["p1000"].index:
            a, b = piv["p1000"].loc[weeks[i - 1]], piv["p1000"].loc[w]; m = a.notna() & b.notna()
            row["up_pct"] = round(float((b[m] > a[m] + 0.05).mean() * 100), 1)
            row["dn_pct"] = round(float((b[m] < a[m] - 0.05).mean() * 100), 1)
            ha, hb = piv["holders"].loc[weeks[i - 1]], piv["holders"].loc[w]; m = ha.notna() & hb.notna()
            row["holders_chg"] = round(float(hb[m].sum() / ha[m].sum() - 1) * 100, 2)
        mk.append(row)

    # 個股
    def chg(code, k, n):
        h = piv[k][code].dropna() if code in piv[k] else pd.Series(dtype=float)
        return round(float(h.iloc[-1] - h.iloc[-1 - n]), 2) if len(h) > n else None
    def hchg(code, n):
        h = piv["holders"][code].dropna() if code in piv["holders"] else pd.Series(dtype=float)
        return round(float(h.iloc[-1] / h.iloc[-1 - n] - 1) * 100, 2) if len(h) > n else None
    def pchg(code, n):
        if len(weeks) <= n: return None
        a, b = px.get(weeks[-1 - n], {}).get(code), px.get(weeks[-1], {}).get(code)
        return round((b / a - 1) * 100, 1) if a and b else None
    stocks = []
    for c in top:
        r = L[L.code == c].iloc[0]
        d1, d4, d12 = chg(c, "p1000", 1), chg(c, "p1000", 4), chg(c, "p1000", 12)
        h4 = hchg(c, 4)
        tag = None
        if d4 is not None and h4 is not None:
            if d4 >= 1 and h4 <= -1: tag = "集中"
            elif d4 <= -1 and h4 >= 1: tag = "渙散"
        p4 = pchg(c, 4)
        if tag == "集中" and p4 is not None and p4 < 0: tag = "逆勢吸籌"
        if tag == "渙散" and p4 is not None and p4 > 0: tag = "拉高出貨"
        hist = [[w, *(None if pd.isna(piv[k].at[w, c]) else float(piv[k].at[w, c]) for k in ("p1000", "p400", "p50", "holders"))]
                for w in weeks if c in piv["p1000"] and w in piv["p1000"].index]
        nm = names.get(c, ["", ""])
        stocks.append({"code": c, "name": nm[0], "mkt": nm[1], "cap": round(float(r.cap)), "px": r.px,
                       "p1000": float(r.p1000), "p400": float(r.p400), "p50": float(r.p50), "holders": int(r.holders),
                       "d1": d1, "d4": d4, "d12": d12, "h4": h4, "px4": p4, "tag": tag, "hist": hist})
    tz = timezone(timedelta(hours=8))
    out = {"built_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M"), "latest_week": last, "weeks": weeks, "top_n": TOP_N,
           "market": mk, "stocks": stocks}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"tdcc.json：{len(weeks)} 週、{len(stocks)} 檔、市場序列 {len(mk)} 點，{OUT.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
