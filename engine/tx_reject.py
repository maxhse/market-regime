"""台指期近月（含夜盤）版本的「反彈碰均線不過」事件研究。

近月連續：每天取有日盤成交的最近月份契約（結算日當天仍用到期契約，隔天換月），不做價差調整，與一般看盤軟體的「近月」K 線一致。
全盤 K 線：夜盤（前一晚 15:00 起）＋日盤；開盤＝夜盤開盤，收盤＝日盤收盤。2017/5/15 前沒有夜盤，只用日盤。
訊號用未調整的近月 K 線（你在圖上看到的）；之後的報酬用「換月時去除價差」的連續報酬，避免換月跳空被算成漲跌。
"""
import sys
import pandas as pd

raw = pd.read_csv("data/market/tx_raw.csv", dtype={"contract": str})
raw = raw[raw["date"] < pd.Timestamp.today().strftime("%Y-%m-%d")]          # 今天盤中的不算
day = raw[raw.session == "day"].dropna(subset=["close"])
near = day.sort_values(["date", "contract"]).groupby("date").first()
night = raw[raw.session == "night"].set_index(["date", "contract"])
rows = []
for d, r in near.iterrows():
    n = night.loc[(d, r.contract)] if (d, r.contract) in night.index else None
    hasn = n is not None and pd.notna(n["open"])
    rows.append({"date": pd.Timestamp(d), "contract": r.contract, "close": r.close,
                 "day_high": r.high, "day_low": r.low,
                 "full_high": max(r.high, n["high"]) if hasn else r.high,
                 "full_low": min(r.low, n["low"]) if hasn else r.low})
tx = pd.DataFrame(rows).set_index("date")
# 換月：前一日的新近月契約收盤，用來去除價差
prev_close_same = {}
for d, r in raw[raw.session == "day"].iterrows():
    pass
dd = day.set_index(["date", "contract"])["close"]
ret = []
dates = tx.index
for i, d in enumerate(dates):
    if i == 0:
        ret.append(0.0); continue
    p = dates[i - 1].strftime("%Y-%m-%d")
    k = (p, tx.contract.iloc[i])
    base = dd.get(k, tx.close.iloc[i - 1])
    ret.append(tx.close.iloc[i] / base - 1)
tx["ret"] = ret
adj = (1 + tx["ret"]).cumprod()
c = tx["close"]
fwd = {k: adj.shift(-k) / adj - 1 for k in (5, 10, 20)}


def study(H, L, n, k, tol, sl):
    ma = c.rolling(n).mean()
    weak = (c < ma) & (ma < ma.shift(5))
    touch = weak & (H >= ma * (1 - tol))
    run = touch.astype(int).groupby((~touch).cumsum()).cumsum()
    ev = run == k
    # 10 日內跌破前低：用調整後價格比較
    ladj = L * adj / c
    newlow = pd.concat([ladj.shift(-j) for j in range(1, 11)], axis=1).min(axis=1) < ladj.rolling(20).min()
    fmin = pd.concat([ladj.shift(-j) / adj - 1 for j in range(1, 11)], axis=1).min(axis=1)
    def s(m):
        m = (m & fwd[20].notna()).loc[sl]
        g = lambda x: x.loc[sl][m]
        return {"次數": int(m.sum()), "5日%": g(fwd[5]).mean() * 100, "10日%": g(fwd[10]).mean() * 100,
                "20日%": g(fwd[20]).mean() * 100, "10日跌機率%": (g(fwd[10]) < 0).mean() * 100,
                "破前低%": g(newlow).mean() * 100, "最深%": g(fmin).mean() * 100}
    return s(ev), s(weak & ~touch), ev


pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 300)
periods = [("開發期 2015/6–2024/12", slice("2015-06-01", "2024-12-31")), ("有夜盤後 2017/6–2026", slice("2017-06-01", None))]
for kind, H, L in (("全盤（含夜盤）", tx.full_high, tx.full_low), ("只看日盤", tx.day_high, tx.day_low)):
    for lab, sl in periods:
        rows = {}
        for n in (10, 20, 40, 60):
            for k in (1, 2, 3):
                for tol in (0.0, 0.005, 0.01):
                    e, ctl, _ = study(H, L, n, k, tol, sl)
                    rows[(f"{n}日線", k, f"{tol*100:.1f}%")] = {**{f"事件{x}": v for x, v in e.items()},
                                                              **{f"對照{x}": v for x, v in ctl.items() if x in ("10日%", "10日跌機率%", "破前低%")}}
        t = pd.DataFrame(rows).T
        t.index.names = ["均線", "連續", "容許"]
        print(f"\n===== 台指期近月 {kind}｜{lab} =====")
        print(t.round(1).to_string())

print("\n2026 年 20 日線訊號（全盤）：")
for k, tol in ((1, 0.0), (2, 0.0), (1, 0.005), (2, 0.005), (2, 0.01)):
    _, _, ev = study(tx.full_high, tx.full_low, 20, k, tol, slice("2026-01-01", None))
    print(f"  連 {k} 天 容許 {tol*100:.1f}%：", [f"{d:%m/%d}" for d in ev[ev].index])
ma = c.rolling(20).mean()
x = pd.DataFrame({"契約": tx.contract, "收": c, "全盤高": tx.full_high, "日盤高": tx.day_high, "月線": ma, "月線5日前": ma.shift(5)})
print(x.loc["2026-07-15":"2026-07-31"].round(0).to_string()); print(x.loc["2026-11-05":].round(0).to_string())
