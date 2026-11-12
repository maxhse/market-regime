"""
v3：季線＋斜率規則（100/50/30）加上假跌破／假突破的確認條件。現股成本。只用開發期。

進入「季線下」狀態（降倉）的條件，任一成立：
  連續 N 天收在季線下；或跌破季線超過 D 倍 ATR；或跌破當天有量價／籌碼確認（confirm）
回到「季線上」狀態（回補）：連續 N 天收在季線上，或站上季線超過 D 倍 ATR。
confirm：vol＝帶量跌破（成交金額 z>1）；chip＝融資增而指數跌、或外資期貨淨空續增（皆為前一日資料）
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None
sys.argv = sys.argv[:1]
sys.path.insert(0, str(Path(__file__).parent))
import backtest as bt  # noqa: E402

GRID = [dict(N=n, D=d, confirm=c) for n, d, c in itertools.product([1, 2, 3, 5], [None, 0.5, 1.0, 1.5], ["none", "vol", "chip", "both"])
        if not (n == 1 and (d is not None or c != "none"))]


def exposure(f, S, p):
    c, ma, atr = f["close"].values, f["ma60"].values, f["atr"].values
    down = (f["ma60_slope"] < -0.5).values
    vol = (f["vol_z"] > 1).fillna(False).values
    chip = ((S.get("C1", 0) == -1) | (S.get("C4", 0) == -1)).values
    trig = {"none": np.zeros(len(c), bool), "vol": vol, "chip": chip, "both": vol | chip}[p["confirm"]]
    N, D = p["N"], p["D"]
    st, cb, ca, out = False, 0, 0, np.full(len(c), np.nan)
    for i in range(len(c)):
        if np.isnan(ma[i]):
            continue
        b = c[i] < ma[i]
        cb, ca = (cb + 1, 0) if b else (0, ca + 1)
        if not st:
            if cb >= N or (b and D is not None and c[i] < ma[i] - D * atr[i]) or (b and trig[i]):
                st = True
        elif ca >= N or (not b and D is not None and c[i] > ma[i] + D * atr[i]):
            st = False
        out[i] = (0.3 if down[i] else 0.5) if st else 1.0
    return pd.Series(out, index=f.index)


def perf(e, ret, bench):
    return bt.stats(bt.equity(e, ret), bench, e)


def main():
    data, f, S, tr, tp = bt.prepare()
    expos_full = [exposure(f, S, p) for p in GRID]
    dev = slice(*bt.DEV)
    f, tr = f.loc[dev], tr.loc[dev]
    expos = [e.loc[dev] for e in expos_full]
    hb, days = bt.BETA * tr, f.index
    pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 200)
    h1, h2 = slice(None, "2019-12-31"), slice("2020-01-01", None)
    one = pd.Series(1.0, index=days)
    for lab, sl in (("全期", slice(None)), ("前段15-19", h1), ("後段20-24", h2)):
        b = bt.stats(bt.equity(one.loc[sl], tr.loc[sl], False), tr.loc[sl])
        print(f"大盤 {lab}: 年化 {b['年化%']:.1f}% 回撤 {b['最大回撤%']:.1f}%")

    rows = []
    for p, e in zip(GRID, expos):
        a, x, y = perf(e, hb, tr), perf(e.loc[h1], hb.loc[h1], tr.loc[h1]), perf(e.loc[h2], hb.loc[h2], tr.loc[h2])
        cut = (e < 1) & (e.shift(1) == 1)
        nxt = pd.concat([e.shift(-k) for k in range(1, 11)], axis=1).max(axis=1)
        rows.append({**p, "年化": a["年化%"], "回撤": a["最大回撤%"], "前段年化": x["年化%"], "前段回撤": x["最大回撤%"],
                     "後段年化": y["年化%"], "後段回撤": y["最大回撤%"], "年調整": a["年調整次數"],
                     "降倉次數": int(cut.sum()), "假跌破%": (nxt[cut] == 1).mean() * 100})
    g = pd.DataFrame(rows)
    print("\n=== 全格點（1.5×加權，現股成本）==="); print(g.round(1).to_string(index=False))

    # 滾動：每 6 個月用過去資料挑（回撤不深於大盤者中年化最高）
    oos, picks = pd.Series(index=days, dtype=float), []
    for st in pd.date_range("2020-01-01", "2024-07-01", freq="6MS"):
        test = days[(days >= st) & (days < st + pd.DateOffset(months=6))]
        sl = slice(days[0], days[days < st][-21])
        b = bt.stats(bt.equity(one.loc[sl], tr.loc[sl], False), tr.loc[sl])
        df = pd.DataFrame([perf(e.loc[sl], hb.loc[sl], tr.loc[sl]) for e in expos])
        ok = df[df["最大回撤%"] >= b["最大回撤%"]]
        k = int(ok["年化%"].idxmax()) if len(ok) else int(df["Calmar"].idxmax())
        oos.loc[test] = expos[k].loc[test]; picks.append(tuple(GRID[k].values()))
    o = oos.dropna(); osl = slice(o.index[0], o.index[-1])
    print("\n各段選到：", picks)
    res = {"大盤": bt.stats(bt.equity(one.loc[osl], tr.loc[osl], False), tr.loc[osl]),
           "基準 100/50/30（N=1）": perf(expos[0].loc[osl], hb.loc[osl], tr.loc[osl]),
           "v3 滾動": perf(o, hb.loc[osl], tr.loc[osl])}
    print("\n=== 樣本外 2020/1–2024/12 ==="); print(pd.DataFrame(res).T.round(2))
    if OUT_DIR:
        OUT_DIR.mkdir(parents=True, exist_ok=True); g.to_csv(OUT_DIR / "v3_grid.csv", index=False)


if __name__ == "__main__":
    main()
