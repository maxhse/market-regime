"""加權與櫃買是否輪流領先。沿用 tpex_rule.py 的 v4 水位（et 加權、eo 櫃買）。"""
import numpy as np, pandas as pd
exec(open("engine/tpex_rule.py").read().split("S = {")[0])
port = {"A 1.5×加權報酬": 1.5 * tr, "B 櫃買指數": tpr, "C 各半": 0.75 * tr + 0.5 * tpr}
bench = {"A 1.5×加權報酬": tr, "B 櫃買指數": tpr, "C 各半": 0.5 * tr + 0.5 * tpr}
pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 200)
E = pd.DataFrame({"t": et, "o": eo}).loc["2014-07-01":].dropna()
strong = (E == 1)

# 1) 轉弱／轉強事件配對：同方向、前後 40 個交易日內最近的一次
def turns(s):
    d = s.astype(int).diff()
    return {"轉弱": list(s.index[d == -1]), "轉強": list(s.index[d == 1])}
Tt, To = turns(strong.t), turns(strong.o)
pos = {d: i for i, d in enumerate(E.index)}
rows = []
for kind in ("轉弱", "轉強"):
    used = set()
    for a in Tt[kind]:
        c = [b for b in To[kind] if abs(pos[b] - pos[a]) <= 40 and b not in used]
        if not c: rows.append({"日期": a, "類型": kind, "誰先": "只有加權", "早幾天": np.nan}); continue
        b = min(c, key=lambda x: abs(pos[x] - pos[a])); used.add(b)
        lag = pos[a] - pos[b]   # >0：櫃買先
        rows.append({"日期": min(a, b), "類型": kind, "誰先": "櫃買先" if lag > 0 else "加權先" if lag < 0 else "同一天", "早幾天": abs(lag)})
    for b in To[kind]:
        if b not in used: rows.append({"日期": b, "類型": kind, "誰先": "只有櫃買", "早幾天": np.nan})
ev = pd.DataFrame(rows).sort_values("日期").reset_index(drop=True)
ev["年"] = ev["日期"].dt.year
print("=== 1) 轉弱／轉強時誰先（配對在 40 日內）===")
print(pd.crosstab(ev["類型"], ev["誰先"]))
print("\n先發生的一方平均早幾天：", ev.dropna().groupby("誰先")["早幾天"].median().to_dict())
print("\n逐年：")
print(pd.crosstab(ev["年"], ev["誰先"]).to_string())
print("\n逐筆（配對成功者）：")
print(ev[ev["誰先"].isin(["櫃買先", "加權先"])][["日期", "類型", "誰先", "早幾天"]].to_string(index=False))

# 2) 不一致時誰向誰靠攏
dis = (E.t != E.o)
grp = (dis != dis.shift()).cumsum()
res = []
for g, x in E[dis].groupby(grp[dis]):
    end = x.index[-1]; i = pos[end]
    if i + 1 >= len(E): continue
    nxt = E.iloc[i + 1]
    weak_side = "櫃買弱" if x.o.iloc[-1] < x.t.iloc[-1] else "加權弱"
    # 結束時：哪一邊動了
    moved_t, moved_o = nxt.t != x.t.iloc[-1], nxt.o != x.o.iloc[-1]
    if (nxt.t == nxt.o):
        who = "強的跟著轉弱（弱的一方領先）" if min(nxt.t, nxt.o) < max(x.t.iloc[-1], x.o.iloc[-1]) and \
              ((weak_side == "櫃買弱" and moved_t) or (weak_side == "加權弱" and moved_o)) else "弱的回升（強的一方領先）"
    else:
        who = "其他"
    res.append({"開始": x.index[0], "天數": len(x), "情況": weak_side, "結果": who})
res = pd.DataFrame(res)
print("\n=== 2) 不一致的段落，最後怎麼結束 ===")
print(pd.crosstab(res["情況"], res["結果"]))
print("\n逐年：")
print(pd.crosstab(res["開始"].dt.year, [res["情況"], res["結果"]]).to_string())

# 3) 策略：不一致時，跟隨「最近一次配對事件中先動的那一邊」
lead = pd.Series(np.nan, index=E.index, dtype=object)
known = ev[ev["誰先"].isin(["櫃買先", "加權先"])].copy()
# 事件要等兩邊都動了才知道誰先 → 以後動那一方的日期 + 1 天起才可用
known["可用日"] = [E.index[min(pos[d] + int(k) + 1, len(E) - 1)] for d, k in zip(known["日期"], known["早幾天"])]
for d, w in zip(known["可用日"], known["誰先"]):
    lead.loc[d] = w
lead = lead.ffill()
follow = pd.Series(np.where(E.t == E.o, E.t, np.where(lead == "櫃買先", E.o, np.where(lead == "加權先", E.t, np.minimum(E.t, E.o)))), index=E.index)
# 跟隨最近變動的一方：哪一邊的水位最近一次改變較晚，就用哪一邊
lt = pd.Series(np.where(E.t != E.t.shift(), np.arange(len(E)), np.nan), index=E.index).ffill()
lo_ = pd.Series(np.where(E.o != E.o.shift(), np.arange(len(E)), np.nan), index=E.index).ffill()
recent = pd.Series(np.where(E.t == E.o, E.t, np.where(lo_ > lt, E.o, np.where(lt > lo_, E.t, np.minimum(E.t, E.o)))), index=E.index)
asym = pd.Series(np.where(E.t == E.o, E.t, np.where(lo_ > lt, E.o, np.where(lt > lo_, E.t, np.minimum(E.t, E.o)))), index=E.index)
strat = {"跟隨最近變動的一方": recent, "只看加權（目前）": E.t, "只看櫃買": E.o, "取較低": np.minimum(E.t, E.o), "跟隨最近的領先者": follow,
         "跟隨最近的落後者": pd.Series(np.where(E.t == E.o, E.t, np.where(lead == "櫃買先", E.t, np.where(lead == "加權先", E.o, np.minimum(E.t, E.o)))), index=E.index)}
print("\n=== 3) 策略比較 ===")
for plab, sl in (("樣本內 2014/7–2021", slice("2014-07-01", "2021-12-31")), ("樣本外 2022/1–2026/11", slice("2022-01-01", None))):
    out = {}
    for k in ("C 各半", "A 1.5×加權報酬", "B 櫃買指數"):
        for n, e in strat.items():
            s = stats(equity(e.loc[sl], port[k].loc[sl]), bench[k].loc[sl], e.loc[sl])
            out[(k, n)] = {"年化%": s["年化%"], "最大回撤%": s["最大回撤%"], "Calmar": s["Calmar"], "平均水位%": s["平均水位%"]}
    print(f"\n{plab}"); print(pd.DataFrame(out).T.round(2).to_string())
print("\n目前最近的領先者：", lead.iloc[-1])
