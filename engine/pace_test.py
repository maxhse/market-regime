"""補充指標（漲跌速度、成交量、融資 vs 指數）：事件研究＋是否能改善 v5 水位。只用公開資料。
樣本內 2014/7–2021 挑條件，樣本外 2022 起驗證。評估：1 倍加權報酬指數與 1 倍櫃買指數兩個市場，扣現股成本。"""
import io, contextlib, itertools
import numpy as np, pandas as pd
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("engine/tpex_rule.py").read().split("S = {")[0])
eoc_ = rule(tpc, tpc, tpc).expo / 100
V5 = np.minimum(et, eoc_)
ch = pd.read_csv("data/market/chip_hist.csv", parse_dates=["date"], na_values=["NA"]).set_index("date")
mg = (ch["twse_margin"] / 1e8).reindex(idx).ffill(limit=2)
to = mk["turnover"].reindex(idx)
c = tw.close
IS, OOS = slice("2014-07-01", "2021-12-31"), slice("2022-01-01", None)
pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 300)

# ---- 標籤（和網站相同定義）----
hi, lo = c.rolling(20).max(), c.rolling(20).min()
ds = lambda s, f: s.rolling(20).apply(lambda a: len(a) - 1 - (a.argmax() if f == "max" else a.argmin()), raw=True)
dhi, dlo = ds(c, "max"), ds(c, "min")
drop, rise, r5 = c / hi - 1, c / lo - 1, c.pct_change(5)
speed = pd.Series(np.select([(r5 <= -.05) | ((drop <= -.08) & (dhi <= 10) & (dhi >= dlo)), (r5 >= .05) | ((rise >= .08) & (dlo <= 10) & (dlo >= dhi)),
                             (dhi > dlo) & (drop <= -.03), (dlo > dhi) & (rise >= .03)], ["急跌", "急漲", "緩跌", "緩漲"], "盤整"), index=idx)
vr = to / to.shift(1).rolling(20).mean()
vol = pd.Series(np.select([vr >= 1.5, vr >= 1.15, vr <= .6, vr <= .85], ["爆量", "量增", "窒息量", "量縮"], "量平"), index=idx)
pv = pd.Series(np.where(c.pct_change() > 0, "價漲", "價跌"), index=idx) + vol
n = pd.Series(np.where(speed.isin(["急跌", "緩跌"]), dhi, np.where(speed.isin(["急漲", "緩漲"]), dlo, 10)), index=idx).fillna(10).clip(lower=3).astype(int)
p = np.arange(len(idx)); st = np.maximum(p - n.values, 0)
mchg = pd.Series(mg.values / mg.values[st] - 1, index=idx); ichg = pd.Series(c.values / c.values[st] - 1, index=idx)
dn = ichg < 0
mlab = pd.Series(np.select([dn & (mchg <= ichg * 1.5), dn & (mchg > ichg * .5), dn, ~dn & (mchg >= ichg * 1.5) & (mchg > .01), ~dn & (mchg < 0), ~dn],
                           ["融資清洗", "融資未退", "融資同步減", "融資追價", "籌碼沉澱", "融資同步增"], ""), index=idx).where(mg.notna())

# ---- 1) 事件研究 ----
eqt, eqo = (1 + tr).cumprod(), (1 + tpr).cumprod()
F = {}
for nm, eq, L in (("加權", eqt, tw.low), ("櫃買", eqo, tpc)):
    F[nm] = {"10日%": eq.shift(-10) / eq - 1, "20日%": eq.shift(-20) / eq - 1,
             "10日最深%": pd.concat([eq.shift(-k) / eq - 1 for k in range(1, 11)], axis=1).min(axis=1)}
lowp = tw.low.rolling(20).min()
brk = pd.concat([tw.low.shift(-k) for k in range(1, 11)], axis=1).min(axis=1) < lowp
def ev(mask, sl):
    m = (mask.fillna(False) & F["加權"]["20日%"].notna()).loc[sl]
    r = {"天數": int(m.sum())}
    for nm in ("加權", "櫃買"):
        r[f"{nm}20日%"] = F[nm]["20日%"].loc[sl][m].mean() * 100
        r[f"{nm}20日上漲%"] = (F[nm]["20日%"].loc[sl][m] > 0).mean() * 100
    r["加權10日最深%"] = F["加權"]["10日最深%"].loc[sl][m].mean() * 100
    r["10日破前低%"] = brk.loc[sl][m].mean() * 100
    return r
