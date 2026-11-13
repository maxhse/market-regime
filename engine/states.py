"""
盤勢狀態分類與「回檔 vs 轉弱」研究。只用開發期 2015/6–2024/12（驗收期已用過，不再使用）。

狀態（每日、只用當日以前的資料）：
  多頭趨勢  季線上狀態，收盤在月線上
  多頭回檔  季線上狀態，收盤跌破月線
  區間震盪  季線走平，且近 40 日穿越季線 4 次以上
  轉弱      季線下狀態（3 日確認），季線未下彎，收盤在月線下
  空頭      季線下狀態，季線下彎，收盤在月線下
  跌深反彈  季線下狀態，收盤站上月線
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.argv = sys.argv[:1]
sys.path.insert(0, str(Path(__file__).parent))
import backtest as bt  # noqa: E402
from rule import rule, classify as _classify  # noqa: E402

ORDER = ["多頭趨勢", "多頭回檔", "區間震盪", "轉弱", "跌深反彈", "空頭"]


def classify(f, R):
    return _classify(f["close"], R)


def fwd_tools(tr):
    eq = (1 + tr).cumprod()
    f20 = eq.shift(-20) / eq - 1
    worst = pd.concat([eq.shift(-k) / eq - 1 for k in range(1, 21)], axis=1).min(axis=1)
    vol = tr.rolling(20).std().shift(-20) * np.sqrt(245)
    return f20, worst, vol


def pullbacks(f, R, feats):
    """多頭中的回檔：季線上狀態、季線未下彎時收盤跌破月線。之後 60 日內先回到前高＝回檔結束；先跌到離前高 -10%＝轉空。"""
    c = f["close"]; ep = []; i, n = 60, len(c)
    cv, m20 = c.values, f["ma20"].values
    while i < n - 1:
        if (not R["state"].iat[i]) and R["slope"].iat[i] >= -0.5 and cv[i] < m20[i] and cv[i - 1] >= m20[i - 1]:
            peak = cv[max(0, i - 40):i + 1].max(); out, j = "未定", i
            for j in range(i + 1, min(i + 61, n)):
                if cv[j] >= peak: out = "回檔結束"; break
                if cv[j] <= peak * 0.90: out = "轉空"; break
            else:
                out = "盤整逾 60 日" if i + 60 < n else "未定"
            lo = cv[i:j + 1].min()
            ep.append({"日期": c.index[i], "結果": out, "天數": j - i, "起點離前高%": (cv[i] / peak - 1) * 100,
                       "最深離前高%": (lo / peak - 1) * 100, **{k: v.iat[i] for k, v in feats.items()}})
            i = j
        i += 1
    return pd.DataFrame(ep)


def main():
    data, f, S, tr, tp = bt.prepare()
    tx, br = data["taiex"], data["breadth"].reindex(f.index)
    R = rule(tx["close"], tx["high"], tx["low"]).reindex(f.index)
    st = classify(f, R)
    dev = slice(*bt.DEV)
    pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 200)

    # ---- 1. 各狀態之後的表現
    f20, worst, vol = fwd_tools(tr)
    d = pd.DataFrame({"狀態": st, "f20": f20, "worst": worst, "vol": vol}).loc[dev].dropna()
    g = d.groupby("狀態")
    tab = pd.DataFrame({"天數占比%": g.size() / len(d) * 100, "20日報酬%": g["f20"].mean() * 100, "上漲機率%": g["f20"].apply(lambda x: (x > 0).mean() * 100),
                        "20日內最深%": g["worst"].mean() * 100, "再跌8%機率%": g["worst"].apply(lambda x: (x < -0.08).mean() * 100),
                        "之後年化波動%": g["vol"].mean() * 100,
                        "前段報酬%": d.loc[:"2019"].groupby("狀態")["f20"].mean() * 100, "後段報酬%": d.loc["2020":].groupby("狀態")["f20"].mean() * 100}).reindex(ORDER)
    print(f"=== 各盤勢狀態之後 20 日（開發期；全期平均報酬 {d.f20.mean()*100:.2f}%、最深 {d.worst.mean()*100:.2f}%）==="); print(tab.round(1))
    seg = (st != st.shift()).loc[dev].sum() / (len(d) / 245)
    print(f"狀態每年切換約 {seg:.0f} 次")

    # ---- 2. 回檔 vs 轉空
    sox200 = f["sox"] < f["sox"].rolling(200, min_periods=150).mean()
    feats = {
        "站上60日線比例": br["above_ma60"], "廣度10日變化": br["above_ma60"] - br["above_ma60"].shift(10),
        "上漲家數比10日": f["adr10"], "創新低占比%": br["new_low"] / br["n"] * 100,
        "融資5日%": (f["margin_chg5"] * 100).shift(1), "融資增指數跌": (S["C1"] == -1).astype(float),
        "外資現貨z": f["foreign_flow_z"].shift(1), "外資期貨淨空續增": (S["C4"] == -1).astype(float),
        "外資期貨OI": f["fut_oi"].shift(1), "跌破日量z": f["vol_z"], "櫃買相對20日%": f["rel20"] * 100,
        "費半在200日線下": sox200.astype(float), "季線斜率": R["slope"], "離季線ATR倍數": (f["close"] - R["ma"]) / f["atr"],
        "指數離季線%": (f["close"] / R["ma"] - 1) * 100,
    }
    ep = pullbacks(f.loc[dev], R.loc[dev], {k: v.loc[dev] for k, v in feats.items()})
    ep = ep[ep["結果"] != "未定"]
    print(f"\n=== 多頭回檔共 {len(ep)} 次 ==="); print(ep["結果"].value_counts().to_string())
    print(ep.groupby("結果")[["天數", "起點離前高%", "最深離前高%"]].median().round(1))
    bad = ep["結果"] == "轉空"
    rows = []
    for k in feats:
        x = ep[k]
        if x.nunique() <= 2:
            hi = x > 0.5; lab = "是 / 否"
        else:
            hi = x > x.median(); lab = f"高於 / 低於中位數 {x.median():.2f}"
        rows.append({"特徵（回檔起點當天）": k, "分組": lab, "高組次數": int(hi.sum()), "高組轉空%": bad[hi].mean() * 100 if hi.sum() else np.nan,
                     "低組次數": int((~hi).sum()), "低組轉空%": bad[~hi].mean() * 100 if (~hi).sum() else np.nan,
                     "轉空中位": x[bad].median(), "回檔結束中位": x[ep["結果"] == "回檔結束"].median()})
    ft = pd.DataFrame(rows); ft["差距"] = (ft["高組轉空%"] - ft["低組轉空%"]).abs()
    print(f"\n整體轉空比例 {bad.mean()*100:.0f}%（{int(bad.sum())}/{len(ep)}）"); print(ft.sort_values("差距", ascending=False).round(2).to_string(index=False))
    print("\n轉空的回檔："); print(ep[bad][["日期", "天數", "起點離前高%", "站上60日線比例", "廣度10日變化", "櫃買相對20日%", "融資5日%", "外資期貨OI", "季線斜率", "指數離季線%"]].round(2).to_string(index=False))

    # ---- 3. 狀態對應水位的回測（1.5×加權，現股成本）
    hb = bt.BETA * tr
    maps = {
        "定版規則（100/50/30）": R["expo"] / 100,
        "狀態 A：趨勢100 回檔100 震盪70 轉弱50 反彈50 空頭30": st.map({"多頭趨勢": 1, "多頭回檔": 1, "區間震盪": .7, "轉弱": .5, "跌深反彈": .5, "空頭": .3}),
        "狀態 B：趨勢100 回檔70 震盪70 轉弱50 反彈50 空頭30": st.map({"多頭趨勢": 1, "多頭回檔": .7, "區間震盪": .7, "轉弱": .5, "跌深反彈": .5, "空頭": .3}),
        "狀態 C：趨勢100 回檔100 震盪100 轉弱50 反彈70 空頭30": st.map({"多頭趨勢": 1, "多頭回檔": 1, "區間震盪": 1, "轉弱": .5, "跌深反彈": .7, "空頭": .3}),
    }
    res = {}
    for lab, sl in (("全期", dev), ("前段", slice("2015-06-01", "2019-12-31")), ("後段", slice("2020-01-01", "2024-12-31"))):
        one = pd.Series(1.0, index=tr.loc[sl].index)
        b = bt.stats(bt.equity(one, tr.loc[sl], False), tr.loc[sl]); res[("大盤", lab)] = {"年化%": b["年化%"], "回撤%": b["最大回撤%"]}
        for name, e in maps.items():
            e = e.loc[sl].ffill().fillna(1.0); s = bt.stats(bt.equity(e, hb.loc[sl]), tr.loc[sl], e)
            res[(name, lab)] = {"年化%": s["年化%"], "回撤%": s["最大回撤%"], "年調整": s["年調整次數"]}
    print("\n=== 狀態對應水位（開發期，1.5×加權，現股成本）==="); print(pd.DataFrame(res).T.round(1).to_string())


if __name__ == "__main__":
    main()
