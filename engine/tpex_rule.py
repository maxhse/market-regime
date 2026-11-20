"""同一套 v4 規則套在櫃買指數，並測試加權＋櫃買的合併方式。參數不調整（沿用 v4）。
2014–2021 樣本內、2022 起樣本外。
持股報酬：A＝1.5×加權報酬指數；B＝櫃買價格指數（未含息，偏保守）；C＝A、B 各半。"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "engine")
from rule import rule
from backtest import equity, stats

tw = pd.read_csv("data/public/twii.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
mk = pd.read_csv("data/public/market.csv", parse_dates=["date"]).set_index("date")
tp = pd.read_csv("data/raw/tpex.csv", parse_dates=["date"]).set_index("date")      # TEJ OHLC（研究用）
idx = tw.index.intersection(mk.index)
tw = tw.loc[idx]
tpc = mk["tpex_close"].reindex(idx).ffill()
tph = tp["high"].reindex(idx).fillna(tpc); tpl = tp["low"].reindex(idx).fillna(tpc)
tr = mk["tr"].reindex(idx).pct_change().fillna(0)
tpr = tpc.pct_change().fillna(0)

Rt = rule(tw.close, tw.high, tw.low)
Ro = rule(tpc, tph, tpl)
Ro_c = rule(tpc, tpc, tpc)               # 只用收盤（公開資料只有收盤時的版本）
et, eo, eoc = Rt.expo / 100, Ro.expo / 100, Ro_c.expo / 100
print("櫃買：用高低點 vs 只用收盤，水位相同的天數比例：", f"{(eo == eoc).mean()*100:.1f}%")

S = {"只看加權（目前網站）": et, "只看櫃買": eo,
     "兩者平均": (et + eo) / 2, "取較低（較保守）": np.minimum(et, eo), "取較高（較積極）": np.maximum(et, eo)}
ma60t = pd.Series(np.where(tw.close > tw.close.rolling(60).mean(), 1.0, .5), index=idx)
ma60o = pd.Series(np.where(tpc > tpc.rolling(60).mean(), 1.0, .5), index=idx)
S["簡單季線（加權）"] = ma60t; S["簡單季線（櫃買）"] = ma60o
port = {"A 1.5×加權報酬": 1.5 * tr, "B 櫃買指數": tpr, "C 各半": 0.75 * tr + 0.5 * tpr}
bench = {"A 1.5×加權報酬": tr, "B 櫃買指數": tpr, "C 各半": 0.5 * tr + 0.5 * tpr}

pd.set_option("display.width", 250, "display.unicode.east_asian_width", True)
for plab, sl in (("樣本內 2014/7–2021", slice("2014-07-01", "2021-12-31")), ("樣本外 2022/1–2026/11", slice("2022-01-01", None))):
    for k, ret in port.items():
        b = bench[k].loc[sl]
        rows = {"對標指數（持有 1 倍）": stats(equity(pd.Series(1.0, index=b.index), b, False), b),
                "持有 100%": stats(equity(pd.Series(1.0, index=b.index), ret.loc[sl]), b)}
        for n, e in S.items():
            rows[n] = stats(equity(e.loc[sl], ret.loc[sl]), b, e.loc[sl])
        print(f"\n=== {plab}｜持股 {k} ===")
        print(pd.DataFrame(rows).T[["年化%", "最大回撤%", "Calmar", "平均水位%", "年調整次數"]].round(2).to_string())

# 兩者不一致的情況有多常見、之後表現
d = pd.DataFrame({"t": et, "o": eo}).dropna()
print("\n兩者水位不同的天數比例：", f"{(d.t != d.o).mean()*100:.1f}%")
f20t = (1 + tr).cumprod(); f20o = (1 + tpr).cumprod()
for name, m in (("加權弱、櫃買強", (d.t < 1) & (d.o == 1)), ("加權強、櫃買弱", (d.t == 1) & (d.o < 1)),
                ("兩者都強", (d.t == 1) & (d.o == 1)), ("兩者都弱", (d.t < 1) & (d.o < 1))):
    i = m[m].index
    rt = (f20t.shift(-20) / f20t - 1).reindex(i).dropna(); ro = (f20o.shift(-20) / f20o - 1).reindex(i).dropna()
    print(f"  {name}：占 {m.mean()*100:4.1f}%  之後 20 日 加權報酬 {rt.mean()*100:+.1f}%（上漲 {(rt>0).mean()*100:.0f}%）  櫃買 {ro.mean()*100:+.1f}%（上漲 {(ro>0).mean()*100:.0f}%）")
print("\n今日：加權水位", et.iloc[-1], "櫃買水位", eo.iloc[-1], "櫃買煞車", bool(Ro.brake.iloc[-1]), "櫃買季線", round(Ro.ma.iloc[-1], 1), "收", tpc.iloc[-1])
