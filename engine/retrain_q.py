"""水位規則：2014–2021 訓練，2022 起每季用「當時以前」的資料重新挑參數（擴張視窗、隔 20 日），和定版 v4 比較。
只用公開資料：data/public/twii.csv（高低收）、market.csv 的 tr（加權報酬指數）。持股以 1.5×加權報酬、現股成本計。"""
import sys, itertools
import numpy as np, pandas as pd
sys.path.insert(0, "engine")
import rule as R
from backtest import equity, stats, BETA

tw = pd.read_csv("data/public/twii.csv", parse_dates=["date"]).set_index("date")
tw = tw[~tw.index.duplicated()].sort_index()
mk = pd.read_csv("data/public/market.csv", parse_dates=["date"]).set_index("date")
tw = tw.loc[tw.index.intersection(mk.index)]
tr = mk["tr"].reindex(tw.index).pct_change().fillna(0)
C, H, L = tw.close, tw.high, tw.low
hb = BETA * tr
START = "2014-07-01"   # 120 日線暖機後


def state_of(ma_len, n):
    ma = C.rolling(ma_len).mean()
    slope = (ma - ma.shift(R.SLOPE_DAYS)) / R.atr(H, L, C)
    c, m = C.values, ma.values
    st, cb, ca, out = False, 0, 0, np.zeros(len(c), bool)
    for i in range(len(c)):
        if np.isnan(m[i]): continue
        b = c[i] < m[i]
        cb, ca = (cb + 1, 0) if b else (0, ca + 1)
        if not st and cb >= n: st = True
        elif st and ca >= n: st = False
        out[i] = st
    return out, (slope < R.SLOPE_CUT).values


def brake_of(drop):
    if drop is None: return np.zeros(len(C), bool)
    ma20, dd = C.rolling(20).mean(), C / C.rolling(20).max() - 1
    on, br = False, np.zeros(len(C), bool)
    for i in range(len(C)):
        if not on and dd.iat[i] <= -drop: on = True
        elif on and C.iat[i] > ma20.iat[i]: on = False
        br[i] = on
    return br


MAS, NS, DROPS = (40, 60, 80, 120), (1, 2, 3, 5), (None, 0.08, 0.10, 0.12)
LV = [(b, d) for b in (30, 50, 70) for d in (0, 30, 50) if d <= b]
ST = {(m, n): state_of(m, n) for m in MAS for n in NS}
BR = {d: brake_of(d) for d in DROPS}
GRID, EXPO = [], []
for m, n, (b, d), dr in itertools.product(MAS, NS, LV, DROPS):
    st, down = ST[(m, n)]
    e = np.where(st & down, d, np.where(st, b, 100)).astype(float)
    e = np.where(BR[dr], np.minimum(e, 30), e) / 100
    GRID.append(dict(ma=m, n=n, below=b, below_down=d, brake=dr)); EXPO.append(pd.Series(e, index=C.index))
k_v4 = GRID.index(dict(ma=60, n=3, below=50, below_down=30, brake=0.10))
ma60 = pd.Series(np.where(C > C.rolling(60).mean(), 1.0, 0.5), index=C.index)


def pick(sl, obj):
    b = stats(equity(pd.Series(1.0, index=tr.loc[sl].index), tr.loc[sl], False), tr.loc[sl])
    df = pd.DataFrame([stats(equity(e.loc[sl], hb.loc[sl]), tr.loc[sl], e.loc[sl]) for e in EXPO])
    if obj == "calmar":
        return int(df["Calmar"].idxmax())
    ok = df[df["最大回撤%"] >= b["最大回撤%"]]
    return int(ok["年化%"].idxmax()) if len(ok) else int(df["Calmar"].idxmax())


days = C.loc[START:].index
TEST0 = "2022-01-01"
res, curves, picklog = {}, {}, {}
for obj, olab in (("cagr", "回撤不輸大盤下年化最高"), ("calmar", "Calmar 最高")):
    k0 = pick(slice(START, "2021-12-31"), obj)
    print(f"[{olab}] 2014–2021 一次訓練選到：{GRID[k0]}")
    for freq, flab in (("QS", "每季"), ("YS", "每年")):
        for win, wlab in ((None, "累積視窗"), (5, "近 5 年")):
            oos = pd.Series(index=days, dtype=float); log = []
            for q in pd.date_range(TEST0, days[-1], freq=freq):
                test = days[(days >= q) & (days < q + pd.DateOffset(months=3 if freq == "QS" else 12))]
                if not len(test): continue
                st = START if win is None else max(pd.Timestamp(START), q - pd.DateOffset(years=win))
                k = pick(slice(st, days[days < q][-21]), obj)
                oos.loc[test] = EXPO[k].loc[test]; log.append((f"{q:%Y}Q{(q.month-1)//3+1}", tuple(GRID[k].values())))
            lab = f"{flab}重新優化｜{wlab}｜{olab}"
            picklog[lab] = log; res[lab] = oos.dropna()
    res[f"2014–2021 優化一次不再改｜{olab}"] = EXPO[k0].loc[res[lab].index]
idx = res[next(iter(res))].index
sl = slice(idx[0], idx[-1])
one = pd.Series(1.0, index=idx)
rows = {"加權報酬指數（對標）": stats(equity(one, tr.loc[sl], False), tr.loc[sl]),
        "1.5× 持有 100%": stats(equity(one, hb.loc[sl]), tr.loc[sl], one),
        "季線 100/50（最簡單）": stats(equity(ma60.loc[sl], hb.loc[sl]), tr.loc[sl], ma60.loc[sl]),
        "定版 v4（目前網站）": stats(equity(EXPO[k_v4].loc[sl], hb.loc[sl]), tr.loc[sl], EXPO[k_v4].loc[sl])}
series = {"定版 v4（目前網站）": EXPO[k_v4].loc[sl]}
for k, e in res.items():
    rows[k] = stats(equity(e, hb.loc[sl]), tr.loc[sl], e); series[k] = e
pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 100)
print(f"\n=== 驗證期 {idx[0]:%Y-%m-%d} ~ {idx[-1]:%Y-%m-%d} ===")
print(pd.DataFrame(rows).T.round(2).to_string())
yr = pd.DataFrame({"大盤": tr.loc[sl], **{k: equity(e, hb.loc[sl]).pct_change().fillna(0) for k, e in series.items()}})
print("\n逐年報酬%：\n", ((1 + yr).groupby(yr.index.year).prod() - 1).mul(100).round(1).T.to_string())
dd = {k: (equity(e, hb.loc[sl]).groupby(lambda d: d.year).apply(lambda s: (s / s.cummax() - 1).min()) * 100) for k, e in series.items()}
dd["大盤"] = equity(one, tr.loc[sl], False).groupby(lambda d: d.year).apply(lambda s: (s / s.cummax() - 1).min()) * 100
print("\n逐年最大回撤%：\n", pd.DataFrame(dd).T.round(1).to_string())
for k, v in [x for x in picklog.items() if "每年" in x[0]]:
    print(f"\n[{k}] 每季選到（季線長度, 確認天數, 季線下水位, 下彎水位, 煞車）：")
    for q, p in v: print("  ", q, p)
