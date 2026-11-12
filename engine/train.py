"""
v1 滾動訓練（只用開發期 2015/6–2024/12）

擴張窗口：初始訓練 2015/6–2019/12，之後每 6 個月往前測試一段、再併入訓練。
訓練與測試間隔 20 個交易日。目標：訓練段內「1.5×加權」回撤不深於加權報酬指數，報酬最高。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None
sys.argv = sys.argv[:1]
sys.path.insert(0, str(Path(__file__).parent))
import backtest as bt  # noqa: E402
from model_v1 import GRID, exposure, flags, washout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EMBARGO = 20
MARGIN = 0.85


def perf(e, ret, bench):
    eq = bt.equity(e, ret)
    return bt.stats(eq, bench, e)


def pick(expos, hb, tr, sl):
    """在訓練段挑參數：回撤不深於大盤者中年化最高；都不合格就挑回撤最淺。"""
    b = bt.stats(bt.equity(pd.Series(1.0, index=tr.loc[sl].index), tr.loc[sl], False), tr.loc[sl])
    res = [perf(e.loc[sl], hb.loc[sl], tr.loc[sl]) for e in expos]
    df = pd.DataFrame(res)
    ok = df[df["最大回撤%"] >= MARGIN * b["最大回撤%"]]   # 訓練段回撤要比大盤淺 15%，預留樣本外退化空間
    return int(ok["年化%"].idxmax()) if len(ok) else int(df["最大回撤%"].idxmax()), df


def main():
    data, f, S, tr, tp = bt.prepare()
    R, Wt = flags(f, S), washout(f)
    dev = slice(*bt.DEV)
    f, R, Wt, tr, tp = f.loc[dev], R.loc[dev], Wt.loc[dev], tr.loc[dev], tp.loc[dev]
    hb = bt.BETA * tr
    stable = f["close"] > f["ma5"]
    expos = [exposure(R, Wt, p, stable) for p in GRID]
    days = f.index

    # ---- 滾動（walk-forward）
    starts = pd.date_range("2020-01-01", "2024-07-01", freq="6MS")
    oos = pd.Series(index=days, dtype=float)
    log = []
    for st in starts:
        en = st + pd.DateOffset(months=6) - pd.Timedelta(days=1)
        test = days[(days >= st) & (days <= en)]
        train_end = days[days < st][-EMBARGO - 1]
        k, _ = pick(expos, hb, tr, slice(days[0], train_end))
        oos.loc[test] = expos[k].loc[test]
        seg = perf(expos[k].loc[test], hb.loc[test], tr.loc[test])
        bseg = (1 + tr.loc[test]).prod() - 1
        sseg = bt.equity(expos[k].loc[test], hb.loc[test]).iloc[-1] - 1
        log.append({"測試段": f"{st:%Y/%m}", **GRID[k], "策略%": sseg * 100, "大盤%": bseg * 100,
                    "策略回撤%": seg["最大回撤%"], "大盤回撤%": ((1 + tr.loc[test]).cumprod() / (1 + tr.loc[test]).cumprod().cummax() - 1).min() * 100})
    log = pd.DataFrame(log)
    o = oos.dropna()
    osl = slice(o.index[0], o.index[-1])
    one = pd.Series(1.0, index=o.index)
    ma = pd.Series(np.where(f["close"] > f["ma60"], 1.0, 0.5), index=days).loc[osl]

    pd.set_option("display.width", 220, "display.unicode.east_asian_width", True)
    print("=== 各測試段選到的參數與表現 ==="); print(log.round(1).to_string(index=False))
    print(f"\n贏大盤的段數（報酬）：{(log['策略%'] > log['大盤%']).sum()}/{len(log)}；回撤較淺的段數：{(log['策略回撤%'] > log['大盤回撤%']).sum()}/{len(log)}")

    rows, curves = {}, {}
    rows["加權報酬指數（對標）"] = bt.stats(bt.equity(one, tr.loc[osl], False), tr.loc[osl])
    curves["加權報酬指數（對標）"] = bt.equity(one, tr.loc[osl], False)
    for lab, ret in (("1.5×加權", hb), ("櫃買指數", tp)):
        for name, e in (("持有100%", one), ("季線規則", ma), ("v1 滾動", o)):
            rows[f"{lab}｜{name}"] = perf(e, ret.loc[osl], tr.loc[osl]); curves[f"{lab}｜{name}"] = bt.equity(e, ret.loc[osl])
    rows["1.5×加權｜v1 滾動（零成本）"] = bt.stats(bt.equity(o, hb.loc[osl], False), tr.loc[osl], o)
    tab = pd.DataFrame(rows).T
    print(f"\n=== 樣本外合併 {o.index[0].date()} ~ {o.index[-1].date()} ==="); print(tab.round(2))

    # 隨機時點
    rng = np.random.default_rng(7)
    real = perf(o, hb.loc[osl], tr.loc[osl])
    sims = pd.DataFrame([perf(pd.Series(np.roll(o.values, k), index=o.index), hb.loc[osl], tr.loc[osl])
                         for k in rng.integers(40, len(o) - 40, 1000)])
    print(f"\n隨機時點檢定：年化贏過 {(real['年化%'] > sims['年化%']).mean()*100:.0f}%、回撤贏過 {(real['最大回撤%'] > sims['最大回撤%']).mean()*100:.0f}%、Calmar 贏過 {(real['Calmar'] > sims['Calmar']).mean()*100:.0f}% 的隨機水位")

    # 逐年
    yr = pd.DataFrame({"大盤": tr.loc[osl], "高beta持有": hb.loc[osl],
                       "v1": bt.equity(o, hb.loc[osl]).pct_change().fillna(0)})
    print("\n=== 逐年報酬% ==="); print(((1 + yr).groupby(yr.index.year).prod() - 1).mul(100).round(1))

    # ---- 定版：用整個開發期訓練一次
    k, df = pick(expos, hb, tr, slice(days[0], days[-1]))
    final = GRID[k]
    print(f"\n=== 定版參數（全開發期）：{final} ==="); print(pd.Series(df.loc[k]).round(2).to_string())
    # 參數高原：定版參數每次只動一格
    print("\n=== 參數高原（定版參數一次改一個）===")
    for i, p in enumerate(GRID):
        diff = [key for key in p if p[key] != final[key]]
        if len(diff) == 1 or (set(diff) == {"W", "floor"} and (p["W"] == 0 or final["W"] == 0)):
            print(f"  {str({d: p[d] for d in diff}):28s} 年化 {df.loc[i,'年化%']:.1f}  回撤 {df.loc[i,'最大回撤%']:.1f}  平均水位 {df.loc[i,'平均水位%']:.0f}  調整 {df.loc[i,'年調整次數']:.0f}/年")
    print(f"  全格點 {len(GRID)} 組：回撤合格 {(df['最大回撤%'] >= rows['加權報酬指數（對標）']['最大回撤%']).sum()} 組；年化中位數 {df['年化%'].median():.1f}")

    (ROOT / "engine" / "params_v1.json").write_text(json.dumps(final, indent=1))
    if OUT_DIR:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(curves).to_csv(OUT_DIR / "oos_curves.csv"); o.to_csv(OUT_DIR / "oos_expo.csv"); tab.to_csv(OUT_DIR / "oos_stats.csv")


if __name__ == "__main__":
    main()
