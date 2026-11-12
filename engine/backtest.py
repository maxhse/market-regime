"""
回測（只跑開發期 2015/6–2024/12；2025 起為最終驗收期，開發階段不看）

python engine/backtest.py            → 基準、v0、隨機水位、單一訊號證據
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None
sys.argv = sys.argv[:1]  # build.py 也會讀 argv
sys.path.insert(0, str(Path(__file__).parent))
from build import load_all, build_features, total_score, to_tiers  # noqa: E402

DEV = ("2015-06-01", "2024-12-31")
BETA = 1.5
COST_SELL, COST_BUY = 0.004425, 0.001425   # 賣：手續費+證交稅；買：手續費
CHIP_LAG = ["C1", "C2", "C3", "C4"]        # 盤後才公布 → 次日才能用


def equity(expo: pd.Series, ret: pd.Series, cost=True):
    """expo[t] 為 t 日收盤決定的水位，吃 t+1 日報酬。"""
    e = expo.shift(1).fillna(expo.iloc[0])
    d = expo.diff().fillna(0)
    c = np.where(d < 0, -d * COST_SELL, d * COST_BUY) if cost else 0.0
    return (1 + e * ret - pd.Series(c, index=expo.index).shift(1).fillna(0)).cumprod()


def stats(eq: pd.Series, bench_ret: pd.Series, expo=None):
    r = eq.pct_change().dropna()
    yrs = len(r) / 245
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    mdd = (eq / eq.cummax() - 1).min()
    dn = bench_ret.reindex(r.index) < 0
    out = {"年化%": cagr * 100, "最大回撤%": mdd * 100, "Calmar": cagr / abs(mdd),
           "下檔捕獲": r[dn].mean() / bench_ret.reindex(r.index)[dn].mean()}
    if expo is not None:
        out["平均水位%"] = expo.mean() * 100
        out["年調整次數"] = (expo.diff().abs() > 0).sum() / yrs
    return out


def prepare():
    data, _ = load_all()
    f, S = build_features(data)
    S = S.copy()
    S[[c for c in CHIP_LAG if c in S]] = S[[c for c in CHIP_LAG if c in S]].shift(1)
    tr = data["taiex_tr"]["close"].reindex(f.index).pct_change().fillna(0)
    tp = data["tpex"]["close"].reindex(f.index).pct_change().fillna(0)
    return data, f, S, tr, tp


def main():
    data, f, S, tr, tp = prepare()
    score, _ = total_score(S)
    v0 = to_tiers(score) / 100
    sl = slice(*DEV)
    f, S, tr, tp, v0 = f.loc[sl], S.loc[sl], tr.loc[sl], tp.loc[sl], v0.loc[sl]
    hb = BETA * tr
    one = pd.Series(1.0, index=f.index)
    ma = pd.Series(np.where(f["close"] > f["ma60"], 1.0, 0.5), index=f.index)

    rows, curves = {}, {}
    rows["加權報酬指數（對標）"] = stats(equity(one, tr, False), tr)
    for label, ret in (("1.5×加權", hb), ("櫃買指數", tp)):
        for name, ex in (("持有100%", one), ("季線規則", ma), ("v0 模型", v0)):
            eq = equity(ex, ret)
            rows[f"{label}｜{name}"] = stats(eq, tr, ex)
            curves[f"{label}｜{name}"] = eq
    curves["加權報酬指數（對標）"] = equity(one, tr, False)
    tab = pd.DataFrame(rows).T
    pd.set_option("display.width", 200, "display.unicode.east_asian_width", True)
    print(f"開發期 {DEV[0]} ~ {DEV[1]}（{len(f)} 交易日）\n"); print(tab.round(2))

    # 隨機水位：把水位序列循環平移（平均水位、調整次數完全相同，只有時點不同）
    rng = np.random.default_rng(1)
    for label, ret in (("1.5×加權", hb), ("櫃買指數", tp)):
        for name, ex in (("季線規則", ma), ("v0 模型", v0)):
            real = stats(equity(ex, ret), tr)
            sims = []
            for k in rng.integers(60, len(ex) - 60, 1000):
                sh = pd.Series(np.roll(ex.values, k), index=ex.index)
                sims.append(stats(equity(sh, ret), tr))
            sims = pd.DataFrame(sims)
            print(f"隨機時點檢定 {label}｜{name}：Calmar 贏過 {(real['Calmar'] > sims['Calmar']).mean()*100:.0f}% 隨機，"
                  f"回撤贏過 {(real['最大回撤%'] > sims['最大回撤%']).mean()*100:.0f}% 隨機")

    # 單一訊號證據：訊號狀態 → 之後 20 日「1.5×加權」的報酬與最差回落
    eqh = (1 + hb).cumprod()
    fwd = eqh.shift(-20) / eqh - 1
    worst = pd.concat([eqh.shift(-k) / eqh - 1 for k in range(1, 21)], axis=1).min(axis=1)
    base = fwd.mean() * 100
    ev = []
    halves = {"前段(15-19)": slice("2015-06-01", "2019-12-31"), "後段(20-24)": slice("2020-01-01", "2024-12-31")}
    for c in S.columns:
        for lab, m in (("偏多", S[c] > 0), ("偏空", S[c] < 0)):
            if m.sum() < 30:
                continue
            row = {"訊號": c, "狀態": lab, "天數": int(m.sum()), "20日報酬%": fwd[m].mean() * 100,
                   "相對平均": fwd[m].mean() * 100 - base, "20日最差回落%": worst[m].mean() * 100}
            for hn, hs in halves.items():
                mm = m.loc[hs]
                row[hn] = (fwd.loc[hs][mm].mean() - fwd.loc[hs].mean()) * 100 if mm.sum() >= 15 else np.nan
            ev.append(row)
    ev = pd.DataFrame(ev)
    print(f"\n單一訊號證據（全期平均 20 日報酬 {base:.2f}%，平均最差回落 {worst.mean()*100:.2f}%）\n")
    print(ev.round(2).to_string(index=False))

    out = OUT_DIR
    if out:
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(curves).to_csv(out / "curves.csv")
        pd.DataFrame({"v0": v0, "ma": ma, "close": f["close"]}).to_csv(out / "expo.csv")
        tab.to_csv(out / "stats.csv"); ev.to_csv(out / "evidence.csv", index=False)


if __name__ == "__main__":
    main()
