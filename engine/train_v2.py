"""
v2：季線規則＋斜率（＋洗盤後回補），以及轉折訊號的事件研究。只用開發期 2015/6–2024/12。

水位：站上季線 100%；跌破季線 L1；跌破且季線下彎 L2。
回補：洗盤（跌停暴增／融資單日大減／爆量重挫）後 W 天內收盤站上 5 日線 → 水位至少 floor。
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None
sys.argv = sys.argv[:1]
sys.path.insert(0, str(Path(__file__).parent))
import backtest as bt  # noqa: E402
from model_v1 import washout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FUT_COST = 0.0003   # 用期貨調整：每單位水位變動的單邊成本（手續費、期交稅、滑價）
GRID = [dict(L1=l1, L2=l2, W=W, floor=fl)
        for l1, l2 in [(0.7, 0.7), (0.7, 0.5), (0.7, 0.3), (0.5, 0.5), (0.5, 0.3), (0.5, 0.0), (0.3, 0.3), (0.3, 0.0)]
        for W, fl in [(0, 0.0), (10, 0.7), (10, 1.0), (20, 0.7), (20, 1.0)]]


def exposure(f, Wt, p):
    below, down = f["close"] < f["ma60"], f["ma60_slope"] < -0.5
    e = pd.Series(np.where(below & down, p["L2"], np.where(below, p["L1"], 1.0)), index=f.index)
    if p["W"]:
        act = Wt.any(axis=1).rolling(p["W"], min_periods=1).max().astype(bool) & (f["close"] > f["ma5"])
        e = e.where(~act, np.maximum(e, p["floor"]))
    return e


def eq(e, ret, mode):
    if mode == "cash":
        return bt.equity(e, ret)
    d = e.diff().abs().fillna(0) * FUT_COST * bt.BETA
    return (1 + e.shift(1).fillna(e.iloc[0]) * ret - d.shift(1).fillna(0)).cumprod()


def perf(e, ret, bench, mode):
    return bt.stats(eq(e, ret, mode), bench, e)


def walk(expos, hb, tr, days, mode, margin=0.85):
    oos, picks = pd.Series(index=days, dtype=float), []
    for st in pd.date_range("2020-01-01", "2024-07-01", freq="6MS"):
        test = days[(days >= st) & (days < st + pd.DateOffset(months=6))]
        sl = slice(days[0], days[days < st][-21])
        b = bt.stats(bt.equity(pd.Series(1.0, index=tr.loc[sl].index), tr.loc[sl], False), tr.loc[sl])
        df = pd.DataFrame([perf(e.loc[sl], hb.loc[sl], tr.loc[sl], mode) for e in expos])
        ok = df[df["最大回撤%"] >= margin * b["最大回撤%"]]
        k = int(ok["年化%"].idxmax()) if len(ok) else int(df["最大回撤%"].idxmax())
        oos.loc[test] = expos[k].loc[test]; picks.append(k)
    return oos.dropna(), picks


def events(f, Wt, S, tr):
    """轉折訊號事件研究：訊號出現後 20 日的表現，與對照組比較。"""
    c = f["close"]
    eqi = (1 + tr).cumprod()
    fwd20 = eqi.shift(-20) / eqi - 1
    fmin = pd.concat([eqi.shift(-k) / eqi - 1 for k in range(1, 21)], axis=1).min(axis=1)
    fmax = pd.concat([eqi.shift(-k) / eqi - 1 for k in range(1, 21)], axis=1).max(axis=1)
    lo, hi = c.rolling(81, center=True, min_periods=41).min(), c.rolling(81, center=True, min_periods=41).max()
    pos = (c - lo) / (hi - lo)          # 在前後各 40 日區間中的位置：0＝最低點、1＝最高點
    weak, hi60 = c < f["ma60"], c >= c.rolling(60).max()
    any_w = Wt.any(axis=1)
    conf = any_w.rolling(10, min_periods=1).max().astype(bool) & (c > f["ma5"]) & (c.shift(1) <= f["ma5"].shift(1))
    bottoms = {
        "【對照】所有跌破季線的日子": weak,
        "跌停家數暴增": Wt["跌停家數暴增"], "融資單日減 2.5% 以上": Wt["融資單日大減"], "爆量重挫": Wt["爆量重挫"],
        "站上 60 日線比例 < 10%": f["above_ma60"] < 0.10,
        "創新低家數 > 全市場 20%": f["new_low"] / f["n"] > 0.20,
        "洗盤後 10 日內首次站上 5 日線": conf,
    }
    tops = {
        "【對照】所有創 60 日新高的日子": hi60,
        "新高＋站上 60 日線比例 < 50%": hi60 & (f["above_ma60"] < 0.5),
        "新高＋創新高家數低於近 60 日中位數": hi60 & (f["new_high"] < f["new_high"].rolling(60).median()),
        "新高＋櫃買 20 日落後 3% 以上": hi60 & (f["rel20"] < -0.03),
        "新高＋外資期貨淨空續增": hi60 & (S["C4"] == -1),
        "融資 5 日增 3%＋指數 5 日漲 3%": (f["margin_chg5"] > 0.03) & (f["ret5"] > 0.03),
        "新高＋費半跌破 200 日線": hi60 & (f["sox"] < f["sox"].rolling(200, min_periods=150).mean()),
    }
    out = []
    for kind, group in (("底部", bottoms), ("頭部", tops)):
        for name, m in group.items():
            m = m.fillna(False).astype(bool)
            if "對照" not in name:   # 10 日內只算第一次
                m = m & ~m.shift(1, fill_value=False).rolling(10, min_periods=1).max().astype(bool)
            v = m & fwd20.notna()
            if v.sum() == 0:
                out.append({"類型": kind, "訊號": name, "次數": 0}); continue
            out.append({"類型": kind, "訊號": name, "次數": int(v.sum()), "區間位置(中位)": pos[v].median(),
                        "20日報酬%": fwd20[v].mean() * 100, "上漲機率%": (fwd20[v] > 0).mean() * 100,
                        "20日內最深%": fmin[v].mean() * 100, "20日內最高%": fmax[v].mean() * 100,
                        "再跌8%以上機率%": (fmin[v] < -0.08).mean() * 100})
    return pd.DataFrame(out)


def main():
    data, f, S, tr, tp = bt.prepare()
    Wt = washout(f)
    for c in ("above_ma60", "new_high", "new_low", "n"):
        f[c] = data["breadth"][c].reindex(f.index)
    dev = slice(*bt.DEV)
    f, S, Wt, tr, tp = f.loc[dev], S.loc[dev], Wt.loc[dev], tr.loc[dev], tp.loc[dev]
    hb, days = bt.BETA * tr, f.index
    pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_columns", 20)

    ev = events(f, Wt, S, tr)
    print("=== 轉折訊號事件研究（開發期；報酬為加權報酬指數）==="); print(ev.round(2).to_string(index=False))

    expos = [exposure(f, Wt, p) for p in GRID]
    rows, curves, finals = {}, {}, {}
    for mode, mlab in (("cash", "現股調整"), ("fut", "期貨調整")):
        o, picks = walk(expos, hb, tr, days, mode)
        osl = slice(o.index[0], o.index[-1])
        one = pd.Series(1.0, index=o.index)
        ma = pd.Series(np.where(f["close"] > f["ma60"], 1.0, 0.5), index=days).loc[osl]
        if mode == "cash":
            rows["加權報酬指數（對標）"] = bt.stats(bt.equity(one, tr.loc[osl], False), tr.loc[osl])
            curves["加權報酬指數（對標）"] = bt.equity(one, tr.loc[osl], False)
            rows["持有 100%"] = perf(one, hb.loc[osl], tr.loc[osl], mode); curves["持有 100%"] = eq(one, hb.loc[osl], mode)
        rows[f"季線規則｜{mlab}"] = perf(ma, hb.loc[osl], tr.loc[osl], mode)
        rows[f"v2 滾動｜{mlab}"] = perf(o, hb.loc[osl], tr.loc[osl], mode)
        rows[f"v2 滾動｜{mlab}｜櫃買"] = perf(o, tp.loc[osl], tr.loc[osl], mode)
        curves[f"季線規則｜{mlab}"] = eq(ma, hb.loc[osl], mode); curves[f"v2 滾動｜{mlab}"] = eq(o, hb.loc[osl], mode)
        rng = np.random.default_rng(3)
        real = rows[f"v2 滾動｜{mlab}"]
        sims = pd.DataFrame([perf(pd.Series(np.roll(o.values, k), index=o.index), hb.loc[osl], tr.loc[osl], mode)
                             for k in rng.integers(40, len(o) - 40, 1000)])
        print(f"\n[{mlab}] 各段選到的參數：{[tuple(GRID[k].values()) for k in picks]}")
        print(f"[{mlab}] 隨機時點檢定：年化贏 {(real['年化%'] > sims['年化%']).mean()*100:.0f}%、回撤贏 {(real['最大回撤%'] > sims['最大回撤%']).mean()*100:.0f}%、Calmar 贏 {(real['Calmar'] > sims['Calmar']).mean()*100:.0f}%")
        yr = pd.DataFrame({"大盤": tr.loc[osl], "v2": eq(o, hb.loc[osl], mode).pct_change().fillna(0)})
        print(f"[{mlab}] 逐年%：\n{((1 + yr).groupby(yr.index.year).prod() - 1).mul(100).round(1).T}")
        # 定版（全開發期）與高原
        b = bt.stats(bt.equity(pd.Series(1.0, index=days), tr, False), tr)
        df = pd.DataFrame([perf(e, hb, tr, mode) for e in expos])
        ok = df[df["最大回撤%"] >= 0.85 * b["最大回撤%"]]
        k = int(ok["年化%"].idxmax()) if len(ok) else int(df["最大回撤%"].idxmax())
        finals[mode] = GRID[k]
        g = pd.concat([pd.DataFrame(GRID), df[["年化%", "最大回撤%", "平均水位%", "年調整次數"]]], axis=1)
        print(f"[{mlab}] 定版 {GRID[k]}；全開發期格點（大盤 {b['年化%']:.1f}% / {b['最大回撤%']:.1f}%）：\n{g.round(1).to_string(index=False)}")
        if OUT_DIR:
            OUT_DIR.mkdir(parents=True, exist_ok=True); o.to_csv(OUT_DIR / f"v2_expo_{mode}.csv")
    tab = pd.DataFrame(rows).T
    print("\n=== 樣本外合併 2020/1–2024/12（1.5×加權）==="); print(tab.round(2))
    (ROOT / "engine" / "params_v2.json").write_text(json.dumps(finals, indent=1))
    if OUT_DIR:
        pd.DataFrame(curves).to_csv(OUT_DIR / "v2_curves.csv"); ev.to_csv(OUT_DIR / "events.csv", index=False)


if __name__ == "__main__":
    main()
