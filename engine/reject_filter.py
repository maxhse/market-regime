"""反彈碰月線不過＋籌碼／量能過濾。訓練期 2014–2021 挑條件，2022 起驗證。
外資台指期淨未平倉：data/raw/futures.csv（TEJ，只供研究）；成交金額：data/public/market.csv。
兩者都是當天收盤後就公布，收盤決策可用。"""
import pandas as pd, numpy as np, sys
sys.argv = sys.argv[:1]
exec(open("engine/tx_reject.py").read().split("pd.set_option")[0])   # tx, adj, fwd, c
oi = pd.read_csv("data/raw/futures.csv", parse_dates=["date"]).set_index("date")["foreign_tx_net_oi"]
to = pd.read_csv("data/public/market.csv", parse_dates=["date"]).set_index("date")["turnover"]
tw = pd.read_csv("data/public/twii.csv", parse_dates=["date"]).set_index("date")
tw = tw[~tw.index.duplicated()].sort_index()

def run(src):
    if src == "台指期近月（全盤）":
        C, H, L, R = c, tx.full_high, tx.full_low, adj
    else:
        C, H, L = tw.close, tw.high, tw.low
        R = C
    o = oi.reindex(C.index); v = to.reindex(C.index)
    f = {k: R.shift(-k) / R - 1 for k in (10, 20)}
    lr = L * R / C
    newlow = pd.concat([lr.shift(-j) for j in range(1, 11)], axis=1).min(axis=1) < lr.rolling(20).min()
    ma = C.rolling(20).mean()
    weak = (C < ma) & (ma < ma.shift(5))
    vma = v.rolling(20).mean()
    filt = {"不過濾": pd.Series(True, index=C.index),
            "外資淨空 3 日增加": o.diff(3) < 0,
            "外資淨空 5 日增加": o.diff(5) < 0,
            "外資淨空 5 日增 5000 口以上": o.diff(5) < -5000,
            "量縮（成交金額 < 20 日均量）": v < vma,
            "量縮（< 0.8 倍 20 日均量）": v < 0.8 * vma,
            "反彈量 < 前段下跌量": v.rolling(3).mean() < v.shift(3).rolling(10).mean(),
            "外資淨空 5 日增＋量縮": (o.diff(5) < 0) & (v < vma)}
    out = []
    for k in (1, 2):
        for tol in (0.005, 0.01):
            touch = weak & (H >= ma * (1 - tol))
            run_ = touch.astype(int).groupby((~touch).cumsum()).cumsum()
            ev = run_ == k
            for lab, sl in (("訓練 2014–2021", slice("2014-03-01", "2021-12-31")), ("驗證 2022–", slice("2022-01-01", None))):
                ok = f[20].notna()
                ctrl = (weak & ~touch & ok).loc[sl]
                for fn, m in filt.items():
                    e = (ev & m.fillna(False) & ok).loc[sl]
                    g = lambda x, mm: x.loc[sl][mm]
                    out.append({"資料": src, "連續": k, "容許": f"{tol*100:.1f}%", "期間": lab, "過濾": fn, "次數": int(e.sum()),
                                "10日%": g(f[10], e).mean() * 100, "20日%": g(f[20], e).mean() * 100,
                                "10日跌機率%": (g(f[10], e) < 0).mean() * 100, "破前低%": g(newlow, e).mean() * 100,
                                "對照10日%": g(f[10], ctrl).mean() * 100, "對照跌機率%": (g(f[10], ctrl) < 0).mean() * 100})
    return pd.DataFrame(out)

pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 500)
t = pd.concat([run("台指期近月（全盤）"), run("加權指數")])
t.to_csv("/tmp/reject_filter.csv", index=False)
for (src, k, tol), g in t.groupby(["資料", "連續", "容許"], sort=False):
    w = g.pivot_table(index="過濾", columns="期間", values=["次數", "10日%", "10日跌機率%", "破前低%"], sort=False)
    print(f"\n== {src}｜連 {k} 天｜容許 {tol}　對照（弱勢沒碰到）：訓練 10日 {g[g.期間.str.startswith('訓練')]['對照10日%'].iloc[0]:+.1f}% 跌機率 {g[g.期間.str.startswith('訓練')]['對照跌機率%'].iloc[0]:.0f}%｜驗證 10日 {g[g.期間.str.startswith('驗證')]['對照10日%'].iloc[0]:+.1f}% 跌機率 {g[g.期間.str.startswith('驗證')]['對照跌機率%'].iloc[0]:.0f}%")
    print(w.round(1).to_string())
