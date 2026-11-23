"""候選一（台幣快速升值→下限 70%）與候選二（外資重賣→上限 30%）同時使用；外資重賣的標準化方式比較。"""
import io, contextlib
import numpy as np, pandas as pd
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("engine/friend_ideas.py").read().split("# ---- 事件研究 ----")[0])
cap_ = pd.read_csv("data/raw/mktcap.csv", parse_dates=["date"]).set_index("date")
print("市值欄位：", cap_.columns.tolist())
mcap = cap_[cap_.columns[0]].reindex(idx).ffill(limit=3)
def sc(e, sl): return [stats(equity(e.loc[sl], r.loc[sl]), r.loc[sl], e.loc[sl]) for r in (tr, tpr)]
def show(nm, e):
    out = [nm]
    for sl in (IS, OOS):
        for s in sc(e, sl):
            out += [f"{s['年化%']:.1f}%/{s['最大回撤%']:.1f}%/{s['Calmar']:.2f}"]
    out.append(f"{(e != V5).sum()} 天")
    return out
hold = lambda m, n: m.fillna(False).rolling(n, min_periods=1).max().astype(bool)
fxup = hold(fx20 <= -0.015, 10)
sell = (fi20 <= -0.05).fillna(False)
E = {"v5（目前）": V5,
     "＋候選一 台幣升值": V5.where(~fxup, np.maximum(V5, .7)),
     "＋候選二 外資重賣": V5.where(~sell, np.minimum(V5, .3)),
     "兩者都用｜衝突時外資優先": V5.where(~fxup, np.maximum(V5, .7)).where(~sell, np.minimum(V5.where(~fxup, np.maximum(V5, .7)), .3)),
     "兩者都用｜衝突時台幣優先": V5.where(~sell, np.minimum(V5, .3)).where(~fxup, np.maximum(V5.where(~sell, np.minimum(V5, .3)), .7)),
     "兩者都用｜衝突時兩者抵銷（照 v5）": V5.where(~(fxup & ~sell), np.maximum(V5, .7)).where(~(sell & ~fxup), np.minimum(V5, .3))}
rows = [show(k, v) for k, v in E.items()]
pd.set_option("display.width", 260, "display.unicode.east_asian_width", True)
print(pd.DataFrame(rows, columns=["", "內｜加權", "內｜櫃買", "外｜加權", "外｜櫃買", "與 v5 不同"]).to_string(index=False))
both = (fxup & sell)
print("兩者同一天出現：", int(both.sum()), "天", [d.strftime("%Y-%m-%d") for d in both[both].index][:20])

print("\n=== 外資重賣：三種標準化（上限 30%）｜ΔCalmar [內加權, 內櫃買, 外加權, 外櫃買]、被改到的天數 ===")
B = {k: sc(V5, s) for k, s in (("IS", IS), ("OOS", OOS))}
def d(m):
    e = V5.where(~m.fillna(False), np.minimum(V5, .3)); r = []
    for k, s in (("IS", IS), ("OOS", OOS)):
        x = sc(e, s); r += [round(x[i]["Calmar"] - B[k][i]["Calmar"], 3) for i in (0, 1)]
    return r, int((e != V5).sum())
f20 = fs.rolling(20).sum()
for nm, ser, ths in (("÷ 上市成交金額（加權成交值）", fi20, (0.04, 0.05, 0.06)),
                     ("÷ 上市總市值", f20 / mcap, (-0.004, -0.006, -0.008)),
                     ("賣超金額（億）", f20, (-1500, -2500, -3500))):
    for th in ths:
        th_ = -abs(th) if nm.startswith("÷ 上市成交") else th
        r, n = d(ser <= th_); print(f"  {nm} ≤ {th_}: {r} 影響 {n} 天")
