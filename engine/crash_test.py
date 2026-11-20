"""急跌煞車測試（開發期 2015/6–2024/12）。在定版季線規則上加一層上限，水位以 5% 為單位。"""
import sys; sys.argv = sys.argv[:1]
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
import backtest as bt
from rule import rule

data, f, S, tr, tp = bt.prepare()
tx = data["taiex"]
R = rule(tx["close"], tx["high"], tx["low"]).reindex(f.index)
base = R["expo"] / 100
c, ma20, ma60, atr = f["close"], f["ma20"], R["ma"], f["atr"]
dd20 = c / c.rolling(20).max() - 1          # 離 20 日高點
dd60 = c / c.rolling(60).max() - 1
hb = bt.BETA * tr


def latch(on, off):
    """on 觸發後維持，直到 off 成立。"""
    st, out = False, []
    for a, b in zip(on.fillna(False), off.fillna(False)):
        if not st and a: st = True
        elif st and b: st = False
        out.append(st)
    return pd.Series(out, index=on.index)


def cap(e, cond, level):
    return e.where(~cond, np.minimum(e, level))


V = {"定版（100/50/30）": base}
# A：20 日內跌幅達門檻 → 上限 L，收盤站回月線才解除
for x in (0.08, 0.10, 0.12):
    for L in (0.3, 0.2):
        on = dd20 <= -x
        V[f"A 20日跌{int(x*100)}%→{int(L*100)}%，站回月線解除"] = cap(base, latch(on, c > ma20), L)
# B：同 A，但解除條件較快：連續 2 天站上 5 日線
for x in (0.08, 0.10):
    on = dd20 <= -x
    off = (c > f["ma5"]) & (c.shift(1) > f["ma5"].shift(1))
    V[f"B 20日跌{int(x*100)}%→30%，站上5日線2天解除"] = cap(base, latch(on, off), 0.3)
# C：連續版：季線下時，水位隨跌破季線的 ATR 倍數線性下降，5% 為單位，下限 20%
for k in (0.10, 0.15):
    dist = ((ma60 - c) / atr).clip(lower=0)
    cont = (0.5 - k * dist).clip(0.2, 0.5)
    e = base.where(base == 1, np.minimum(base, (cont / 0.05).round() * 0.05))
    V[f"C 連續：季線下每 1 ATR 減 {int(k*100)}%，下限 20%"] = e
# D：月線下彎＋跌破月線＋跌破季線（不等 3 日確認）→ 30%
on = (c < ma20) & (ma20 < ma20.shift(5)) & (c < ma60)
V["D 月線下彎且跌破季線→30%（不等確認）"] = cap(base, on, 0.3)

rows = {}
periods = {"全期": slice(*bt.DEV), "前段15-19": slice("2015-06-01", "2019-12-31"), "後段20-24": slice("2020-01-01", "2024-12-31")}
one = pd.Series(1.0, index=f.index)
for pn, sl in periods.items():
    b = bt.stats(bt.equity(one.loc[sl], tr.loc[sl], False), tr.loc[sl])
    rows[("大盤", pn)] = {"年化%": b["年化%"], "回撤%": b["最大回撤%"]}
    for n, e in V.items():
        e = e.loc[sl].fillna(1.0)
        s = bt.stats(bt.equity(e, hb.loc[sl]), tr.loc[sl], e)
        rows[(n, pn)] = {"年化%": s["年化%"], "回撤%": s["最大回撤%"], "年調整": s["年調整次數"], "平均水位%": s["平均水位%"]}
t = pd.DataFrame(rows).T
pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 200)
print(t.round(1).to_string())

# 各次急跌：從高點到之後 60 日的表現
eps = {"2015/8": ("2015-07-20", "2015-11-30"), "2018/10": ("2018-09-25", "2019-01-31"), "2020/3": ("2020-02-15", "2020-06-30"),
       "2022": ("2022-01-01", "2022-12-31"), "2024/8": ("2024-07-10", "2024-10-31")}
sel = ["定版（100/50/30）", "A 20日跌10%→30%，站回月線解除", "A 20日跌8%→30%，站回月線解除", "B 20日跌10%→30%，站上5日線2天解除",
       "C 連續：季線下每 1 ATR 減 15%，下限 20%", "D 月線下彎且跌破季線→30%（不等確認）"]
rr = {}
for en, (a, b_) in eps.items():
    sl = slice(a, b_)
    rr[(en, "大盤")] = ((1 + tr.loc[sl]).prod() - 1) * 100
    for n in sel:
        e = V[n].loc[sl].fillna(1.0)
        rr[(en, n)] = (bt.equity(e, hb.loc[sl]).iloc[-1] - 1) * 100
print("\n各次急跌期間報酬%（1.5×加權）"); print(pd.Series(rr).unstack(0).round(1).to_string())

# 參考（驗收期已用過，不用來挑選）：2025/1 起
sl = slice("2025-01-01", None)
for n in ["定版（100/50/30）", "A 20日跌10%→30%，站回月線解除"]:
    e = V[n].loc[sl].fillna(1.0); s = bt.stats(bt.equity(e, hb.loc[sl]), tr.loc[sl], e)
    print("驗收期參考", n, round(s["年化%"], 1), round(s["最大回撤%"], 1))
e = V["A 20日跌10%→30%，站回月線解除"]
on = latch(dd20 <= -0.10, c > ma20)
ch = on.astype(int).diff().fillna(0)
print("煞車啟動／解除：", [(d.date(), "啟動" if v > 0 else "解除", round(c[d])) for d, v in ch[ch != 0].loc["2015-06-01":].items()])
