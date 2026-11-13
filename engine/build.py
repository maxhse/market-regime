"""
持股水位建議引擎（v0 初版規則）

讀取 data/raw/*.csv → 計算指標與訊號 → 建議水位 → 產生明日劇本 → 輸出 docs/latest.json

注意：v0 的權重與門檻是依交易邏輯先設的初值，尚未經過滾動回測驗證。
回測階段會用 2015/6–2024/12 開發期重新訓練，2025 起為最終驗收期。
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from rule import LEVELS as RULE, N_CONFIRM, rule as apply_rule  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "raw"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "latest.json"

MODEL_VERSION = "v3 季線＋斜率＋3 日確認"
MODEL_NOTE = ("水位只由加權指數的季線決定：季線上 100%、季線下 50%、季線下且季線下彎 30%；連續 3 天收在季線另一側才切換。"
              "回測以 1.5 倍加權報酬指數模擬高 beta 持股、扣現股交易成本："
              "開發期（2015/6–2024/12）年化 15.6%、最大回撤 -25.2%（大盤 13.8%、-28.6%）；"
              "最終驗收（2025/1–2026/11，只跑一次）年化 68.1%、最大回撤 -22.2%（大盤 46.5%、-26.5%）。"
              "下方盤勢解讀的訊號不影響水位。")
TIERS = [0, 30, 50, 70, 100]
# 總分門檻：分數 >= 門檻 → 對應水位（由高到低）
TIER_CUTS = [(0.40, 100), (0.10, 70), (-0.20, 50), (-0.50, 30)]
HYSTERESIS = 0.05
CAT_WEIGHTS = {"趨勢": 0.35, "籌碼": 0.25, "廣度": 0.15, "恐慌": 0.10, "外部": 0.15}

FILES = {
    "taiex": (["open", "high", "low", "close", "turnover"], "加權指數 OHLC、成交金額", True),
    "tpex": (["open", "high", "low", "close", "turnover"], "櫃買指數 OHLC、成交金額", True),
    "taiex_tr": (["close"], "加權報酬指數", True),
    "tpex_tr": (["close"], "櫃買報酬指數", False),
    "tsmc": (["close"], "台積電收盤價（還原）", True),
    "tsmc_mktcap": (["mktcap"], "台積電市值（扣台積電用）", False),
    "margin": (["twse_margin", "twse_short", "tpex_margin", "tpex_short"], "上市櫃融資融券餘額", True),
    "mktcap": (["twse_mktcap", "tpex_mktcap"], "上市櫃總市值", True),
    "inst": (["foreign_net", "trust_net"], "上市外資、投信買賣超", True),
    "futures": (["foreign_tx_net_oi", "pc_ratio"], "外資台指期淨未平倉、P/C ratio", True),
    "breadth": (["up", "down", "limit_up", "limit_down", "above_ma60", "new_high", "new_low"], "全市場漲跌家數、漲跌停、廣度", True),
    "external": (["usdtwd", "sox"], "台幣匯率、費半指數", True),
    "stock_margin": (["stock_id", "margin_shares", "close"], "個股融資張數（維持率）", False),
    "stock_close": (["stock_id", "close"], "個股收盤價（含下市）", False),
}


# ---------- 資料讀取 ----------
def load_all():
    data, status = {}, []
    for name, (cols, label, required) in FILES.items():
        path = RAW / f"{name}.csv"
        item = {"file": f"{name}.csv", "label": label, "required": required,
                "present": False, "rows": 0, "first": None, "last": None, "missing_cols": []}
        if path.exists():
            try:
                df = pd.read_csv(path)
                df.columns = [c.strip().lower() for c in df.columns]
                df["date"] = pd.to_datetime(df["date"])
                miss = [c for c in cols if c not in df.columns]
                item["missing_cols"] = miss
                if name not in ("stock_margin", "stock_close"):
                    df = df.drop_duplicates("date").set_index("date").sort_index()
                    for c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    item["first"], item["last"] = str(df.index[0].date()), str(df.index[-1].date())
                else:
                    item["first"], item["last"] = str(df["date"].min().date()), str(df["date"].max().date())
                item["present"], item["rows"] = True, int(len(df))
                data[name] = df
            except Exception as e:  # noqa: BLE001
                item["error"] = str(e)
        status.append(item)
    # 費半（公開資料，Actions 抓取）：美股 d 日收盤要到台股 d+1 日才知道，所以日期往後移一天再對齊
    pub = ROOT / "data" / "public" / "sox.csv"
    if pub.exists() and "external" in data:
        sx = pd.read_csv(pub, parse_dates=["date"]).set_index("date")["sox"]
        sx.index = sx.index + pd.Timedelta(days=1)
        ext = data["external"]
        ext["sox"] = sx.reindex(ext.index.union(sx.index)).ffill().reindex(ext.index)
        for it in status:
            if it["file"] == "external.csv":
                it["missing_cols"] = [c for c in it["missing_cols"] if c != "sox"]
    return data, status


def col(data, name, c, idx):
    """取某檔某欄，對齊到加權指數交易日；缺檔或缺欄回傳 None。"""
    df = data.get(name)
    if df is None or c not in df.columns or df[c].notna().sum() == 0:
        return None
    return df[c].reindex(idx).ffill(limit=3)


# ---------- 工具 ----------
def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def zscore(s, n=250):
    return (s - s.rolling(n, min_periods=60).mean()) / s.rolling(n, min_periods=60).std()


def pctrank(s, n=250):
    return s.rolling(n, min_periods=60).apply(lambda x: (x[-1] >= x).mean(), raw=True)


def sig(cat, sid, name, score, detail, value=None):
    return {"cat": cat, "id": sid, "name": name, "score": float(score),
            "state": "bull" if score > 0 else "bear" if score < 0 else "neutral",
            "detail": detail, "value": value}


def fnum(x, d=0):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:,.{d}f}"


# ---------- 特徵與每日訊號分數（時間序列，供歷史水位與劇本統計） ----------
def build_features(data):
    tx = data["taiex"].copy()
    idx = tx.index
    f = pd.DataFrame(index=idx)
    c = tx["close"]
    f["close"], f["open"], f["high"], f["low"] = c, tx["open"], tx["high"], tx["low"]
    f["turnover"] = tx["turnover"]
    f["atr"] = atr(tx)
    for n in (5, 10, 20, 60):
        f[f"ma{n}"] = c.rolling(n).mean()
    f["ma60_slope"] = (f["ma60"] - f["ma60"].shift(10)) / f["atr"]
    f["ma5_slope"] = f["ma5"] - f["ma5"].shift(1)
    f["low20"] = c.rolling(20).min()
    f["high60"] = c.rolling(60).max()
    f["vol_z"] = zscore(np.log(tx["turnover"].replace(0, np.nan)))
    body = (tx["close"] - tx["open"]).abs()
    lower = tx[["open", "close"]].min(axis=1) - tx["low"]
    f["long_lower"] = (lower > 2 * body) & (lower > 0.6 * f["atr"])
    f["ret5"] = c.pct_change(5)
    f["ret20"] = c.pct_change(20)

    S = pd.DataFrame(index=idx)  # 各訊號每日分數
    # 趨勢
    S["T1"] = np.where(c > f["ma60"], 1.0, -1.0)
    S["T2"] = np.select([f["ma60_slope"] > 0.5, f["ma60_slope"] < -0.5], [1.0, -1.0], 0.0)
    S["T3"] = np.where(c > f["ma20"], 1.0, -1.0)

    # 籌碼
    m = col(data, "margin", "twse_margin", idx)
    if m is not None:
        f["margin"] = m
        f["margin_chg5"] = m.pct_change(5)
        up_m, dn_m = f["margin_chg5"] > 0.01, f["margin_chg5"] < -0.01
        up_i, dn_i = f["ret5"] > 0, f["ret5"] <= 0
        S["C1"] = np.select([up_m & dn_i, up_m & up_i, dn_m & up_i, dn_m & dn_i], [-1.0, -0.5, 1.0, 0.0], 0.0)
        mc = col(data, "mktcap", "twse_mktcap", idx)
        if mc is not None:
            f["margin_ratio_pct"] = pctrank(m / mc)
            S["C2"] = np.select([f["margin_ratio_pct"] > 0.9, f["margin_ratio_pct"] < 0.2], [-1.0, 0.5], 0.0)
    fn = col(data, "inst", "foreign_net", idx)
    if fn is not None:
        f["foreign_flow"] = fn.rolling(10).sum() / tx["turnover"].rolling(10).sum()
        f["foreign_flow_z"] = zscore(f["foreign_flow"])
        S["C3"] = np.select([f["foreign_flow_z"] > 1, f["foreign_flow_z"] < -1], [1.0, -1.0], 0.0)
    fo = col(data, "futures", "foreign_tx_net_oi", idx)
    if fo is not None:
        f["fut_oi"], f["fut_oi_chg5"] = fo, fo - fo.shift(5)
        S["C4"] = np.select([(fo > 0) & (f["fut_oi_chg5"] > 0), (fo < 0) & (f["fut_oi_chg5"] < 0)], [1.0, -1.0], 0.0)

    # 廣度
    up, dn = col(data, "breadth", "up", idx), col(data, "breadth", "down", idx)
    if up is not None and dn is not None:
        f["adr10"] = up.rolling(10).sum() / (up + dn).rolling(10).sum()
        S["B1"] = np.select([f["adr10"] > 0.55, f["adr10"] < 0.45], [1.0, -1.0], 0.0)
    tp = data.get("tpex")
    if tp is not None:
        tpc = tp["close"].reindex(idx).ffill(limit=3)
        base_ret = c.pct_change()
        ts_c, ts_mc, mc = col(data, "tsmc", "close", idx), col(data, "tsmc_mktcap", "mktcap", idx), col(data, "mktcap", "twse_mktcap", idx)
        f["base_label"] = "加權指數"
        if ts_c is not None and ts_mc is not None and mc is not None:
            w = (ts_mc / mc).shift(1).clip(0, 0.8)  # 前一日台積電權重（市值單位皆為億）
            base_ret = (c.pct_change() - w * ts_c.pct_change()) / (1 - w)
            f["base_label"] = "加權扣台積電"
        base = (1 + base_ret.fillna(0)).cumprod()
        f["rel20"] = tpc.pct_change(20) - base.pct_change(20)
        S["B2"] = np.select([f["rel20"] > 0.03, f["rel20"] < -0.03], [0.5, -1.0], 0.0)

    # 恐慌（反向指標：只在弱勢中視為止跌加分）
    weak = c < f["ma60"]
    S["P1"] = np.where(weak & (f["vol_z"] > 2), 1.0, 0.0)
    S["P2"] = np.where(weak & f["long_lower"] & (f["vol_z"] > 1), 1.0, 0.0)
    ld = col(data, "breadth", "limit_down", idx)
    if ld is not None:
        f["limit_down"], f["limit_down_z"] = ld, zscore(ld)
        S["P3"] = np.where(weak & (f["limit_down_z"] > 3), 1.0, 0.0)
    pc = col(data, "futures", "pc_ratio", idx)
    if pc is not None:
        f["pc"], f["pc_pct"] = pc, pctrank(pc)

    # 外部
    sox = col(data, "external", "sox", idx)
    if sox is not None:
        f["sox"], f["sox_ma60"] = sox, sox.rolling(60).mean()
        S["E1"] = np.where(sox > f["sox_ma60"], 1.0, -1.0)
    fx = col(data, "external", "usdtwd", idx)
    if fx is not None:
        f["fx"], f["fx_chg20"] = fx, fx.pct_change(20)
        S["E2"] = np.select([f["fx_chg20"] < -0.015, f["fx_chg20"] > 0.015], [0.5, -0.5], 0.0)

    return f, S


SIGNAL_CAT = {"T": "趨勢", "C": "籌碼", "B": "廣度", "P": "恐慌", "E": "外部"}


def total_score(S):
    cats = {}
    for cat in CAT_WEIGHTS:
        cols = [k for k in S.columns if SIGNAL_CAT[k[0]] == cat]
        if cols:
            sub = S[cols]
            if cat == "恐慌":  # 恐慌只加分不扣分，取最大值
                cats[cat] = sub.max(axis=1)
            else:
                cats[cat] = sub.mean(axis=1)
    C = pd.DataFrame(cats)
    w = pd.Series({k: CAT_WEIGHTS[k] for k in C.columns})
    return (C * w).sum(axis=1) / w.sum(), C


def to_tiers(score):
    tiers, cur = [], None
    for s in score:
        if pd.isna(s):
            tiers.append(np.nan)
            continue
        target = next((t for cut, t in TIER_CUTS if s >= cut), 0)
        lower = {t: cut for cut, t in TIER_CUTS} | {0: -9.0}
        if cur is None:
            cur = target
        elif target > cur:  # 緩衝：分數要越過門檻一段距離才升級
            if s >= lower[target] + HYSTERESIS:
                cur = target
        elif target < cur and s < lower[cur] - HYSTERESIS:
            cur = target
        tiers.append(cur)
    return pd.Series(tiers, index=score.index)


def regime_label(score):
    if score >= 0.4: return "多頭"
    if score >= 0.1: return "偏多"
    if score >= -0.2: return "盤整"
    if score >= -0.5: return "偏空"
    return "空頭"


# ---------- 今日訊號說明 ----------
def describe_today(f, S):
    r, out = f.iloc[-1], []
    t = S.iloc[-1]
    slope_txt = "上彎" if r["ma60_slope"] > 0.5 else "下彎" if r["ma60_slope"] < -0.5 else "走平"
    out.append(sig("趨勢", "T1", "季線位置", t["T1"],
                   f"收盤 {fnum(r.close)} {'站上' if t['T1'] > 0 else '跌破'}季線 {fnum(r.ma60)}，距離 {fnum((r.close - r.ma60) / r.atr, 1)} 倍 ATR"))
    out.append(sig("趨勢", "T2", "季線斜率", t["T2"], f"季線{slope_txt}（10 日斜率 {fnum(r.ma60_slope, 2)} ATR）"))
    out.append(sig("趨勢", "T3", "月線位置", t["T3"], f"收盤{'站上' if t['T3'] > 0 else '跌破'}月線 {fnum(r.ma20)}"))
    if "C1" in t:
        q = {-1.0: "融資增、指數跌：散戶接刀", -0.5: "融資增、指數漲：散戶追價", 1.0: "融資減、指數漲：籌碼沉澱", 0.0: "融資減、指數跌：洗籌碼／斷頭，留意底部"}
        txt = q.get(t["C1"], "融資變化不大") if abs(r.margin_chg5) > 0.01 else "融資 5 日變化在 ±1% 內"
        out.append(sig("籌碼", "C1", "融資 vs 指數", t["C1"], f"{txt}（融資 5 日 {fnum(r.margin_chg5 * 100, 1)}%、指數 5 日 {fnum(r.ret5 * 100, 1)}%）"))
    if "C2" in t:
        out.append(sig("籌碼", "C2", "融資占市值", t["C2"], f"位於近一年 {fnum(r.margin_ratio_pct * 100)} 百分位"))
    if "C3" in t:
        out.append(sig("籌碼", "C3", "外資現貨 10 日", t["C3"], f"10 日累積買賣超占成交額 {fnum(r.foreign_flow * 100, 2)}%（z={fnum(r.foreign_flow_z, 1)}）"))
    if "C4" in t:
        out.append(sig("籌碼", "C4", "外資台指期", t["C4"], f"淨未平倉 {fnum(r.fut_oi)} 口，5 日變化 {fnum(r.fut_oi_chg5)} 口"))
    if "B1" in t:
        out.append(sig("廣度", "B1", "漲跌家數", t["B1"], f"10 日上漲家數比例 {fnum(r.adr10 * 100, 1)}%"))
    if "B2" in t:
        out.append(sig("廣度", "B2", "櫃買 vs 權值", t["B2"], f"櫃買 20 日相對{r.base_label} {fnum(r.rel20 * 100, 1)}%"))
    out.append(sig("恐慌", "P1", "恐慌爆量", t["P1"], f"成交金額 z={fnum(r.vol_z, 1)}" + ("，弱勢中爆量" if t["P1"] > 0 else "")))
    out.append(sig("恐慌", "P2", "爆量長下影線", t["P2"], "今日出現" if t["P2"] > 0 else "未出現"))
    if "P3" in t:
        out.append(sig("恐慌", "P3", "跌停家數", t["P3"], f"上市櫃跌停 {fnum(r.limit_down)} 家（z={fnum(r.limit_down_z, 1)}）"))
    if "pc" in f:
        out.append(sig("恐慌", "P4", "P/C ratio", 0, f"{fnum(r.pc, 2)}，近一年 {fnum(r.pc_pct * 100)} 百分位（方向待回測，暫不計分）"))
    if "E1" in t:
        out.append(sig("外部", "E1", "費半季線", t["E1"], f"費半 {fnum(r.sox)} {'站上' if t['E1'] > 0 else '跌破'} 60 日線 {fnum(r.sox_ma60)}"))
    if "E2" in t:
        out.append(sig("外部", "E2", "台幣 20 日", t["E2"], f"USD/TWD 20 日 {fnum(r.fx_chg20 * 100, 1)}%（{'升值' if r.fx_chg20 < 0 else '貶值'}）"))
    return out


# ---------- 劇本與歷史統計 ----------
def event_stats(event, fwd_base):
    ev = event.fillna(False).astype(bool)
    hit = ev.tail(5)
    recent = str(hit[hit].index[-1].date()) if hit.any() else None  # 近 5 日已觸發
    # 去除連續觸發：5 日內只算第一次
    ev = ev & ~ev.shift(1, fill_value=False).rolling(5, min_periods=1).max().astype(bool)
    r5 = fwd_base.shift(-5) / fwd_base - 1
    r20 = fwd_base.shift(-20) / fwd_base - 1
    a5, a20 = r5[ev].dropna(), r20[ev].dropna()
    if len(a20) == 0:
        return {"n": int(ev.sum()), "reliable": False, "recent": recent}
    return {
        "n": int(len(a20)), "reliable": bool(len(a20) >= 10), "recent": recent,
        "up5": round(float((a5 > 0).mean()) * 100), "up20": round(float((a20 > 0).mean()) * 100),
        "avg20": round(float(a20.mean()) * 100, 1),
        "fail20": round(float(a20[a20 <= 0].mean()) * 100, 1) if (a20 <= 0).any() else 0.0,
    }


def close_needed(c, n):
    """明日收盤要站上（或跌破）明日 n 日均線的價位 = 最近 n-1 日收盤平均。"""
    return float(c.iloc[-(n - 1):].mean())


def scenarios_v3(f, R, fwd_base):
    c, r, rr = f["close"], f.iloc[-1], R.iloc[-1]
    lv5, lv20, lv60 = close_needed(c, 5), close_needed(c, 20), close_needed(c, 60)
    state, below = R["state"], R["below"]
    slope = "下彎" if rr.slope < -0.5 else "上彎" if rr.slope > 0.5 else "走平"
    left = max(N_CONFIRM - int(rr.streak), 1)
    out = []
    if not rr.state:
        prog = f"目前已連續 {int(rr.streak)} 天收在季線下，再 {left} 天就成立" if rr.below else "目前收在季線上"
        tgt = RULE["below_down"] if slope == "下彎" else RULE["below"]
        ev = state & ~state.shift(1, fill_value=False)
        out.append({"key": "A", "name": "轉弱", "tone": "bear",
                    "cond": f"連續 {N_CONFIRM} 天收在季線下（季線明日約 {fnum(lv60)}）。{prog}",
                    "meaning": f"中期趨勢轉弱，季線目前{slope}", "action": f"水位降到 {tgt}%", "stats": event_stats(ev, fwd_base)})
        ev = (c < f["ma20"]) & (c.shift(1) >= f["ma20"].shift(1)) & ~below
        out.append({"key": "B", "name": "警戒", "tone": "neutral", "cond": f"收盤跌破月線（約 {fnum(lv20)}）",
                    "meaning": "短線轉弱，季線仍在下方支撐", "action": "水位不變，留意是否續跌破季線", "stats": event_stats(ev, fwd_base)})
        if "above_ma60" in f and f["above_ma60"].notna().any():
            ev = (c >= c.rolling(60).max()) & (f["above_ma60"] < 0.5)
            out.append({"key": "C", "name": "廣度背離", "tone": "neutral", "cond": "指數創 60 日新高，但站上 60 日線的個股不到一半",
                        "meaning": "漲勢集中在少數個股，之後容易停滯", "action": "水位不變，不建議加槓桿", "stats": event_stats(ev, fwd_base)})
    else:
        prog = f"目前已連續 {int(rr.streak)} 天收在季線上，再 {left} 天就成立" if not rr.below else f"目前已連續 {int(rr.streak)} 天收在季線下"
        ev = ~state & state.shift(1, fill_value=False)
        out.append({"key": "A", "name": "轉強", "tone": "bull",
                    "cond": f"連續 {N_CONFIRM} 天收在季線上（季線明日約 {fnum(lv60)}）。{prog}",
                    "meaning": "中期趨勢恢復", "action": f"水位回到 {RULE['above']}%", "stats": event_stats(ev, fwd_base)})
        down = R["slope"] < -0.5
        if slope != "下彎":
            ev = state & down & ~down.shift(1, fill_value=False)
            out.append({"key": "B", "name": "續弱", "tone": "bear", "cond": "仍在季線下，且季線由走平轉為下彎",
                        "meaning": "由回檔轉為趨勢性下跌", "action": f"水位降到 {RULE['below_down']}%", "stats": event_stats(ev, fwd_base)})
        else:
            ev = state & ~down & down.shift(1, fill_value=False)
            out.append({"key": "B", "name": "趨緩", "tone": "bull", "cond": "季線由下彎轉為走平",
                        "meaning": "跌勢趨緩", "action": f"水位回到 {RULE['below']}%", "stats": event_stats(ev, fwd_base)})
        ev = below & (c > f["ma5"]) & (c.shift(1) <= f["ma5"].shift(1)) & (f["ma5_slope"].shift(1) < 0)
        out.append({"key": "C", "name": "止跌跡象", "tone": "neutral", "cond": f"爆量長下影線，或收盤站上下彎的 5 日線（約 {fnum(lv5)}）",
                    "meaning": "回測顯示這類訊號之後的表現沒有比平常好", "action": "水位不變，等站回季線", "stats": event_stats(ev, fwd_base)})
    return out, {"ma5": lv5, "ma20": lv20, "ma60": lv60}, slope


def scenarios(f, tier, fwd_base):
    c, r = f["close"], f.iloc[-1]
    lv5, lv20, lv60 = close_needed(c, 5), close_needed(c, 20), close_needed(c, 60)
    up_tier = lambda k: TIERS[min(TIERS.index(tier) + k, len(TIERS) - 1)]
    dn_tier = lambda k: TIERS[max(TIERS.index(tier) - k, 0)]
    weak = c < f["ma60"]
    out = []
    if tier <= 50:
        ma5_dn = r.ma5_slope < 0
        ev = weak & (((c > f["ma5"]) & (c.shift(1) <= f["ma5"].shift(1)) & (f["ma5_slope"].shift(1) < 0))
                     | (f["long_lower"] & (f["vol_z"] > 1)))
        out.append({"key": "A", "name": "止跌", "tone": "bull",
                    "cond": f"爆量長下影線，或收盤站上{'下彎的' if ma5_dn else ''} 5 日線（約 {fnum(lv5)}）",
                    "meaning": "短線止跌訊號", "action": f"水位可提高到 {up_tier(1)}%",
                    "stats": event_stats(ev, fwd_base)})
        ev = weak & (c > f["ma20"]) & (c.shift(1) <= f["ma20"].shift(1))
        if "margin_chg5" in f:
            ev = ev & (f["margin_chg5"] < 0.01)
        out.append({"key": "B", "name": "轉強", "tone": "bull",
                    "cond": f"收盤站上月線（約 {fnum(lv20)}），且融資 5 日未明顯增加",
                    "meaning": "反彈有籌碼支撐", "action": f"水位可提高到 {up_tier(2)}%",
                    "stats": event_stats(ev, fwd_base)})
        low20 = float(c.iloc[-19:].min())
        ev = c < c.shift(1).rolling(19).min()
        out.append({"key": "C", "name": "續弱", "tone": "bear",
                    "cond": f"收盤跌破近 20 日低點 {fnum(low20)}",
                    "meaning": "跌勢延續", "action": f"水位降到 {dn_tier(1)}%",
                    "stats": event_stats(ev & weak.shift(1, fill_value=False), fwd_base)})
    if tier >= 70:
        ev = (c < f["ma20"]) & (c.shift(1) >= f["ma20"].shift(1)) & ~weak
        out.append({"key": "D", "name": "警戒", "tone": "bear",
                    "cond": f"收盤跌破月線（約 {fnum(lv20)}）",
                    "meaning": "短線轉弱，季線仍在上方", "action": f"水位降到 {dn_tier(1)}%",
                    "stats": event_stats(ev, fwd_base)})
        ev = (c < f["ma60"]) & (c.shift(1) >= f["ma60"].shift(1))
        out.append({"key": "E", "name": "轉弱", "tone": "bear",
                    "cond": f"收盤跌破季線（約 {fnum(lv60)}），季線目前{'上彎' if r.ma60_slope > 0.5 else '下彎' if r.ma60_slope < -0.5 else '走平'}",
                    "meaning": "中期趨勢轉弱", "action": f"水位降到 {min(50, dn_tier(2))}%",
                    "stats": event_stats(ev, fwd_base)})
        if tier == 70:
            hi60 = float(c.iloc[-59:].max())
            ev = (c > c.shift(1).rolling(59).max()) & ~weak
            out.append({"key": "G", "name": "續強", "tone": "bull",
                        "cond": f"收盤創 60 日新高（高於 {fnum(hi60)}）",
                        "meaning": "趨勢延續", "action": "水位提高到 100%",
                        "stats": event_stats(ev, fwd_base)})
        if "margin_chg5" in f:
            ev = (f["margin_chg5"] > 0.03) & (f["ret5"] > 0.03)
            out.append({"key": "F", "name": "過熱", "tone": "neutral",
                        "cond": "融資 5 日增加超過 3%，且指數 5 日漲超過 3%",
                        "meaning": "散戶追價，頭部風險升高", "action": "暫不加碼，留意警戒劇本",
                        "stats": event_stats(ev, fwd_base)})
    return out, {"ma5": lv5, "ma20": lv20, "ma60": lv60}


# ---------- 主程式 ----------
def main():
    data, status = load_all()
    tw_now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    base = {"model_version": MODEL_VERSION, "model_note": MODEL_NOTE, "generated_at": tw_now, "data_status": status}

    public_mode = "taiex" not in data
    pub = ROOT / "data" / "public" / "twii.csv"
    if public_mode and pub.exists():   # 沒有 TEJ 原始檔（例如在 GitHub Actions 上）→ 用公開的指數日線
        tw = pd.read_csv(pub, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
        tw["turnover"] = np.nan
        data["taiex"] = tw
        sx = ROOT / "data" / "public" / "sox.csv"
        if sx.exists():
            s_ = pd.read_csv(sx, parse_dates=["date"]).set_index("date")["sox"]
            s_.index = s_.index + pd.Timedelta(days=1)
            data["external"] = pd.DataFrame({"sox": s_.reindex(tw.index.union(s_.index)).ffill().reindex(tw.index)})
    if "taiex" not in data or len(data["taiex"]) < 80:
        base.update({"status": "no_data", "message": "尚未放入 taiex.csv（至少需要 80 個交易日）"})
        if OUT.exists() and '"status": "ok"' in OUT.read_text(encoding="utf-8"):
            print("no data → 保留既有結果，不覆寫")
            return
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        print("no data → wrote placeholder")
        return

    f, S = build_features(data)
    score, cats = total_score(S)
    tx_ = data["taiex"]
    R = apply_rule(tx_["close"], tx_["high"], tx_["low"]).reindex(f.index)
    tiers = R["expo"]
    if "breadth" in data:
        f["above_ma60"] = data["breadth"]["above_ma60"].reindex(f.index)
    fwd_base = data["taiex_tr"]["close"].reindex(f.index).ffill() if "taiex_tr" in data else f["close"]

    tier, sc = int(tiers.iloc[-1]), float(score.iloc[-1])
    prev = int(tiers.iloc[-2]) if not pd.isna(tiers.iloc[-2]) else tier
    scen, levels, slope_lab = scenarios_v3(f, R, fwd_base)
    sigs, catd = describe_today(f, S), {k: round(float(v), 2) for k, v in cats.iloc[-1].dropna().items()}
    ctx_path = OUT.parent / "context.json"
    PRICE_CATS = ("趨勢", "外部")
    if not public_mode:   # 有完整資料：把籌碼／廣度／恐慌的解讀另存，供公開資料模式沿用
        ctx_path.write_text(json.dumps({"as_of": str(f.index[-1].date()),
                                        "signals": [x for x in sigs if x["cat"] not in PRICE_CATS],
                                        "categories": {k: v for k, v in catd.items() if k not in PRICE_CATS}},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
        ctx_date = None
    else:
        sigs = [x for x in sigs if x["cat"] in PRICE_CATS]
        catd = {k: v for k, v in catd.items() if k in PRICE_CATS}
        ctx_date = None
        if ctx_path.exists():
            ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
            sigs += ctx["signals"]; catd.update(ctx["categories"]); ctx_date = ctx["as_of"]

    hist = pd.DataFrame({"close": f["close"], "tier": tiers, "score": score}).dropna().tail(500)
    base.update({
        "status": "ok",
        "as_of": str(f.index[-1].date()),
        "close": float(f["close"].iloc[-1]),
        "change_pct": float(f["close"].pct_change().iloc[-1] * 100),
        "exposure": tier, "prev_exposure": prev,
        "regime": {100: "季線之上", 50: "跌破季線", 30: "跌破季線且下彎"}[tier], "score": round(sc, 3),
        "rule": {"ma60": round(float(R["ma"].iloc[-1]), 0), "slope": slope_lab, "streak": int(R["streak"].iloc[-1]),
                 "side": "下" if bool(R["below"].iloc[-1]) else "上"},
        "context_date": ctx_date,
        "categories": catd,
        "signals": sigs,
        "scenarios": scen,
        "levels": {k: round(v, 0) for k, v in levels.items()},
        "history": [{"d": str(d.date()), "c": round(float(row.close), 1), "t": int(row.tier), "s": round(float(row.score), 3)}
                    for d, row in hist.iterrows()],
        "stats_note": "劇本統計為 2014 年起每次觸發後的實際走勢，5 日內重複觸發只算一次；樣本少於 10 次僅供參考。",
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(base, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"{base['as_of']}  水位 {tier}%  {base['regime']}  score={sc:.3f}")


if __name__ == "__main__":
    main()
