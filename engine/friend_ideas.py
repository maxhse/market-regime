"""外資現貨賣超強度、頭部過熱、融資水位、台幣匯率：事件研究＋疊加在 v5 水位上的效果。
樣本內 2014/7–2021 挑條件，樣本外 2022 起驗證；1 倍加權報酬指數、1 倍櫃買指數；扣現股成本。
事件研究的標準：之後 20 日內「再跌 3%」、「漲 5% 且沒先跌 3%」，另看 20 日平均報酬。"""
import io, contextlib, itertools, sys
import numpy as np, pandas as pd
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("engine/tpex_rule.py").read().split("S = {")[0])
V5 = np.minimum(et, rule(tpc, tpc, tpc).expo / 100)
ch = pd.read_csv("data/market/chip_hist.csv", parse_dates=["date"], na_values=["NA"]).set_index("date").reindex(idx)
fx = pd.read_csv("data/raw/external.csv", parse_dates=["date"]).set_index("date")["usdtwd"].reindex(idx).ffill(limit=3)
fut = pd.read_csv("data/raw/futures.csv", parse_dates=["date"]).set_index("date")["foreign_tx_net_oi"].reindex(idx).ffill(limit=3)
brd = pd.read_csv("data/public/market.csv", parse_dates=["date"]).set_index("date").reindex(idx)
c, to = tw.close, mk["turnover"].reindex(idx)
fs = ch["twse_foreign"] / 1e8
mgt, mgo = ch["twse_margin"] / 1e8, ch["tpex_margin"] / 1e8
IS, OOS = slice("2014-07-01", "2021-12-31"), slice("2022-01-01", None)
pd.set_option("display.width", 260, "display.unicode.east_asian_width", True, "display.max_rows", 400)
rank = lambda s, n=250: s.rolling(n, min_periods=120).apply(lambda a: (a[:-1] < a[-1]).mean() if len(a) > 1 else np.nan, raw=True)

# ---- 指標 ----
fi10 = fs.rolling(10).sum() / to.rolling(10).sum()
fi20 = fs.rolling(20).sum() / to.rolling(20).sum()
fi20_rk = rank(fi20)
oi5 = fut.diff(5)
ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
b20, b60 = c / ma20 - 1, c / ma60 - 1
o20, o60 = tpc / tpc.rolling(20).mean() - 1, tpc / tpc.rolling(60).mean() - 1
mt_rk, mo_rk = rank(mgt), rank(mgo)
mt20, mo20 = mgt.pct_change(20), mgo.pct_change(20)
vol60 = to / to.rolling(60).mean()
hi60 = c >= c.rolling(60).max()
narrow = hi60.rolling(10).max().astype(bool) & (brd["above_ma60"] < 0.55)                 # 指數近 10 日創 60 日新高，但站上季線家數不到 55%
dd20 = c / c.rolling(20).max() - 1
fx60 = fx / fx.rolling(60).mean() - 1
fx20 = fx.pct_change(20)

# 頭部過熱燈（我的版本：價、槓桿、量、廣度各一）
heat = pd.concat({"乖離": (b20 >= 0.05) | (o20 >= 0.06), "季線乖離": (b60 >= 0.10) | (o60 >= 0.12),
                  "融資位階": (mt_rk >= 0.95) | (mo_rk >= 0.95), "融資急增": (mt20 >= 0.08) | (mo20 >= 0.10),
                  "量能": vol60 >= 1.3, "廣度背離": narrow}, axis=1).fillna(False)
heatn = heat.sum(axis=1)

C = {
    # 1 外資現貨
    "外資20日賣超≥成交3%": fi20 <= -0.03, "外資20日賣超≥成交5%": fi20 <= -0.05,
    "外資10日賣超≥成交5%": fi10 <= -0.05, "外資賣超強度近一年最低10%": fi20_rk <= 0.10,
    "外資現貨＋期貨同步看空": (fi20 <= -0.03) & (oi5 < 0),
    "季線上但外資20日賣超≥3%（背離）": (fi20 <= -0.03) & (c > ma60),
    "外資20日買超≥成交3%": fi20 >= 0.03,
    # 2 頭部過熱
    **{f"過熱燈≥{k}盞": heatn >= k for k in (2, 3, 4)},
    "廣度背離": narrow,
    # 3 融資水位
    "回檔5%＋上市融資位階≥90%": (dd20 <= -0.05) & (mt_rk >= 0.90),
    "回檔5%＋上櫃融資位階≥90%": (dd20 <= -0.05) & (mo_rk >= 0.90),
    "上櫃融資位階≥95%": mo_rk >= 0.95,
    "季線下＋上市融資位階≤20%": (c < ma60) & (mt_rk <= 0.20),
    "季線下＋上櫃融資位階≤20%": (tpc < tpc.rolling(60).mean()) & (mo_rk <= 0.20),
    # 5 台幣
    "美元兌台幣≥32": fx >= 32, "台幣20日貶1.5%以上": fx20 >= 0.015, "台幣在60日均線上方1%（偏貶）": fx60 >= 0.01,
    "台幣20日升1.5%以上": fx20 <= -0.015,
}

# ---- 事件研究 ----
eqt, eqo = (1 + tr).cumprod(), (1 + tpr).cumprod()
def fwd(eq):
    path = pd.concat([eq.shift(-k) / eq - 1 for k in range(1, 21)], axis=1)
    dn3 = (path <= -0.03)
    first_dn = dn3.idxmax(axis=1).where(dn3.any(axis=1))
    up5 = path >= 0.05
    first_up = up5.idxmax(axis=1).where(up5.any(axis=1))
    upok = first_up.notna() & (first_dn.isna() | (first_up < first_dn))
    return {"r20": eq.shift(-20) / eq - 1, "dn3": dn3.any(axis=1), "up5": upok}
