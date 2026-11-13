"""
定版規則（v3）：季線＋斜率＋3 日確認

  季線上狀態 → 100%
  季線下狀態 → 50%；季線同時下彎 → 30%
  狀態切換：連續 3 個交易日收在季線另一側才切換（過濾假跌破、假突破）
  季線下彎：季線近 10 日的變動 < −0.5 倍 ATR(14)

只需要加權指數的日高、低、收。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_LEN, N_CONFIRM, SLOPE_DAYS, SLOPE_CUT = 60, 3, 10, -0.5
LEVELS = {"above": 100, "below": 50, "below_down": 30}


def atr(high, low, close, n=14):
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rule(close: pd.Series, high: pd.Series, low: pd.Series, ma_len=MA_LEN, n=N_CONFIRM) -> pd.DataFrame:
    """回傳每日：ma、slope（ATR 倍數）、below（收盤在季線下）、state（確認後的季線下狀態）、
    streak（目前連續幾天在季線同一側）、expo（建議水位 %）。"""
    ma = close.rolling(ma_len).mean()
    slope = (ma - ma.shift(SLOPE_DAYS)) / atr(high, low, close)
    c, m = close.values, ma.values
    st, cb, ca = False, 0, 0
    state, streak = np.zeros(len(c), bool), np.zeros(len(c), int)
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        b = c[i] < m[i]
        cb, ca = (cb + 1, 0) if b else (0, ca + 1)
        if not st and cb >= n:
            st = True
        elif st and ca >= n:
            st = False
        state[i], streak[i] = st, cb if b else ca
    out = pd.DataFrame({"ma": ma, "slope": slope, "below": close < ma, "state": state, "streak": streak}, index=close.index)
    down = slope < SLOPE_CUT
    out["expo"] = np.where(out["state"] & down, LEVELS["below_down"], np.where(out["state"], LEVELS["below"], LEVELS["above"]))
    out.loc[ma.isna(), "expo"] = np.nan
    return out