for title, lab in (("漲跌速度", speed), ("價量", pv), ("融資 vs 指數", mlab)):
    rows = {}
    for v in ["全部日子"] + sorted(lab.dropna().unique()):
        if v == "": continue
        mask = pd.Series(True, index=idx) if v == "全部日子" else (lab == v)
        for plab, sl in (("樣本內", IS), ("樣本外", OOS)):
            rows[(v, plab)] = ev(mask, sl)
    print(f"\n=== 事件研究：{title} ===")
    print(pd.DataFrame(rows).T.round(1).to_string())

# ---- 2) 疊加在 v5 水位上 ----
def score(e, sl):
    out = []
    for r in (tr, tpr):
        s = stats(equity(e.loc[sl], r.loc[sl]), r.loc[sl], e.loc[sl]); out.append(s)
    return out
CONDS = {
    "緩跌": speed == "緩跌", "急跌": speed == "急跌", "急漲": speed == "急漲", "緩漲": speed == "緩漲",
    "價跌量增": pv == "價跌量增", "價跌爆量": pv == "價跌爆量", "價漲量縮": pv == "價漲量縮",
    "融資未退": mlab == "融資未退", "融資清洗": mlab == "融資清洗", "融資追價": mlab == "融資追價",
    "緩跌＋融資未退": (speed == "緩跌") & (mlab == "融資未退"),
    "急跌＋融資清洗": (speed == "急跌") & (mlab == "融資清洗"),
    "急跌＋價跌爆量": (speed == "急跌") & (pv == "價跌爆量"),
}
ACTS = [("上限", 30), ("上限", 50), ("上限", 70), ("下限", 50), ("下限", 70)]
base = {pl: score(V5, sl) for pl, sl in (("IS", IS), ("OOS", OOS))}
rows = []
for (cn, cm), (a, lv) in itertools.product(CONDS.items(), ACTS):
    cm = cm.fillna(False)
    e = V5.where(~cm, np.minimum(V5, lv / 100) if a == "上限" else np.maximum(V5, lv / 100))
    if (e != V5).sum() == 0: continue
    r = {"條件": cn, "動作": f"{a}{lv}%", "影響天數": int((e != V5).sum())}
    for pl, sl in (("IS", IS), ("OOS", OOS)):
        s = score(e, sl)
        for k, nm in enumerate(("加權", "櫃買")):
            r[f"{pl}{nm}年化"] = s[k]["年化%"] - base[pl][k]["年化%"]
            r[f"{pl}{nm}回撤"] = s[k]["最大回撤%"] - base[pl][k]["最大回撤%"]
            r[f"{pl}{nm}Calmar"] = s[k]["Calmar"] - base[pl][k]["Calmar"]
    r["IS平均ΔCalmar"] = (r["IS加權Calmar"] + r["IS櫃買Calmar"]) / 2
    r["OOS平均ΔCalmar"] = (r["OOS加權Calmar"] + r["OOS櫃買Calmar"]) / 2
    rows.append(r)
g = pd.DataFrame(rows).sort_values("IS平均ΔCalmar", ascending=False)
print("\n=== v5 基準 ===")
for pl in ("IS", "OOS"):
    print(pl, [(round(s["年化%"], 2), round(s["最大回撤%"], 2), round(s["Calmar"], 2)) for s in base[pl]])
print("\n=== 疊加結果（相對 v5 的變化；依樣本內平均 ΔCalmar 排序）===")
print(g.round(2).to_string(index=False))
best = g.groupby("條件").head(1).head(5)
print("\n樣本內每個條件最好的動作，取前 5：")
print(best[["條件", "動作", "影響天數", "IS平均ΔCalmar", "OOS平均ΔCalmar", "OOS加權年化", "OOS加權回撤", "OOS櫃買年化", "OOS櫃買回撤"]].round(2).to_string(index=False))
g.to_csv("/tmp/pace_overlay.csv", index=False)
