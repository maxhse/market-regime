"""反彈碰均線不過的事件研究。加權指數日線（data/public/twii.csv）。"""
import pandas as pd, numpy as np
tw = pd.read_csv("data/public/twii.csv", parse_dates=["date"]).set_index("date")
tw = tw[~tw.index.duplicated()].sort_index()
c, h, l = tw["close"], tw["high"], tw["low"]
fwd = {k: c.shift(-k) / c - 1 for k in (5, 10, 20)}
fmin10 = pd.concat([l.shift(-k) / c - 1 for k in range(1, 11)], axis=1).min(axis=1)
low20 = l.rolling(20).min()                                  # 反彈前的低點（近 20 日最低）
newlow = pd.concat([l.shift(-k) for k in range(1, 11)], axis=1).min(axis=1) < low20   # 10 日內跌破前低


def study(n, k, tol, sl):
    ma = c.rolling(n).mean()
    down = ma < ma.shift(5)                                  # 均線下彎（5 日）
    weak = (c < ma) & down
    touch = weak & (h >= ma * (1 - tol))                     # 盤中碰到（或差 tol 以內），收盤仍在下方
    run = touch.astype(int).groupby((~touch).cumsum()).cumsum()
    ev = run == k                                            # 第 k 天連續碰到時出訊號（每段只算一次）
    ev, weak_ = ev.loc[sl], weak.loc[sl]
    ctrl = weak_ & ~touch.loc[sl]
    def s(m):
        m = m & fwd[20].loc[sl].notna()
        return {"次數": int(m.sum()), "5日%": fwd[5].loc[sl][m].mean() * 100, "10日%": fwd[10].loc[sl][m].mean() * 100,
                "20日%": fwd[20].loc[sl][m].mean() * 100, "10日下跌機率%": (fwd[10].loc[sl][m] < 0).mean() * 100,
                "10日內跌破前低%": newlow.loc[sl][m].mean() * 100, "10日內最深%": fmin10.loc[sl][m].mean() * 100}
    return s(ev), s(ctrl), ev


pd.set_option("display.width", 250, "display.unicode.east_asian_width", True, "display.max_rows", 200)
for lab, sl in (("開發期 2015/6–2024/12", slice("2015-06-01", "2024-12-31")), ("全期 2014–2026", slice("2014-03-01", None))):
    rows = {}
    for n in (5, 10, 20, 40, 60):
        for k in (1, 2, 3):
            for tol in (0.0, 0.005):
                e, ctl, _ = study(n, k, tol, sl)
                rows[(f"{n}日線", k, f"{tol*100:.1f}%")] = {**{f"事件{x}": v for x, v in e.items()},
                                                          **{f"對照{x}": v for x, v in ctl.items() if x != "次數"}}
    t = pd.DataFrame(rows).T
    t.index.names = ["均線", "連續天數", "容許差距"]
    print(f"\n===== {lab} =====")
    print(t[["事件次數", "事件5日%", "事件10日%", "事件20日%", "事件10日下跌機率%", "事件10日內跌破前低%", "事件10日內最深%",
             "對照10日%", "對照10日下跌機率%", "對照10日內跌破前低%", "對照10日內最深%"]].round(1).to_string())

# 你提到的兩段：列出 20 日線、連續 2 天的訊號日
_, _, ev = study(20, 2, 0.0, slice("2026-01-01", None))
print("\n2026 年 20 日線連續 2 天碰到不過：")
for d in ev[ev].index:
    print(f"  {d.date()} 收 {c[d]:,.0f}  之後 5 日 {fwd[5][d]*100:+.1f}%  10 日 {fwd[10][d]*100:+.1f}%" if pd.notna(fwd[10][d]) else f"  {d.date()} 收 {c[d]:,.0f}（之後資料不足）")
