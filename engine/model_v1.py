"""
v1 模型：預設 100%，風險訊號「同時出現幾個」才降倉；洗盤後設水位下限以利回補。

風險旗標（邏輯先定，不訓練）：
  R1 收盤跌破季線            R2 季線下彎             R3 收盤跌破月線
  R4 10 日上漲家數比例 < 45%  R5 櫃買 20 日落後加權扣台積電 3% 以上
  R6 融資增、指數跌（次日可用） R7 外資台指期淨空且續增（次日可用）
  R8 費半跌破 200 日線
洗盤觸發（弱勢中）：跌停家數暴增、融資單日減 2.5% 以上（次日可用）、或爆量重挫。

訓練的參數 6 個：
  a     幾個旗標開始降倉          s   每多幾個旗標再降一級
  gate  是否要求先跌破季線才降倉   N   加碼確認天數（降倉立即、加碼要連續 N 天達標）
  W     洗盤後幾天內有效          floor 洗盤後「站上 5 日線」時的水位下限
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

LEVELS = np.array([1.0, 0.7, 0.5, 0.3, 0.0])
FLAG_NAMES = {
    "R1": "收盤跌破季線", "R2": "季線下彎", "R3": "收盤跌破月線", "R4": "上漲家數比例偏低",
    "R5": "櫃買落後權值", "R6": "融資增、指數跌", "R7": "外資期貨淨空續增", "R8": "費半跌破 200 日線",
}
GRID = [dict(a=a, s=s, gate=g, N=N, W=W, floor=fl)
        for a, s, g, N in itertools.product([2, 3, 4, 5], [1, 2], [0, 1], [1, 3, 5])
        for W, fl in [(0, 0.0)] + list(itertools.product([5, 10, 20], [0.5, 0.7, 1.0]))]


def flags(f: pd.DataFrame, S: pd.DataFrame) -> pd.DataFrame:
    """S 的籌碼欄位需已延遲一天。"""
    R = pd.DataFrame(index=f.index)
    R["R1"] = f["close"] < f["ma60"]
    R["R2"] = f["ma60_slope"] < -0.5
    R["R3"] = f["close"] < f["ma20"]
    R["R4"] = f["adr10"] < 0.45 if "adr10" in f else False
    R["R5"] = f["rel20"] < -0.03 if "rel20" in f else False
    R["R6"] = S["C1"] == -1 if "C1" in S else False
    R["R7"] = S["C4"] == -1 if "C4" in S else False
    R["R8"] = f["sox"] < f["sox"].rolling(200, min_periods=150).mean() if "sox" in f else False
    return R.fillna(False).astype(bool)


def washout(f: pd.DataFrame) -> pd.DataFrame:
    weak = f["close"] < f["ma60"]
    W = pd.DataFrame(index=f.index)
    W["跌停家數暴增"] = weak & (f.get("limit_down_z", pd.Series(0, index=f.index)) > 3)
    m1 = f["margin"].pct_change() if "margin" in f else pd.Series(0, index=f.index)
    W["融資單日大減"] = (weak & (m1 < -0.025)).shift(1, fill_value=False)   # 盤後公布
    W["爆量重挫"] = weak & (f["vol_z"] > 2) & (f["close"].pct_change() < -0.025)
    return W.fillna(False).astype(bool)


def exposure(R: pd.DataFrame, Wt: pd.DataFrame, p: dict, stable: pd.Series) -> pd.Series:
    """stable：收盤站上 5 日線（洗盤後的止跌確認）。"""
    K = R.sum(axis=1)
    if p["gate"]:
        K = K.where(R["R1"], 0)
    idx = np.clip(np.floor((K - p["a"]) / p["s"]) + 1, 0, 4).astype(int)
    e = pd.Series(LEVELS[idx], index=R.index)
    if p["N"] > 1:  # 降倉立即執行；加碼需連續 N 天都達到該水位
        e = e.rolling(p["N"], min_periods=1).min()
    if p["W"]:
        active = Wt.any(axis=1).rolling(p["W"], min_periods=1).max().astype(bool) & stable
        e = e.where(~active, np.maximum(e, p["floor"]))
    return e