FT, FO = fwd(eqt), fwd(eqo)
ok = FT["r20"].notna()
def ev(m, sl):
    m = (m.fillna(False) & ok).loc[sl]
    r = {"天數": int(m.sum()), "段數": int((m & ~m.shift(1, fill_value=False)).sum())}
    for nm, F in (("加權", FT), ("櫃買", FO)):
        r[f"{nm}20日%"] = F["r20"].loc[sl][m].mean() * 100
        r[f"{nm}再跌3%"] = F["dn3"].loc[sl][m].mean() * 100
        r[f"{nm}漲5%"] = F["up5"].loc[sl][m].mean() * 100
    return r
rows = {}
for nm, m in [("【全部日子】", pd.Series(True, index=idx))] + list(C.items()):
    for pl, sl in (("內", IS), ("外", OOS)):
        rows[(nm, pl)] = ev(m, sl)
print("=== 事件研究（內＝2014/7–2021、外＝2022 起）===")
print(pd.DataFrame(rows).T.round(0).to_string())

# ---- 疊加在 v5 ----
def sc(e, sl):
    return [stats(equity(e.loc[sl], r.loc[sl]), r.loc[sl], e.loc[sl]) for r in (tr, tpr)]
base = {"IS": sc(V5, IS), "OOS": sc(V5, OOS)}
def hold(m, n):
    m = m.fillna(False)
    return m.rolling(n, min_periods=1).max().astype(bool) if n > 1 else m
res = []
for (nm, m), (act, lv), h in itertools.product(C.items(), [("上限", 30), ("上限", 50), ("上限", 70), ("上限", 80), ("下限", 50), ("下限", 70)], (1, 10)):
    mm = hold(m, h)
    e = V5.where(~mm, np.minimum(V5, lv / 100) if act == "上限" else np.maximum(V5, lv / 100))
    if (e != V5).sum() < 5: continue
    r = {"條件": nm, "動作": f"{act}{lv}%", "維持": h, "影響天數": int((e != V5).sum())}
    for pl, sl in (("IS", IS), ("OOS", OOS)):
        s = sc(e, sl)
        r[f"{pl}ΔCalmar加權"] = s[0]["Calmar"] - base[pl][0]["Calmar"]; r[f"{pl}ΔCalmar櫃買"] = s[1]["Calmar"] - base[pl][1]["Calmar"]
        r[f"{pl}Δ年化加權"] = s[0]["年化%"] - base[pl][0]["年化%"]; r[f"{pl}Δ回撤加權"] = s[0]["最大回撤%"] - base[pl][0]["最大回撤%"]
        r[f"{pl}Δ年化櫃買"] = s[1]["年化%"] - base[pl][1]["年化%"]; r[f"{pl}Δ回撤櫃買"] = s[1]["最大回撤%"] - base[pl][1]["最大回撤%"]
    r["IS平均"] = (r["ISΔCalmar加權"] + r["ISΔCalmar櫃買"]) / 2
    r["OOS平均"] = (r["OOSΔCalmar加權"] + r["OOSΔCalmar櫃買"]) / 2
    r["四格皆不差"] = all(r[k] >= -0.005 for k in ("ISΔCalmar加權", "ISΔCalmar櫃買", "OOSΔCalmar加權", "OOSΔCalmar櫃買"))
    res.append(r)
g = pd.DataFrame(res)
g.to_csv("/tmp/friend_overlay.csv", index=False)
print("\nv5 基準 IS:", [(round(s['年化%'], 1), round(s['最大回撤%'], 1), round(s['Calmar'], 2)) for s in base["IS"]],
      " OOS:", [(round(s['年化%'], 1), round(s['最大回撤%'], 1), round(s['Calmar'], 2)) for s in base["OOS"]])
best = g.sort_values("IS平均", ascending=False).groupby("條件").head(1)
print("\n=== 每個條件在樣本內最好的動作，及其樣本外結果 ===")
print(best[["條件", "動作", "維持", "影響天數", "IS平均", "OOS平均", "ISΔCalmar加權", "ISΔCalmar櫃買", "OOSΔCalmar加權", "OOSΔCalmar櫃買",
            "OOSΔ年化加權", "OOSΔ回撤加權", "OOSΔ年化櫃買", "OOSΔ回撤櫃買", "四格皆不差"]].round(3).to_string(index=False))
print("\n=== 四格（內外 × 加權櫃買）皆不變差、且平均有改善者 ===")
print(g[g["四格皆不差"] & (g["IS平均"] > 0.01) & (g["OOS平均"] > 0.01)].sort_values("IS平均", ascending=False)
      [["條件", "動作", "維持", "影響天數", "IS平均", "OOS平均", "OOSΔ年化加權", "OOSΔ回撤加權", "OOSΔ年化櫃買", "OOSΔ回撤櫃買"]].round(3).to_string(index=False))
print("\n今日：fi20", round(fi20.iloc[-1] * 100, 2), "fi10", round(fi10.iloc[-1] * 100, 2), "過熱燈", int(heatn.iloc[-1]),
      "上市融資位階", round(mt_rk.iloc[-1], 2), "上櫃融資位階", round(mo_rk.iloc[-1], 2), "fx", fx.dropna().iloc[-1])
