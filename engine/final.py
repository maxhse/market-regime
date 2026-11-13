"""定版規則的穩健性檢查（開發期）與最終驗收（2025/1 起，只跑一次）。  python engine/final.py [--final] [輸出資料夾]"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

FINAL = "--final" in sys.argv
OUT_DIR = next((Path(a) for a in sys.argv[1:] if not a.startswith("--")), None)
sys.argv = sys.argv[:1]
sys.path.insert(0, str(Path(__file__).parent))
import backtest as bt  # noqa: E402
from rule import rule  # noqa: E402


def run(e, ret, bench):
    return bt.stats(bt.equity(e, ret), bench, e)


def table(tx, tr, tp, sl, variants, betas=(1.5,)):
    rows = {}
    one = pd.Series(1.0, index=tr.loc[sl].index)
    rows["加權報酬指數（對標）"] = bt.stats(bt.equity(one, tr.loc[sl], False), tr.loc[sl])
    for name, e in variants.items():
        for b in betas:
            rows[f"{name}｜beta {b}"] = run(e.loc[sl], b * tr.loc[sl], tr.loc[sl])
    return pd.DataFrame(rows).T


def main():
    data, f, S, tr, tp = bt.prepare()
    tx = data["taiex"]
    pd.set_option("display.width", 220, "display.unicode.east_asian_width", True)
    R = rule(tx["close"], tx["high"], tx["low"])
    e3 = R["expo"] / 100
    e1 = rule(tx["close"], tx["high"], tx["low"], n=1)["expo"] / 100
    one = pd.Series(1.0, index=tx.index)
    dev = slice(*bt.DEV)
    if not FINAL:
        print("=== 開發期：均線天數高原（3 日確認，beta 1.5，現股成本）===")
        v = {f"{n} 日線": rule(tx["close"], tx["high"], tx["low"], ma_len=n)["expo"] / 100 for n in (20, 40, 50, 60, 80, 100, 120)}
        print(table(tx, tr, tp, dev, v).round(2))
        print("\n=== 開發期：beta 敏感度（定版規則）===")
        print(table(tx, tr, tp, dev, {"定版": e3, "持有 100%": one}, betas=(1.0, 1.2, 1.5, 1.8)).round(2))
        print("\n=== 開發期：櫃買指數（不含息）===")
        print(pd.DataFrame({"櫃買持有": run(one.loc[dev], tp.loc[dev], tr.loc[dev]), "櫃買＋定版": run(e3.loc[dev], tp.loc[dev], tr.loc[dev])}).T.round(2))
        return
    ho = slice("2025-01-01", None)
    print(f"=== 最終驗收 {tr.loc[ho].index[0].date()} ~ {tr.loc[ho].index[-1].date()}（{len(tr.loc[ho])} 交易日）===")
    t = table(tx, tr, tp, ho, {"持有 100%": one, "季線＋斜率（不確認）": e1, "定版（3 日確認）": e3}, betas=(1.0, 1.5))
    t.loc["櫃買｜持有"] = run(one.loc[ho], tp.loc[ho], tr.loc[ho]); t.loc["櫃買｜定版"] = run(e3.loc[ho], tp.loc[ho], tr.loc[ho])
    print(t.round(2))
    hb = 1.5 * tr.loc[ho]
    rng = np.random.default_rng(11); o = e3.loc[ho]; real = run(o, hb, tr.loc[ho])
    sims = pd.DataFrame([run(pd.Series(np.roll(o.values, k), index=o.index), hb, tr.loc[ho]) for k in rng.integers(20, len(o) - 20, 1000)])
    print(f"隨機時點：年化贏 {(real['年化%'] > sims['年化%']).mean()*100:.0f}%、回撤贏 {(real['最大回撤%'] > sims['最大回撤%']).mean()*100:.0f}%、Calmar 贏 {(real['Calmar'] > sims['Calmar']).mean()*100:.0f}%")
    ch = o[o.diff().fillna(0) != 0]
    print("\n水位變動紀錄："); print(pd.DataFrame({"水位": (ch * 100).astype(int), "收盤": tx["close"].reindex(ch.index).round(0)}).to_string())
    if OUT_DIR:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"加權報酬指數（對標）": bt.equity(one.loc[ho], tr.loc[ho], False), "持有": bt.equity(one.loc[ho], hb), "定版": bt.equity(o, hb)}).to_csv(OUT_DIR / "ho_curves.csv")
        pd.DataFrame({"expo": o, "close": tx["close"].loc[ho], "ma": R["ma"].loc[ho]}).to_csv(OUT_DIR / "ho_expo.csv")


if __name__ == "__main__":
    main()
