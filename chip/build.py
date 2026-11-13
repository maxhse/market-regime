"""把 data/daily/*.json 彙整成網站讀的 docs/data/dashboard.json。"""
from __future__ import annotations

from collections import defaultdict

from common import (daily_path, existing_days, load_json, now_tpe, OUT_DIR,
                    save_json, STATUS_PATH)

WINDOWS = [1, 3, 5, 10, 20]
CHART_DAYS = 20
TABLE_DAYS = 20
HISTORY_DAYS = 60
TOP_N = 20


def _series(days, path, scale=1.0):
    """取出某個欄位的時間序列，缺值的日子直接略過。"""
    out = []
    for d in days:
        rec = load_json(daily_path(d), {}) or {}
        node = rec
        for k in path:
            node = (node or {}).get(k) if isinstance(node, dict) else None
        if isinstance(node, (int, float)):
            out.append({"d": d, "v": node * scale})
    return out


def _last(seq):
    return seq[-1]["v"] if seq else None


def _window_sum(seq, n):
    return sum(p["v"] for p in seq[-n:]) if seq else None


def _window_delta(seq, n):
    """餘額類：最新值 減 n 個交易日前的值。"""
    if len(seq) < 2:
        return None
    ref = seq[-(n + 1)] if len(seq) > n else seq[0]
    return seq[-1]["v"] - ref["v"]


def build_rankings(days):
    """1/3/5/10/20 日個股買賣超金額排行（上市+上櫃合併）。"""
    flow_days = [d for d in days if (load_json(daily_path(d), {}) or {}).get("flows")]
    flow_days = flow_days[-max(WINDOWS):]

    cache = {d: (load_json(daily_path(d), {}) or {}).get("flows", []) for d in flow_days}
    out = {}
    for w in WINDOWS:
        used = flow_days[-w:]
        agg = defaultdict(lambda: {"n": "", "m": "", "f": 0, "t": 0})
        for d in used:
            for row in cache.get(d, []):
                a = agg[row["c"]]
                a["n"] = row.get("n") or a["n"]
                a["m"] = row.get("m") or a["m"]
                a["f"] += row.get("f") or 0
                a["t"] += row.get("t") or 0
        items = [{"c": c, **v} for c, v in agg.items()]

        block = {"days_used": len(used), "complete": len(used) >= w,
                 "from": used[0] if used else None,
                 "to": used[-1] if used else None}
        for who in ("f", "t"):
            ranked = sorted(items, key=lambda r: r[who], reverse=True)
            block[who] = {
                "buy": [r for r in ranked[:TOP_N] if r[who] > 0],
                "sell": [r for r in ranked[::-1][:TOP_N] if r[who] < 0],
            }
        out[str(w)] = block
    return out


def main() -> None:
    days = existing_days()[-HISTORY_DAYS:]
    if not days:
        raise SystemExit("data/daily 尚無資料，請先執行 update.py 或 backfill.py")

    ser = {
        "twse_foreign": _series(days, ["twse_insti", "foreign"]),
        "twse_trust":   _series(days, ["twse_insti", "trust"]),
        "twse_dealer":  _series(days, ["twse_insti", "dealer"]),
        "tpex_foreign": _series(days, ["tpex_insti", "foreign"]),
        "tpex_trust":   _series(days, ["tpex_insti", "trust"]),
        "tpex_dealer":  _series(days, ["tpex_insti", "dealer"]),
        "twse_margin":  _series(days, ["twse_margin", "balance"]),
        "tpex_margin":  _series(days, ["tpex_margin", "balance"]),
        "tx_foreign_oi": _series(days, ["taifex_tx", "net_oi_lots"]),
    }

    summary = {
        "twse_foreign": _last(ser["twse_foreign"]),
        "twse_trust": _last(ser["twse_trust"]),
        "twse_margin_change": _window_delta(ser["twse_margin"], 1),
        "tpex_margin_change": _window_delta(ser["tpex_margin"], 1),
        "tx_foreign_oi": _last(ser["tx_foreign_oi"]),
        "tx_foreign_oi_change": _window_delta(ser["tx_foreign_oi"], 1),
        "dates": {k: (v[-1]["d"] if v else None) for k, v in ser.items()},
    }

    windows = {}
    for w in WINDOWS:
        windows[str(w)] = {
            "twse_foreign": _window_sum(ser["twse_foreign"], w),
            "twse_trust": _window_sum(ser["twse_trust"], w),
            "tpex_foreign": _window_sum(ser["tpex_foreign"], w),
            "tpex_trust": _window_sum(ser["tpex_trust"], w),
            "twse_margin": _window_delta(ser["twse_margin"], w),
            "tpex_margin": _window_delta(ser["tpex_margin"], w),
            "tx_foreign_oi": _window_delta(ser["tx_foreign_oi"], w),
        }

    table = []
    for d in days[-TABLE_DAYS:]:
        rec = load_json(daily_path(d), {}) or {}
        a, b = rec.get("twse_insti") or {}, rec.get("tpex_insti") or {}
        tw_margin, tp_margin = rec.get("twse_margin") or {}, rec.get("tpex_margin") or {}
        table.append({
            "d": d,
            "tw_f": a.get("foreign"), "tw_t": a.get("trust"), "tw_d": a.get("dealer"),
            "tp_f": b.get("foreign"), "tp_t": b.get("trust"), "tp_d": b.get("dealer"),
            "tw_m": tw_margin.get("balance"), "tw_mc": tw_margin.get("change"),
            "tp_m": tp_margin.get("balance"), "tp_mc": tp_margin.get("change"),
            "oi": (rec.get("taifex_tx") or {}).get("net_oi_lots"),
        })

    save_json(OUT_DIR / "dashboard.json", {
        "built_at": now_tpe().isoformat(timespec="seconds"),
        "status": load_json(STATUS_PATH, {}) or {},
        "summary": summary,
        "windows": windows,
        "series": {k: v[-CHART_DAYS:] for k, v in ser.items()},
        "table": list(reversed(table)),
        "rankings": build_rankings(days),
    })
    print(f"已產出 dashboard.json（{len(days)} 個交易日）")


if __name__ == "__main__":
    main()
