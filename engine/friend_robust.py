import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("engine/friend_ideas.py").read().split("# ---- 事件研究 ----")[0])
    exec(open("engine/friend_ideas.py").read().split("# ---- 事件研究 ----")[1].split("rows = {}")[0])
def sc(e, sl): return [stats(equity(e.loc[sl], r.loc[sl]), r.loc[sl], e.loc[sl]) for r in (tr, tpr)]
B = {k: sc(V5, s) for k, s in (("IS", IS), ("OOS", OOS))}
def test(m, act, lv, h):
    m = m.fillna(False); m = m.rolling(h, min_periods=1).max().astype(bool) if h > 1 else m
    e = V5.where(~m, np.minimum(V5, lv) if act == "cap" else np.maximum(V5, lv))
    out = []
    for k, s in (("IS", IS), ("OOS", OOS)):
        x = sc(e, s); out += [round(x[i]["Calmar"] - B[k][i]["Calmar"], 3) for i in (0, 1)]
    return out, int((e != V5).sum()), e
print("外資賣超強度上限 30%（門檻敏感度）  [IS加權, IS櫃買, OOS加權, OOS櫃買]")
for th in (0.03, 0.04, 0.045, 0.05, 0.055, 0.06, 0.07):
    for lv in (0.3, 0.5):
        r, n, _ = test(fi20 <= -th, "cap", lv, 1); print(f"  賣超≥{th*100:.1f}% 上限{lv*100:.0f}%：{r} 影響 {n} 天")
print("台幣升值下限（門檻、維持天數敏感度）")
for th in (0.01, 0.0125, 0.015, 0.02, 0.025):
    for lv in (0.5, 0.7):
        for h in (1, 5, 10, 20):
            r, n, _ = test(fx20 <= -th, "floor", lv, h); print(f"  20日升≥{th*100:.2f}% 下限{lv*100:.0f}% 維持{h}：{r} 影響 {n} 天")
# 只看真正被改到的日子：之後 20 日
for nm, m, act, lv, h in (("外資賣超≥5% 上限30%", fi20 <= -0.05, "cap", .3, 1), ("台幣升≥1.5% 下限70% 維持10", fx20 <= -0.015, "floor", .7, 10)):
    _, _, e = test(m, act, lv, h)
    ch_ = (e != V5)
    for k, s in (("IS", IS), ("OOS", OOS)):
        mm = (ch_ & FT["r20"].notna()).loc[s]
        seg = (mm & ~mm.shift(1, fill_value=False))
        print(nm, k, "天", int(mm.sum()), "段", int(seg.sum()), "段起點", [d.strftime("%Y-%m") for d in seg[seg].index],
              "加權20日", round(FT["r20"].loc[s][mm].mean() * 100, 1), "櫃買20日", round(FO["r20"].loc[s][mm].mean() * 100, 1))
