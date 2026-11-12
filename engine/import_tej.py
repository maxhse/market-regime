"""
TEJ 匯出檔（Tab 分隔 txt）→ data/raw/*.csv

用法：python engine/import_tej.py <txt 檔或資料夾> [...]
依「表頭欄位」自動辨識是哪一種資料，不依檔名。
單位統一換成「億元」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

TWSE, TPEX, TR, TSMC = "Y9999", "OTC99", "Y9997", "2330"


def read_tej(path: Path) -> pd.DataFrame:
    enc = "utf-16" if path.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    df = pd.read_csv(path, sep="\t", dtype=str, encoding=enc)
    df.columns = [c.strip() for c in df.columns]
    code_col = df.columns[0]  # 證券代碼（第一欄，表頭可能有亂碼前綴）
    df["code"] = df[code_col].str.strip().str.split().str[0]
    df["date"] = pd.to_datetime(df["年月日"].str.strip(), format="%Y%m%d")
    for c in df.columns:
        if c not in (code_col, "code", "date", "年月日", "漲跌停"):
            df[c] = pd.to_numeric(df[c].str.strip().replace(".", None), errors="coerce")
    return df


def pick(df, code, mapping, scale=1.0):
    sub = df[df["code"] == code].set_index("date").sort_index()
    out = pd.DataFrame({new: sub[old] * scale for old, new in mapping.items()})
    return out[~out.index.duplicated()]


def merge_write(name, new: pd.DataFrame):
    """與既有檔案合併（新資料覆蓋同日期），方便日後增量匯入。"""
    path = RAW / f"{name}.csv"
    if path.exists():
        old = pd.read_csv(path, parse_dates=["date"]).set_index("date")
        new = new.combine_first(old)
    new.index.name = "date"
    new.sort_index().to_csv(path, date_format="%Y-%m-%d")
    print(f"  → {name}.csv  {len(new)} 筆  {new.index.min().date()} ~ {new.index.max().date()}")


def breadth_from_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """全市場個股（含下市）還原收盤價 + 漲跌停註記 → 每日廣度。ETF（00 開頭）不計。"""
    df = df[~df["code"].str.startswith("00")]
    px = df.pivot_table(index="date", columns="code", values="收盤價(元)", aggfunc="last").sort_index()
    flag = df.assign(f=df["漲跌停"].str.strip()).pivot_table(index="date", columns="code", values="f", aggfunc="last")
    ret = px.pct_change(fill_method=None)
    n = px.notna().sum(axis=1)
    ma60 = px.rolling(60, min_periods=40).mean()
    hi, lo = px.rolling(250, min_periods=120).max(), px.rolling(250, min_periods=120).min()
    out = pd.DataFrame({
        "n": n, "up": (ret > 0).sum(axis=1), "down": (ret < 0).sum(axis=1),
        "limit_up": (flag == "+").sum(axis=1), "limit_down": (flag == "-").sum(axis=1),
        "above_ma60": ((px > ma60).sum(axis=1) / ma60.notna().sum(axis=1)).round(4),
        "new_high": ((px >= hi) & hi.notna()).sum(axis=1), "new_low": ((px <= lo) & lo.notna()).sum(axis=1),
    })
    out.iloc[0, out.columns.get_indexer(["up", "down"])] = float("nan")
    return out


def import_fx(paths):
    """台北外匯經紀 USD/TWD 年度檔（DATE,OPEN,CLOSE,HIGH,LOW,...）→ external.csv 的 usdtwd 欄。"""
    rows = []
    for p in paths:
        d = pd.read_csv(p, usecols=[0, 2], dtype=str, header=0, names=["date", "usdtwd"])
        d["date"] = pd.to_datetime(d["date"].str.strip(), errors="coerce")
        d["usdtwd"] = pd.to_numeric(d["usdtwd"].str.replace(",", "").str.strip(), errors="coerce")
        rows.append(d.dropna())
    fx = pd.concat(rows)
    fx = fx[fx["usdtwd"] > 0].drop_duplicates("date").set_index("date").sort_index()
    print("USD/TWD：")
    merge_write("external", fx)


def handle(path: Path):
    df = read_tej(path)
    cols = set(df.columns)
    print(f"{path.name}：")
    if "漲跌停" in cols:
        merge_write("breadth", breadth_from_stocks(df))
    elif "收盤價(元)" in cols:  # 指數與台積電日 OHLC
        ohlc = {"開盤價(元)": "open", "最高價(元)": "high", "最低價(元)": "low", "收盤價(元)": "close"}
        for code, name in ((TWSE, "taiex"), (TPEX, "tpex")):
            if (df["code"] == code).any():
                o = pick(df, code, ohlc)
                o["turnover"] = pick(df, code, {"成交值(千元)": "t"}, 1e-5)["t"]  # 千元→億
                merge_write(name, o)
        if (df["code"] == TR).any():
            merge_write("taiex_tr", pick(df, TR, {"收盤價(元)": "close"}))
        if (df["code"] == TSMC).any():
            t = pick(df, TSMC, {"收盤價(元)": "close"})
            merge_write("tsmc", t)  # 注意：TEJ 預設為還原股價
    elif "融資餘額(千元)" in cols:
        m = pick(df, TWSE, {"融資餘額(千元)": "twse_margin", "融券餘額(千元)": "twse_short"}, 1e-5)
        m = m.join(pick(df, TPEX, {"融資餘額(千元)": "tpex_margin", "融券餘額(千元)": "tpex_short"}, 1e-5))
        merge_write("margin", m)
    elif "市值(百萬元)" in cols:
        codes = set(df["code"])
        if TWSE in codes:
            m = pick(df, TWSE, {"市值(百萬元)": "twse_mktcap"}, 0.01)
            m = m.join(pick(df, TPEX, {"市值(百萬元)": "tpex_mktcap"}, 0.01))
            merge_write("mktcap", m)
        if TSMC in codes:  # 台積電市值（用來算權重）
            merge_write("tsmc_mktcap", pick(df, TSMC, {"市值(百萬元)": "mktcap"}, 0.01))
    elif "外資買賣超市值(百萬)" in cols:
        merge_write("inst", pick(df, TWSE, {"外資買賣超市值(百萬)": "foreign_net", "投信買賣超市值(百萬)": "trust_net"}, 0.01))
    elif "多空未平倉口數淨額" in cols:
        merge_write("futures", pick(df, df["code"].iloc[0], {"多空未平倉口數淨額": "foreign_tx_net_oi"}))
    else:
        print(f"  ！無法辨識欄位：{sorted(cols)}")


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    fx = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        for f in sorted(list(p.glob("*.txt")) + list(p.glob("*nt.csv"))) if p.is_dir() else [p]:
            fx.append(f) if f.suffix == ".csv" else handle(f)
    if fx:
        import_fx(fx)
