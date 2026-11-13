"""抓取各資料源，寫進 data/daily/<date>.json，並更新 data/status.json。

每個資料源獨立處理：某一項掛掉不會影響其他項，也不會覆蓋掉先前抓成功的數字。
狀態檔記錄每個來源「上次成功更新時間」與最後一次的錯誤訊息。
"""
from __future__ import annotations

import sys
import traceback

import sources as S
from common import (candidate_dates, daily_path, iso, load_json, now_tpe,
                    save_json, session, STATUS_PATH)

# key, 顯示名稱, 抓取函式, 寫進 daily 檔的哪個欄位
SOURCES = [
    ("twse_insti",  "證交所三大法人",   S.twse_institutional, "twse_insti"),
    ("tpex_insti",  "櫃買三大法人",     S.tpex_institutional, "tpex_insti"),
    ("twse_margin", "上市融資餘額",     S.twse_margin,        "twse_margin"),
    ("tpex_margin", "上櫃融資餘額",     S.tpex_margin,        "tpex_margin"),
    ("taifex_tx",   "台指期外資未平倉", S.taifex_tx_foreign,  "taifex_tx"),
    ("stock_flows", "個股買賣超排行",   S.stock_flows,        "flows"),
]


def run_source(s, key, label, fn, field, back: int, status: dict) -> bool:
    """從最近的交易日往回試，第一個成功的就採用。"""
    errors = []
    for d in candidate_dates(back):
        try:
            payload = fn(s, d)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{iso(d)}: {e}")
            continue

        key_date = iso(d)
        rec = load_json(daily_path(key_date), {}) or {}
        rec["date"] = key_date
        rec[field] = payload
        save_json(daily_path(key_date), rec)

        status[key] = {
            "label": label,
            "ok": True,
            "data_date": key_date,
            "updated_at": now_tpe().isoformat(timespec="seconds"),
            "error": None,
        }
        print(f"  OK   {label} <- {key_date}")
        return True

    prev = status.get(key, {})
    status[key] = {
        "label": label,
        "ok": False,
        "data_date": prev.get("data_date"),
        "updated_at": prev.get("updated_at"),
        "error": errors[0] if errors else "unknown",
        "failed_at": now_tpe().isoformat(timespec="seconds"),
    }
    print(f"  FAIL {label}: {errors[:2]}")
    return False


def main() -> int:
    back = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    s = session()
    status = load_json(STATUS_PATH, {}) or {}

    print(f"[{now_tpe().isoformat(timespec='seconds')}] 開始更新")
    ok = 0
    for key, label, fn, field in SOURCES:
        try:
            ok += bool(run_source(s, key, label, fn, field, back, status))
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    status["_checked_at"] = now_tpe().isoformat(timespec="seconds")
    save_json(STATUS_PATH, status)
    print(f"完成：{ok}/{len(SOURCES)} 個來源成功")
    # 全掛才視為失敗，個別來源失效不讓整個 workflow 變紅
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
